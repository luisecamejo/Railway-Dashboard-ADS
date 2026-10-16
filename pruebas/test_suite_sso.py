"""
Acceso por Sentinel Suite a través de su gateway (reportes bajo suite…/reportes).

    python pruebas/test_suite_sso.py

Hace lo que hace el gateway de la suite: quita el prefijo /reportes, manda X-Forwarded-Prefix
y la identidad firmada (X-Suite-* + Authorization: Bearer <JWT>) con el secreto compartido; o
una firma anónima para las rutas públicas (/r/<token>). Con GATEWAY_ONLY=1, como en
producción: nada entra por otra vía (ni la cookie), y el dominio viejo responde 301 a la suite.

Comprueba: el panel y su API con la identidad firmada, el prefijo en lo que se genera, los
permisos por subcuenta, los dashboards del equipo /s/{slug}/, los enlaces /r/… sin sesión,
las firmas que no valen, el 301 del dominio viejo y que X-Admin-Token (máquinas) sigue igual.
"""
import hashlib
import hmac
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "web"))

import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from jwt.algorithms import OKPAlgorithm  # noqa: E402

CLAVE = Ed25519PrivateKey.generate()
OTRA = Ed25519PrivateKey.generate()
JWK = json.loads(OKPAlgorithm.to_jwk(CLAVE.public_key()))
JWK.update({"kid": "k1", "alg": "EdDSA", "use": "sig"})


class _Jwks(BaseHTTPRequestHandler):
    def do_GET(self):
        cuerpo = json.dumps({"keys": [JWK]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), _Jwks)
threading.Thread(target=srv.serve_forever, daemon=True).start()
SUITE = f"http://127.0.0.1:{srv.server_address[1]}"
SECRETO = "secreto-compartido-de-prueba-1234"
PREFIJO = "/reportes"
HOST_SUITE = "suite.test"            # lo que el navegador ve (X-Forwarded-Host)
ORIGEN = f"https://{HOST_SUITE}"

TOKEN = "token-de-prueba-solo-para-los-tests"
os.environ["ADMIN_TOKEN"] = TOKEN
os.environ["RUTA_DATOS_LOCAL"] = tempfile.mkdtemp(prefix="reportes-suite-")
os.environ["SUITE_URL"] = SUITE
os.environ["SUITE_GATEWAY_SECRET"] = SECRETO
os.environ["GATEWAY_ONLY"] = "1"
os.environ.pop("DATABASE_URL", None)

import logging  # noqa: E402
for n in ("httpx", "reportes", "reportes.seguridad"):
    logging.getLogger(n).setLevel(logging.ERROR)

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app, almacen  # noqa: E402

# El servicio recibe las peticiones en su dominio de Railway (lo que el gateway tiene como upstream)
VIEJO = "https://reportes-production-a40d.up.railway.app"
C = TestClient(app, base_url=VIEJO)
ADMIN = {"X-Admin-Token": TOKEN}
bien = []


def ok(nombre, cond, extra=""):
    bien.append(bool(cond))
    print(("  ✓ " if cond else "  ✗ ") + nombre + (f"  ({extra})" if extra and not cond else ""))


def jwt_de(tools=None, subs="*", clave=CLAVE, iss=None, exp=900):
    ahora = int(time.time())
    claims = {"iss": iss or SUITE, "aud": "sentinel-suite", "sub": "7", "iat": ahora, "exp": ahora + exp,
              "jti": str(uuid.uuid4()), "email": "ana@sentinelmarketing.net", "name": "Ana",
              "tools": tools if tools is not None else {"reportes": "equipo"}, "subs": subs,
              "agency": "equipo", "adm": False}
    return jwt.encode(claims, clave, algorithm="EdDSA", headers={"kid": "k1"})


def _firma(ts, user, role, subs, tok, secreto=SECRETO):
    msg = "\n".join([ts, user, role, subs, tok])
    return hmac.new(secreto.encode(), msg.encode(), hashlib.sha256).hexdigest()


