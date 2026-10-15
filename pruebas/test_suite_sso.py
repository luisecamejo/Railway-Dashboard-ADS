"""
Acceso del equipo por Sentinel Suite.

    python pruebas/test_suite_sso.py

Levanta un JWKS de mentira con una clave Ed25519 de prueba, firma cookies como lo hace la
suite y comprueba: el panel sin token, los permisos por cliente (subcuenta), los
dashboards internos /s/{slug}/, que el X-Admin-Token y los enlaces /r/… siguen igual, y
que una cookie falsa o de otra web no sirve.
"""
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

TOKEN = "token-de-prueba-solo-para-los-tests"
os.environ["ADMIN_TOKEN"] = TOKEN
os.environ["RUTA_DATOS_LOCAL"] = tempfile.mkdtemp(prefix="reportes-suite-")
os.environ["SUITE_URL"] = SUITE
os.environ.pop("DATABASE_URL", None)

import logging  # noqa: E402
for n in ("httpx", "reportes", "reportes.seguridad"):
    logging.getLogger(n).setLevel(logging.ERROR)

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app, almacen  # noqa: E402

C = TestClient(app, base_url="https://reportes.test")
ORIGEN = "https://reportes.test"
ADMIN = {"X-Admin-Token": TOKEN}
bien = []


def ok(nombre, cond, extra=""):
    bien.append(bool(cond))
    print(("  ✓ " if cond else "  ✗ ") + nombre + (f"  ({extra})" if extra and not cond else ""))


def cookie(tools=None, subs="*", clave=CLAVE, iss=None, exp=900):
    ahora = int(time.time())
    claims = {"iss": iss or SUITE, "aud": "sentinel-suite", "sub": "7", "iat": ahora, "exp": ahora + exp,
              "jti": str(uuid.uuid4()), "email": "ana@sentinelmarketing.net", "name": "Ana",
              "tools": tools if tools is not None else {"reportes": "equipo"}, "subs": subs,
              "agency": "equipo", "adm": False}
    return {"sentinel_sso": jwt.encode(claims, clave, algorithm="EdDSA", headers={"kid": "k1"})}


def con(ck, **kw):
    C.cookies.clear()
    for k, v in ck.items():
        C.cookies.set(k, v)
    return kw


# ── datos de partida: dos clientes, uno con snapshot y enlace ──────────────────
almacen.guardar_cliente("cliff", "Aesthetics by Cliff", "4sSVbJbkgof1niHlBZyY", "America/Denver", None)
almacen.guardar_cliente("garage", "Garage Door", None, "America/New_York", None)
snap = {"desde": "2026-06-01", "hasta": "2026-10-01", "generado": "2026-10-01T08:00Z",
        "cliente": {"nombre": "Aesthetics by Cliff", "tz": "America/Denver"},
        "leads": [{"nombre": "Zoe Smith", "telefono": "+13035556024"}]}
r = C.post("/admin/snapshots/cliff", headers=ADMIN, json=snap)
enl = C.post("/admin/enlaces", headers=ADMIN, json={"cliente": "cliff", "modo": "cliente"}).json()
print("preparación:", r.status_code, "enlace", bool(enl.get("token")))

print("\nPanel")
C.cookies.clear()
r = C.get("/admin", follow_redirects=False)
ok("sin sesión de la suite, /admin manda a la suite (con tool=reportes)",
   r.status_code == 302 and r.headers["location"].startswith(f"{SUITE}/sso/refresh?return=") and "tool=reportes" in r.headers["location"])
r = C.get("/admin", **con(cookie()))
ok("con sesión, el panel se sirve sin pedir token", r.status_code == 200 and "window.SUITE=" in r.text)
ok("y trae la barra de la suite", f"{SUITE}/suite-bar.js" in r.text)
r = C.get("/admin", follow_redirects=False, **con(cookie(tools={"funnel-hacker": "admin"})))
ok("sin la herramienta reportes, de vuelta a la suite", r.status_code == 302)
r = C.get("/", follow_redirects=False, **con(cookie()))
ok("la raíz lleva al panel a quien viene de la suite", r.status_code == 302 and r.headers["location"] == "/admin")

print("\nAPI del panel con la cookie")
r = C.get("/admin/estado", **con(cookie()))
ok("/admin/estado con la cookie", r.status_code == 200 and {c["slug"] for c in r.json()["clientes"]} == {"cliff", "garage"})
ok("y dice quién es", (r.json().get("persona") or {}).get("email") == "ana@sentinelmarketing.net")
r = C.get("/admin/estado", **con(cookie(subs=["cliff"])))
ok("solo ve los clientes de sus subcuentas", [c["slug"] for c in r.json()["clientes"]] == ["cliff"])
ok("y no los datos de otro cliente", C.get("/admin/config/garage", **con(cookie(subs=["cliff"]))).status_code == 403)
ok("ni sus enlaces por ?cliente=", C.get("/admin/enlaces?cliente=garage", **con(cookie(subs=["cliff"]))).status_code == 403)
lista = C.get("/admin/enlaces", **con(cookie(subs=["garage"]))).json()["enlaces"]
ok("la lista de enlaces se filtra por sus subcuentas", lista == [])
ok("ni tocar un enlace de otro cliente por su token",
   C.post(f"/admin/enlaces/{enl['token']}/dominios", headers={"Origin": ORIGEN}, json={"dominios": []}, **con(cookie(subs=["garage"]))).status_code == 403)
