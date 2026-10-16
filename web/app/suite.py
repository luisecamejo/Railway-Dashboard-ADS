# -*- coding: utf-8 -*-
"""
Acceso a través de Sentinel Suite: reportes vive en https://suite.sentinelmarketing.net/reportes.

La suite es la puerta única (gateway). La persona entra allí y la suite reenvía cada petición
a este servicio con el prefijo `/reportes` ya quitado, `X-Forwarded-Prefix: /reportes`, y la
identidad firmada en cabeceras (`X-Suite-*`, HMAC con el secreto compartido
`SUITE_GATEWAY_SECRET`) más el JWT en `Authorization: Bearer`. Aquí no hay login propio.

El middleware vendorizado (`web/vendor/sentinel_suite_auth.py`, copia de
`packages/suite-auth-fastapi` de la suite) comprueba la firma, que la persona tiene la
herramienta `reportes` (rol admin o equipo) y que la subcuenta de la URL (`/s/<slug>/`) está
entre las suyas. Lo que la persona puede ver en el panel se filtra con `Persona.filtra`.

Lo que llega SIN persona:

  · Los enlaces con token `/r/<token>/…` que ven los clientes (empotrados en GoHighLevel),
    `app.js`, `/salud` y `robots.txt`: rutas públicas, también declaradas en la suite como
    «rutas públicas» de la herramienta. Por el gateway llegan con una firma anónima.
  · Las máquinas (extractores y scripts) con `X-Admin-Token`: no pasan por la suite, hablan con
    el servicio por su URL de Railway, y las valida `exige_admin` en main.py.

Con `GATEWAY_ONLY=1` (producción) solo vale lo firmado por el gateway. Cualquier otra petición
—al dominio viejo reportes.sentinelmarketing.net o al de Railway— responde **301** a la misma
ruta y query bajo la suite, también los `/r/<token>`. Sin `SUITE_URL` todo esto queda apagado
(pruebas antiguas, desarrollo local); sin `GATEWAY_ONLY` el middleware acepta además un Bearer
directo o la cookie `sentinel_sso` (transición).
"""
from __future__ import annotations

import os
import pathlib
import sys
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

_VENDOR = pathlib.Path(__file__).resolve().parents[1] / "vendor"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from sentinel_suite_auth import SuiteAuth, SuiteUser, verify_gateway_signature  # noqa: E402

SUITE_URL = os.environ.get("SUITE_URL", "").strip().rstrip("/")
HERRAMIENTA = "reportes"
ROLES = ("admin", "equipo")
# Ruta pública de reportes dentro de la suite (la que el gateway manda en X-Forwarded-Prefix).
RUTA_SUITE = "/" + os.environ.get("SUITE_RUTA", "reportes").strip("/")
# Lo que se sirve sin persona. Tiene que coincidir con las «rutas públicas» de la herramienta en la suite.
PUBLICAS = ("/r/", "/app.js", "/salud", "/healthz", "/robots.txt", "/static/")
# Lo que se contesta aunque no venga por la suite ni con token: el healthcheck de Railway.
SIEMPRE_DIRECTO = ("/salud",)

# Lo que pide el propio dashboard con fetch (el snapshot y el botón de actualizar) es API: sin
# acceso recibe 401/403 en JSON, que el cargador del visor sabe pintar; una redirección a la
# suite en medio de un fetch solo daría un error raro.
API = ("/api/", "/snapshot.json", "/refrescar")

_auth: Optional[SuiteAuth] = (SuiteAuth(hub_url=SUITE_URL, tool=HERRAMIENTA, public_paths=PUBLICAS,
                                        api_prefixes=API)
                              if SUITE_URL else None)


def activa() -> bool:
    return _auth is not None


def solo_gateway() -> bool:
    return bool(_auth and _auth.gateway_only)


@dataclass
class Persona:
    email: str
    nombre: Optional[str]
    rol: str                 # rol en reportes: admin | equipo
    subs: object             # "*" = todas las subcuentas, o lista de slugs
    claims: dict
    prefijo: str = ""        # "/reportes" por el gateway; "" en local

    def llega(self, slug: str) -> bool:
        return self.subs == "*" or (isinstance(self.subs, list) and slug in self.subs)

    def filtra(self, slugs):
        """Deja solo los clientes que esta persona puede ver."""
        return list(slugs) if self.subs == "*" else [s for s in slugs if s in (self.subs or [])]