REENVIO = {"X-Forwarded-Prefix": PREFIJO, "X-Forwarded-Host": HOST_SUITE, "X-Forwarded-Proto": "https"}


def gw(tools=None, subs="*", *, tok=None, ts=None, secreto=SECRETO, **extra):
    """Las cabeceras con las que el gateway reenvía la petición de una persona."""
    tok = tok or jwt_de(tools, subs)
    rol = (tools or {"reportes": "equipo"}).get("reportes", "")
    subs_h = "*" if subs == "*" else ",".join(subs)
    ts = ts or str(int(time.time()))
    h = {**REENVIO, "X-Suite-User": "7", "X-Suite-Role": rol, "X-Suite-Subaccounts": subs_h,
         "X-Suite-Timestamp": ts, "X-Suite-Signature": _firma(ts, "7", rol, subs_h, tok, secreto),
         "Authorization": f"Bearer {tok}", "X-Suite-Via": "session"}
    h.update(extra)
    return h


def anon(**extra):
    """La firma anónima con la que el gateway reenvía una ruta pública (sin persona)."""
    ts = str(int(time.time()))
    return {**REENVIO, "X-Suite-Via": "public", "X-Suite-Timestamp": ts,
            "X-Suite-Signature": _firma(ts, "", "", "", ""), **extra}


# ── datos de partida: dos clientes, uno con snapshot y enlace ──────────────────
almacen.guardar_cliente("cliff", "Aesthetics by Cliff", "4sSVbJbkgof1niHlBZyY", "America/Denver", None)
almacen.guardar_cliente("garage", "Garage Door", None, "America/New_York", None)
snap = {"desde": "2026-06-01", "hasta": "2026-10-01", "generado": "2026-10-01T08:00Z",
        "cliente": {"nombre": "Aesthetics by Cliff", "tz": "America/Denver"},
        "leads": [{"nombre": "Zoe Smith", "telefono": "+13035556024"}]}
r = C.post("/admin/snapshots/cliff", headers=ADMIN, json=snap)
enl = C.post("/admin/enlaces", headers=ADMIN, json={"cliente": "cliff", "modo": "cliente"}).json()
TOK = enl.get("token")
print("preparación:", r.status_code, "enlace", bool(TOK))

print("\nPanel por el gateway")
r = C.get("/admin", headers=gw())
ok("con la identidad firmada, el panel se sirve sin pedir token", r.status_code == 200 and "window.SUITE=" in r.text)
ok("y sabe su prefijo (/reportes) para las llamadas y los enlaces", '"prefix": "/reportes"' in r.text)
ok("y trae la barra de la suite", f"{SUITE}/suite-bar.js" in r.text)
r = C.get("/admin", follow_redirects=False, headers=gw(tools={"funnel-hacker": "admin"}))
ok("sin la herramienta reportes, la suite revalida (tool=reportes)",
   r.status_code == 302 and "/sso/refresh?return=" in r.headers["location"] and "tool=reportes" in r.headers["location"])
ok("…y la vuelta lleva el prefijo", f"https%3A%2F%2F{HOST_SUITE}%2Freportes%2Fadmin" in r.headers["location"])
r = C.get("/", follow_redirects=False, headers=gw())
ok("la raíz lleva al panel, con prefijo", r.status_code == 302 and r.headers["location"] == "/reportes/admin")

print("\nAPI del panel")
r = C.get("/admin/estado", headers=gw())
ok("/admin/estado con la identidad firmada", r.status_code == 200 and {c["slug"] for c in r.json()["clientes"]} == {"cliff", "garage"})
ok("y dice quién es", (r.json().get("persona") or {}).get("email") == "ana@sentinelmarketing.net")
r = C.get("/admin/estado", headers=gw(subs=["cliff"]))
ok("solo ve los clientes de sus subcuentas", [c["slug"] for c in r.json()["clientes"]] == ["cliff"])
ok("y no los datos de otro cliente", C.get("/admin/config/garage", headers=gw(subs=["cliff"])).status_code == 403)
ok("ni sus enlaces por ?cliente=", C.get("/admin/enlaces?cliente=garage", headers=gw(subs=["cliff"])).status_code == 403)
ok("la lista de enlaces se filtra por sus subcuentas", C.get("/admin/enlaces", headers=gw(subs=["garage"])).json()["enlaces"] == [])
ok("ni tocar un enlace de otro cliente por su token",
   C.post(f"/admin/enlaces/{TOK}/dominios", headers=gw(subs=["garage"], Origin=ORIGEN), json={"dominios": []}).status_code == 403)