ok("dar de alta un cliente nuevo exige llegar a todas las subcuentas",
   C.post("/admin/clientes", headers={"Origin": ORIGEN}, json={"slug": "nuevo", "nombre": "Nuevo", "tz": "Europe/Madrid"}, **con(cookie(subs=["cliff"]))).status_code == 403)
r = C.post("/admin/clientes", headers={"Origin": ORIGEN}, json={"slug": "nuevo", "nombre": "Nuevo", "tz": "Europe/Madrid"}, **con(cookie()))
ok("con todas, sí", r.status_code == 200, r.text[:200])
ok("una escritura desde otro subdominio se rechaza",
   C.post("/admin/clientes", headers={"Origin": "https://funnel.sentinelmarketing.net"}, json={"slug": "x", "nombre": "X"}, **con(cookie())).status_code == 403)
ok("sin Origin hace falta la cabecera del panel",
   C.post("/admin/clientes", json={"slug": "x2", "nombre": "X2", "tz": "Europe/Madrid"}, **con(cookie())).status_code == 403
   and C.post("/admin/clientes", headers={"X-Requested-With": "panel-reportes"}, json={"slug": "x2", "nombre": "X2", "tz": "Europe/Madrid"}, **con(cookie())).status_code == 200)
ok("borrar un cliente es solo para el rol admin",
   C.delete("/admin/clientes/nuevo?confirmar=nuevo", headers={"Origin": ORIGEN}, **con(cookie())).status_code == 403)
ok("con rol admin sí se puede",
   C.delete("/admin/clientes/nuevo?confirmar=nuevo", headers={"Origin": ORIGEN}, **con(cookie(tools={"reportes": "admin"}))).status_code == 200)

print("\nCookies que no valen")
ok("firmada con otra clave", C.get("/admin/estado", **con(cookie(clave=OTRA))).status_code == 401)
ok("caducada", C.get("/admin/estado", **con(cookie(exp=-120))).status_code == 401)
ok("de otro emisor", C.get("/admin/estado", **con(cookie(iss="https://evil.example"))).status_code == 401)
r = C.get("/admin/estado", **con(cookie(exp=-120)))
ok("y el 401 dice adónde ir a renovarla", r.headers.get("x-suite-login", "").startswith(f"{SUITE}/sso/refresh"))
ok("manipulada (rol cambiado a mano)",
   C.get("/admin/estado", **con({"sentinel_sso": (lambda t: t[0] + "." + jwt.utils.base64url_encode(json.dumps({**json.loads(jwt.utils.base64url_decode(t[1])), "tools": {"reportes": "admin"}}).encode()).decode() + "." + t[2])(cookie(tools={})["sentinel_sso"].split("."))})).status_code == 401)

print("\nDashboards del equipo (/s/{slug}/)")
C.cookies.clear()
r = C.get("/s/cliff/", follow_redirects=False)
ok("sin sesión, a la suite", r.status_code == 302 and r.headers["location"].startswith(f"{SUITE}/sso/refresh"))
r = C.get("/s/garage/", follow_redirects=False, **con(cookie(subs=["cliff"])))
ok("cliente fuera de sus subcuentas: la suite revalida (&sub=)", r.status_code == 302 and "&sub=garage" in r.headers["location"])
d = C.get("/s/cliff/snapshot.json", **con(cookie(subs=["cliff"])))
ok("snapshot interno sin enmascarar", d.status_code == 200 and d.json()["leads"][0]["nombre"] == "Zoe Smith")
ok("snapshot de otro cliente: 403", C.get("/s/garage/snapshot.json", **con(cookie(subs=["cliff"]))).status_code == 403)
ok("snapshot sin sesión: 401", (C.cookies.clear() or C.get("/s/cliff/snapshot.json")).status_code == 401)
ok("cliente sin datos todavía: 409", C.get("/s/garage/snapshot.json", **con(cookie())).status_code == 409)
ok("estado del botón actualizar", C.get("/s/cliff/refrescar", **con(cookie())).status_code == 200)

print("\nLo que no cambia")
ok("X-Admin-Token sigue sirviendo (extractores y scripts)", C.get("/admin/estado", headers=ADMIN).status_code == 200)
C.cookies.clear()
ok("el enlace del cliente /r/… sigue igual (y enmascara según su modo)",
   C.get(f"/r/{enl['token']}/snapshot.json").status_code == 200)
ok("sin cookie ni token, la API del panel sigue cerrada", C.get("/admin/estado").status_code == 401)

srv.shutdown()
print(f"\n{sum(bien)}/{len(bien)} comprobaciones del acceso por Sentinel Suite")
sys.exit(0 if all(bien) else 1)
