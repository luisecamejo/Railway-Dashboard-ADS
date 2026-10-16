"""sentinel_suite_auth — valida la identidad de Sentinel Suite en herramientas FastAPI / Starlette.

COPIA VENDORIZADA de packages/suite-auth-fastapi/sentinel_suite_auth.py del repositorio sentinel-suite.
No se edita aquí: los cambios se hacen en la suite y se vuelve a copiar.

Dependencias: PyJWT[crypto] (pip install "PyJWT[crypto]>=2.8"). FastAPI/Starlette ya traen anyio.

Uso:

    from fastapi import Depends, FastAPI
    from sentinel_suite_auth import SuiteAuth, SuiteUser

    suite = SuiteAuth(
        hub_url="https://suite.sentinelmarketing.net",
        tool="transcriptor-pro",
        on_user=upsert_local_user,          # opcional: crea/sincroniza el usuario local la primera vez
        public_paths=["/healthz", "/static/"],
    )
    app = FastAPI()
    suite.install(app)                       # middleware: redirige al hub si falta la sesión o el acceso

    @app.get("/api/yo")
    def yo(user: SuiteUser = Depends(suite.user)):
        return {"email": user.email, "rol": user.role}

    @app.post("/api/ajustes")
    def ajustes(user: SuiteUser = Depends(suite.require_role("admin"))):
        ...
"""
from __future__ import annotations

import hashlib
import hmac
import inspect
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Optional, Union
from urllib.parse import quote

import anyio
import jwt
from jwt import PyJWKClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

SSO_COOKIE = "sentinel_sso"
AUDIENCE = "sentinel-suite"
LEEWAY_SEC = 30
_ATTEMPT_COOKIE = "suite_sso_attempt"


@dataclass
class SuiteUser:
    id: str                      # id del usuario en el hub (estable): guárdalo en tu tabla local
    email: str
    name: Optional[str]
    role: str                    # rol en ESTA herramienta
    is_suite_admin: bool
    claims: dict = field(repr=False, default_factory=dict)
    agency: str = "equipo"                        # rol en la agencia: admin | equipo
    subaccount: Optional[str] = None             # subcuenta activa en esta petición (slug)
    subaccounts: Union[str, list] = field(default_factory=list)  # "*" = todas, o lista de slugs
    via: str = "cookie"                           # gateway | bearer | cookie
    scopes: Optional[list] = None                 # scopes del access token OAuth (None si es un SSO)
    client_id: Optional[str] = None
    prefix: str = ""                              # ruta pública bajo la suite (X-Forwarded-Prefix), p. ej. "/reportes"

    def can_access(self, slug: str) -> bool:
        return can_access_subaccount(self.claims, slug)

    @property
    def read_only(self) -> bool:
        """True con un access token OAuth que no incluye <tool>:write."""
        return self.scopes is not None and not any(s.endswith(":write") for s in self.scopes)


def verify_gateway_signature(headers, secret: Optional[str]) -> str:
    """Cabeceras firmadas del gateway de la suite: "ok" (persona identificada, JWT en Authorization), "anon" (firma
    anónima válida: una ruta pública que el gateway sirve sin persona), "none" (sin firma) o "bad" (firma inválida o
    caducada: nunca se confía en ella).
    Fórmula: HMAC-SHA256(secreto, f"{ts}\\n{user}\\n{role}\\n{subs}\\n{jwt}"), ts con ±5 min de margen. La anónima lleva
    todo vacío menos ts: dice que la petición viene de la suite (y no del dominio antiguo), no quién la hace."""
    sig = headers.get("x-suite-signature")
    if not sig:
        return "none"
    if not secret:
        return "bad"
    ts = headers.get("x-suite-timestamp", "")
    token = re.sub(r"^Bearer\s+", "", headers.get("authorization", ""), flags=re.I)
    anon = not token and not headers.get("x-suite-user")
    if not ts.isdigit() or abs(time.time() - int(ts)) > 300 or (not token and not anon):
        return "bad"
    msg = "\n".join([ts, headers.get("x-suite-user", ""), headers.get("x-suite-role", ""), headers.get("x-suite-subaccounts", ""), token])
    expected = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return "bad"
    return "anon" if anon else "ok"


_SUB_RE = re.compile(r"^/s/([a-z0-9][a-z0-9-]{1,49})(?:/|$)")


def can_access_subaccount(claims: dict, slug: str) -> bool:
    """claims["subs"] es "*" (todas) o una lista de slugs."""
    subs = claims.get("subs", [])
    return subs == "*" or (isinstance(subs, list) and slug in subs)