ok("dar de alta un cliente nuevo exige llegar a todas las subcuentas",
   C.post("/admin/clientes", headers=gw(subs=["cliff"], Origin=ORIGEN), json={"slug": "nuevo", "nombre": "Nuevo", "tz": "Europe/Madrid"}).status_code == 403)
r = C.post("/admin/clientes", headers=gw(Origin=ORIGEN), json={"slug": "nuevo", "nombre": "Nuevo", "tz": "Europe/Madrid"})
ok("con todas, sí (el Origin es el de la suite)", r.status_code == 200, r.text[:200])
ok("una escritura desde otro origen se rechaza",
   C.post("/admin/clientes", headers=gw(Origin="https://otra.sentinelmarketing.net"), json={"slug": "x", "nombre": "X"}).status_code == 403)
ok("borrar un cliente es solo para el rol admin",
   C.delete("/admin/clientes/nuevo?confirmar=nuevo", headers=gw(Origin=ORIGEN)).status_code == 403)
ok("con rol admin sí se puede",
   C.delete("/admin/clientes/nuevo?confirmar=nuevo", headers=gw(tools={"reportes": "admin"}, Origin=ORIGEN)).status_code == 200)

print("\nFirmas que no valen")
ok("firmada con otro secreto", C.get("/admin/estado", headers=gw(secreto="otro-secreto-que-no-es-el-bueno")).status_code == 401)
ok("con marca de tiempo vieja (repetición)", C.get("/admin/estado", headers=gw(ts=str(int(time.time()) - 900))).status_code == 401)
ok("firma buena pero JWT de otra clave", C.get("/admin/estado", headers=gw(tok=jwt_de(clave=OTRA))).status_code == 401)
ok("firma buena pero JWT caducado", C.get("/admin/estado", headers=gw(tok=jwt_de(exp=-120))).status_code == 401)
ok("firma buena pero JWT de otro emisor", C.get("/admin/estado", headers=gw(tok=jwt_de(iss="https://evil.example"))).status_code == 401)
t = jwt_de(tools={}).split(".")
manip = t[0] + "." + jwt.utils.base64url_encode(json.dumps({**json.loads(jwt.utils.base64url_decode(t[1])), "tools": {"reportes": "admin"}}).encode()).decode() + "." + t[2]
ok("JWT manipulado (rol cambiado a mano) aunque la firma del gateway lo cubra", C.get("/admin/estado", headers=gw(tok=manip)).status_code == 401)
ok("la cookie de la suite ya no vale por sí sola",
   C.get("/admin/estado", headers=REENVIO, cookies={"sentinel_sso": jwt_de()}, follow_redirects=False).status_code == 301)
r = C.get("/admin/estado", headers=anon(), follow_redirects=False)
ok("una firma anónima no abre nada privado: a entrar por la suite",
   r.status_code == 302 and r.headers["location"].startswith(f"{SUITE}/ir/reportes?ruta="))
ok("…y para una API, 401", C.get("/admin/estado", headers=anon(Accept="application/json")).status_code == 401)