def persona(peticion: Request) -> Optional[Persona]:
    """La persona que el middleware dejó en la petición, si tiene acceso a reportes."""
    u = getattr(peticion.state, "suite_user", None)
    if not isinstance(u, SuiteUser) or u.role not in ROLES:
        return None
    return Persona(email=u.email, nombre=u.name, rol=u.role, subs=u.subaccounts, claims=u.claims,
                   prefijo=u.prefix)


def prefijo(peticion: Request) -> str:
    """Con qué prefijo ve esta petición el navegador: '/reportes' por el gateway, '' en local."""
    return peticion.headers.get("x-forwarded-prefix", "").rstrip("/")


def _proto_host(peticion: Request) -> tuple[str, str]:
    """
    Desde dónde ve la web el navegador. Por el gateway es SIEMPRE la suite: el borde de
    Railway pisa X-Forwarded-Host con el dominio del servicio, así que esa cabecera no vale
    para saberlo; vale el prefijo, que solo pone el gateway.
    """
    if prefijo(peticion) and SUITE_URL:
        proto, _, host = SUITE_URL.partition("://")
        return proto, host
    proto = peticion.headers.get("x-forwarded-proto", peticion.url.scheme).split(",")[0].strip()
    host = peticion.headers.get("x-forwarded-host", peticion.headers.get("host", peticion.url.netloc))
    return proto, host


def url_propia(peticion: Request) -> str:
    """La URL que ve el navegador, con el prefijo de la suite si viene por el gateway."""
    proto, host = _proto_host(peticion)
    qs = f"?{peticion.url.query}" if peticion.url.query else ""
    return f"{proto}://{host}{prefijo(peticion)}{peticion.url.path}{qs}"


def origen_propio(peticion: Request) -> str:
    proto, host = _proto_host(peticion)
    return f"{proto}://{host}"


def url_en_suite(peticion: Request) -> str:
    """La misma ruta y query, pero bajo la suite. Es adonde manda el 301 del dominio viejo."""
    qs = f"?{peticion.url.query}" if peticion.url.query else ""
    return f"{SUITE_URL}{RUTA_SUITE}{peticion.url.path}{qs}"


def login(peticion: Request, sub: Optional[str] = None) -> str:
    """
    Adónde mandar a quien llega sin sesión válida: la suite renueva la sesión (o pide iniciarla)
    y vuelve aquí. Si tras renovar sigue sin acceso, el aviso lo da la suite.
    """
    url = f"{SUITE_URL}/sso/refresh?return={quote(url_propia(peticion), safe='')}&tool={HERRAMIENTA}"
    return url + (f"&sub={quote(sub, safe='')}" if sub else "")


def escritura_segura(peticion: Request) -> bool:
    """
    Para lo que cambia datos se exige que la petición salga de esta misma web (por el gateway, el
    origen es el de la suite). Es una defensa extra contra peticiones lanzadas desde otro sitio
    con la sesión de la persona.
    """
    if peticion.method in ("GET", "HEAD", "OPTIONS"):
        return True
    origen = peticion.headers.get("origin")
    if origen:
        return origen == origen_propio(peticion)
    # Sin Origin (navegadores viejos): al menos la cabecera que solo pone nuestro panel
    return peticion.headers.get("x-requested-with") == "panel-reportes"


async def _dispatch(peticion: Request, call_next):
    """
    Delante del middleware de la suite:

      · Las máquinas (X-Admin-Token) no pasan por la suite: se dejan pasar y las valida exige_admin.
      · Con GATEWAY_ONLY, lo que no viene firmado por el gateway (ni con persona ni anónimo) es
        alguien entrando por el dominio viejo o por el de Railway: 301 a la misma ruta en la suite.
        Solo /salud se contesta igual desde cualquier sitio (el healthcheck de Railway).
      · El resto lo decide el middleware de la suite (rutas públicas, firma, rol, subcuenta).
    """
    if peticion.headers.get("x-admin-token"):
        return await call_next(peticion)
    if _auth.gateway_only and peticion.url.path not in SIEMPRE_DIRECTO:
        if verify_gateway_signature(peticion.headers, _auth.gateway_secret) == "none":
            return RedirectResponse(url_en_suite(peticion), status_code=301)
    return await _auth.dispatch(peticion, call_next)


def instalar(app) -> None:
    """Se llama desde main.py. Sin SUITE_URL no instala nada."""
    if _auth:
        app.add_middleware(BaseHTTPMiddleware, dispatch=_dispatch)