def default_subaccount_from(request: Request) -> Optional[str]:
    """Por defecto la subcuenta va en la ruta: /s/<slug>/… (la convención de la barra de la suite)."""
    m = _SUB_RE.match(request.url.path)
    return m.group(1) if m else None


OnUser = Callable[[SuiteUser, Request], Union[Any, Awaitable[Any]]]


class SuiteAuth:
    def __init__(self, hub_url: str, tool: str, on_user: Optional[OnUser] = None,
                 public_paths: Iterable[str] = (), api_prefixes: Iterable[str] = ("/api/",),
                 subaccount_from: Callable[[Request], Optional[str]] = None,
                 gateway_secret: Optional[str] = None, gateway_only: Optional[bool] = None):
        """gateway_secret: secreto compartido con el gateway de la suite (por defecto SUITE_GATEWAY_SECRET).
        gateway_only: solo tráfico firmado por el gateway (por defecto GATEWAY_ONLY=1). Con FastAPI bajo una ruta de la
        suite, arranca la app con root_path="/<ruta>" (o lee X-Forwarded-Prefix) para que las URL que genera lleven el prefijo."""
        self.subaccount_from = subaccount_from or default_subaccount_from
        self.hub = hub_url.rstrip("/")
        self.tool = tool
        self.gateway_secret = gateway_secret if gateway_secret is not None else os.environ.get("SUITE_GATEWAY_SECRET", "")
        self.gateway_only = gateway_only if gateway_only is not None else os.environ.get("GATEWAY_ONLY", "").lower() in ("1", "true", "yes")
        self.on_user = on_user
        self.public_paths = tuple(public_paths)
        self.api_prefixes = tuple(api_prefixes)
        self._jwks = PyJWKClient(f"{self.hub}/.well-known/jwks.json", cache_keys=True, lifespan=3600, timeout=8)
        self._provisioned: dict[str, tuple[float, Any]] = {}  # jti → (exp, usuario local): on_user una vez por token

    # ── verificación ──
    def verify(self, token: Optional[str]) -> Optional[dict]:
        """Devuelve los claims si el token es válido (firma EdDSA, emisor, audiencia, caducidad); si no, None."""
        if not token:
            return None
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            return jwt.decode(token, key, algorithms=["EdDSA"], audience=AUDIENCE, issuer=self.hub, leeway=LEEWAY_SEC,
                              options={"require": ["exp", "iat", "sub", "iss", "aud"]})
        except Exception:  # firma, caducidad, kid desconocido, hub caído…
            return None

    def _is_public(self, path: str) -> bool:
        return any(path == p or (p.endswith("/") and path.startswith(p)) or path.startswith(p.rstrip("/") + "/") for p in self.public_paths)

    def _is_api(self, request: Request) -> bool:
        accept = request.headers.get("accept", "")
        path = request.url.path
        return (request.method not in ("GET", "HEAD") or path.startswith(self.api_prefixes)
                or any(p in path for p in self.api_prefixes)  # también /s/<slug>/api/…
                or ("application/json" in accept and "text/html" not in accept))

    def _full_url(self, request: Request) -> str:
        prefix = request.headers.get("x-forwarded-prefix", "").rstrip("/")
        qs = f"?{request.url.query}" if request.url.query else ""
        if prefix:  # por el gateway la URL pública es la de la suite (X-Forwarded-Host la pisa el borde de Railway)
            return f"{self.hub}{prefix}{request.url.path}{qs}"
        proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
        host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
        return f"{proto}://{host}{request.url.path}{qs}"

    async def _provision(self, user: SuiteUser, request: Request) -> Any:
        if not self.on_user:
            return None
        jti = user.claims.get("jti") or f"{user.id}:{user.claims.get('iat')}"
        now = time.time()
        for k in [k for k, (exp, _) in self._provisioned.items() if exp < now]:
            self._provisioned.pop(k, None)
        if jti in self._provisioned:
            return self._provisioned[jti][1]
        result = self.on_user(user, request)
        if inspect.isawaitable(result):
            result = await result
        self._provisioned[jti] = (float(user.claims.get("exp", now + 900)), result)
        return result

    # ── middleware ──
    async def dispatch(self, request: Request, call_next) -> Response:
        if self._is_public(request.url.path):
            return await call_next(request)
        login = f"{self.hub}/sso/refresh?return={quote(self._full_url(request), safe='')}"
        # 1. Gateway de la suite (firma válida → JWT en Authorization) · 2. Bearer directo · 3. cookie (transición)
        gw = verify_gateway_signature(request.headers, self.gateway_secret)
        if gw == "bad":
            return JSONResponse({"error": "Firma del gateway de Sentinel Suite no válida. Revisa SUITE_GATEWAY_SECRET."}, status_code=401)
        request.state.suite_gateway = gw in ("ok", "anon")  # viene de la suite (con persona o por una ruta pública)
        bearer = re.sub(r"^Bearer\s+", "", request.headers.get("authorization", ""), flags=re.I) or None
        if gw == "ok":
            claims = await anyio.to_thread.run_sync(self.verify, bearer)
            via = "gateway"
        elif self.gateway_only:
            # "anon": la suite la trató como ruta pública pero aquí no lo es → sin identidad, como cualquier otra
            back = f"{self.hub}/ir/{quote(self.tool, safe='')}"
            if self._is_api(request):
                return JSONResponse({"error": "Esta herramienta solo se usa desde Sentinel Suite.", "suite": back}, status_code=401)
            qs = f"?{request.url.query}" if request.url.query else ""
            return RedirectResponse(f"{back}?ruta={quote(request.url.path + qs, safe='')}", status_code=302)
        else:
            claims = await anyio.to_thread.run_sync(self.verify, bearer) if bearer else None
            via = "bearer"
            if not claims:
                claims = await anyio.to_thread.run_sync(self.verify, request.cookies.get(SSO_COOKIE))
                via = "cookie"
        if not claims:
            if self._is_api(request):
                return JSONResponse({"error": "Sesión caducada. Vuelve a entrar.", "login": login}, status_code=401)
            if request.cookies.get(_ATTEMPT_COOKIE):  # la cookie no llega a esta herramienta: no entrar en bucle
                r = HTMLResponse(f"<h1>No se pudo iniciar sesión</h1><p>La cookie de Sentinel Suite no llega a esta herramienta. "
                                 f"Revisa el subdominio y COOKIE_DOMAIN en el hub.</p><p><a href=\"{login}\">Reintentar</a></p>", status_code=401)
                r.delete_cookie(_ATTEMPT_COOKIE, path="/")
                return r
            r = RedirectResponse(login, status_code=302)
            r.set_cookie(_ATTEMPT_COOKIE, "1", max_age=20, path="/", httponly=True, samesite="lax")
            return r
        role = (claims.get("tools") or {}).get(self.tool)
        if not role:
            recheck = f"{login}&tool={quote(self.tool, safe='')}"
            if self._is_api(request):
                return JSONResponse({"error": "No tienes acceso a esta herramienta.", "suite": f"{self.hub}/", "login": recheck}, status_code=403)
            # El token puede ser anterior a que le dieran acceso: el hub lo renueva y, si sigue sin acceso, muestra el aviso
            return RedirectResponse(recheck, status_code=302)
        # Subcuenta activa (la de la URL, /s/<slug>/… por defecto): tiene que estar entre las de la persona
        subaccount = self.subaccount_from(request)
        if subaccount and not can_access_subaccount(claims, subaccount):
            recheck = f"{login}&sub={quote(subaccount, safe='')}"
            if self._is_api(request):
                return JSONResponse({"error": "No tienes acceso a esta subcuenta.", "suite": f"{self.hub}/", "login": recheck}, status_code=403)
            return RedirectResponse(recheck, status_code=302)
        user = SuiteUser(id=str(claims["sub"]), email=claims.get("email", ""), name=claims.get("name"), role=role,
                         is_suite_admin=bool(claims.get("adm")), claims=claims,
                         agency=claims.get("agency") or ("admin" if claims.get("adm") else "equipo"),
                         subaccount=subaccount, subaccounts=claims.get("subs", []), via=via,
                         scopes=claims["scope"].split(" ") if isinstance(claims.get("scope"), str) else None,
                         client_id=claims.get("client_id"), prefix=request.headers.get("x-forwarded-prefix", "").rstrip("/"))
        request.state.suite_user = user
        request.state.local_user = await self._provision(user, request)
        response = await call_next(request)
        if request.cookies.get(_ATTEMPT_COOKIE):
            response.delete_cookie(_ATTEMPT_COOKIE, path="/")
        return response

    def install(self, app) -> None:
        app.add_middleware(BaseHTTPMiddleware, dispatch=self.dispatch)

    # ── dependencias de FastAPI ──
    def user(self, request: Request) -> SuiteUser:
        user = getattr(request.state, "suite_user", None)
        if user is None:  # ruta pública o middleware no instalado
            from fastapi import HTTPException
            raise HTTPException(status_code=401, detail="Sin sesión de Sentinel Suite.")
        return user

    def require_role(self, *roles: str):
        def dep(request: Request) -> SuiteUser:
            user = self.user(request)
            if user.role not in roles:
                from fastapi import HTTPException
                raise HTTPException(status_code=403, detail="Tu rol no permite hacer esto.")
            return user
        return dep

    def logout_url(self) -> str:
        return f"{self.hub}/salir"