print("\nDashboards del equipo (/s/{slug}/)")
r = C.get("/s/cliff/", headers=gw(subs=["cliff"]))
ok("con la identidad firmada se sirve, con la barra de la suite", r.status_code == 200 and "suite-bar.js" in r.text)
ok("y app.js se pide bajo el prefijo", "'/reportes/app.js?v=" in r.text)
r = C.get("/s/garage/", follow_redirects=False, headers=gw(subs=["cliff"]))
ok("cliente fuera de sus subcuentas: la suite revalida (&sub=)", r.status_code == 302 and "&sub=garage" in r.headers["location"])
r = C.get("/s/cliff", follow_redirects=False, headers=gw())
ok("sin barra final, redirige con prefijo", r.status_code == 308 and r.headers["location"] == "/reportes/s/cliff/")
d = C.get("/s/cliff/snapshot.json", headers=gw(subs=["cliff"]))
ok("snapshot interno sin enmascarar", d.status_code == 200 and d.json()["leads"][0]["nombre"] == "Zoe Smith")
ok("snapshot de otro cliente: 403", C.get("/s/garage/snapshot.json", headers=gw(subs=["cliff"])).status_code == 403)
ok("cliente sin datos todavía: 409", C.get("/s/garage/snapshot.json", headers=gw()).status_code == 409)
ok("estado del botón actualizar", C.get("/s/cliff/refrescar", headers=gw()).status_code == 200)

print("\nEnlaces del cliente (/r/…): públicos, por el gateway sin sesión")
r = C.get(f"/r/{TOK}/", headers=anon())
ok("el visor se sirve con la firma anónima", r.status_code == 200 and "snapshot.json" in r.text)
ok("y carga app.js bajo el prefijo", "'/reportes/app.js?v=" in r.text)
ok("el snapshot, enmascarado según el modo del enlace", C.get(f"/r/{TOK}/snapshot.json", headers=anon()).status_code == 200)
ok("app.js es público", C.get("/app.js", headers=anon()).status_code == 200)
r = C.get(f"/r/{TOK}", follow_redirects=False, headers=anon())
ok("sin barra final, redirige con prefijo", r.status_code == 308 and r.headers["location"] == f"/reportes/r/{TOK}/")
ok("el refresco desde el dashboard del cliente", C.get(f"/r/{TOK}/refrescar", headers=anon()).status_code == 200)
C.post(f"/admin/enlaces/{TOK}/dominios", headers=ADMIN, json={"dominios": ["https://app.gohighlevel.com"]})
r = C.get(f"/r/{TOK}/embed", headers=anon())
ok("el embed para GoHighLevel pide el snapshot con prefijo",
   r.status_code == 200 and f"'/reportes/r/{TOK}/snapshot.json'" in r.text and "frame-ancestors https://app.gohighlevel.com" in r.headers.get("content-security-policy", ""))
ok("un token que no existe sigue dando 404", C.get("/r/noexiste/", headers=anon()).status_code == 404)

print("\nDominio viejo y dominio de Railway: 301 a la suite")
r = C.get("/admin", follow_redirects=False)
ok("/admin sin firma → 301 a suite…/reportes/admin", r.status_code == 301 and r.headers["location"] == f"{SUITE}/reportes/admin")
r = C.get(f"/r/{TOK}/?desde=2026-01-01", follow_redirects=False)
ok("también los enlaces /r/…, con su query", r.status_code == 301 and r.headers["location"] == f"{SUITE}/reportes/r/{TOK}/?desde=2026-01-01")
ok("y /s/<slug>/", C.get("/s/cliff/", follow_redirects=False).status_code == 301)
ok("y app.js", C.get("/app.js", follow_redirects=False).status_code == 301)
ok("y la raíz", C.get("/", follow_redirects=False).headers.get("location") == f"{SUITE}/reportes/")
ok("/salud se contesta en cualquier dominio (healthcheck de Railway)", C.get("/salud").json() == {"ok": True})

print("\nMáquinas (extractores y scripts): X-Admin-Token sin pasar por la suite")
ok("X-Admin-Token sigue sirviendo", C.get("/admin/estado", headers=ADMIN).status_code == 200)
ok("un token malo es 401, no 301", C.get("/admin/estado", headers={"X-Admin-Token": "malo"}, follow_redirects=False).status_code == 401)
ok("sin token ni firma, la API del panel no se abre", C.get("/admin/estado", follow_redirects=False).status_code in (301, 401))

srv.shutdown()
print(f"\n{sum(bien)}/{len(bien)} comprobaciones del acceso por Sentinel Suite (gateway)")
sys.exit(0 if all(bien) else 1)
