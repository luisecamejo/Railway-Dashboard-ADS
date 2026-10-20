# -*- coding: utf-8 -*-
"""
La pestaña «Ajustes» del dashboard (20-oct-2026, al retirar el panel /admin).

Existe SOLO para administradores que entran por Sentinel Suite: el servidor la anuncia con
<meta name="roaslytics-ajustes"> en /s/<slug>/ y el dashboard la enseña si lo encuentra.
Aquí se comprueba que:
  1. un admin por la suite recibe el meta (con el prefijo /reportes y el slug);
  2. una persona con rol equipo NO lo recibe;
  3. el enlace del cliente (/r/<token>/ y /embed) NUNCA lo lleva;
  4. las rutas que usa la pestaña (enlace general, menú de GHL, rotar) responden a la
     persona admin por el gateway, y /admin ya no es un panel: redirige a la suite.

Mismo montaje que test_suite_sso.py: un JWKS local hace de hub.

    python pruebas/test_ajustes.py
"""
import base64, hashlib, hmac, json, logging, os, pathlib, sys, tempfile, threading, time, uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "web"))

import jwt  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

CLAVE = Ed25519PrivateKey.generate()
PUB = CLAVE.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
JWK = {"kty": "OKP", "crv": "Ed25519", "kid": "k1", "x": base64.urlsafe_b64encode(PUB).rstrip(b"=").decode()}


class _Jwks(BaseHTTPRequestHandler):
    def do_GET(self):
        cuerpo = json.dumps({"keys": [JWK]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), _Jwks)
threading.Thread(target=srv.serve_forever, daemon=True).start()
SUITE = f"http://127.0.0.1:{srv.server_address[1]}"
SECRETO = "secreto-compartido-de-prueba-1234"
os.environ["ADMIN_TOKEN"] = "token-de-prueba"
os.environ["RUTA_DATOS_LOCAL"] = tempfile.mkdtemp(prefix="ajustes-")
os.environ["SUITE_URL"] = SUITE
os.environ["SUITE_GATEWAY_SECRET"] = SECRETO
os.environ["GATEWAY_ONLY"] = "1"
os.environ["GHL_MENU_AUTO"] = "0"
os.environ.pop("DATABASE_URL", None)
logging.disable(logging.WARNING)

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app, almacen  # noqa: E402

C = TestClient(app, base_url="https://reportes-production-a40d.up.railway.app")
ADMIN = {"X-Admin-Token": "token-de-prueba"}
fallos = []


def ok(cond, etiqueta, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + etiqueta + (f"  ({extra})" if extra and not cond else ""))
    if not cond:
        fallos.append(etiqueta)


def _firma(ts, user, role, subs, tok):
    return hmac.new(SECRETO.encode(), "\n".join([ts, user, role, subs, tok]).encode(), hashlib.sha256).hexdigest()


def jwt_de(rol):
    ahora = int(time.time())
    claims = {"iss": SUITE, "aud": "sentinel-suite", "sub": "7", "iat": ahora, "exp": ahora + 900,
              "jti": str(uuid.uuid4()), "email": "ana@sentinelmarketing.net", "name": "Ana",
              "tools": {"reportes": rol}, "subs": "*", "agency": "equipo", "adm": False}
    return jwt.encode(claims, CLAVE, algorithm="EdDSA", headers={"kid": "k1"})


def gw(rol, metodo="GET"):
    tok, ts = jwt_de(rol), str(int(time.time()))
    h = {"X-Forwarded-Prefix": "/reportes", "X-Forwarded-Proto": "https",
         "X-Forwarded-Host": "reportes-production-a40d.up.railway.app",
         "X-Suite-User": "7", "X-Suite-Role": rol, "X-Suite-Subaccounts": "*",
         "X-Suite-Timestamp": ts, "X-Suite-Signature": _firma(ts, "7", rol, "*", tok),
         "Authorization": f"Bearer {tok}", "X-Suite-Via": "session"}
    if metodo != "GET":
        h["Origin"] = SUITE
    return h


def anon():
    ts = str(int(time.time()))
    return {"X-Forwarded-Prefix": "/reportes", "X-Suite-Via": "public", "X-Suite-Timestamp": ts,
            "X-Suite-Signature": _firma(ts, "", "", "", "")}


def main() -> int:
    almacen.guardar_cliente("cliff", "Aesthetics by Cliff", "LOC1", "America/Denver", None)
    almacen.guardar_config("cliff", {"nombre": "Aesthetics by Cliff", "slug": "cliff", "tz": "America/Denver",
                                     "ghlLocationId": "LOC1", "cuentas": []})
    snap = {"desde": "2026-06-01", "hasta": "2026-10-01", "generado": "2026-10-01T08:00Z",
            "cliente": {"nombre": "Aesthetics by Cliff", "tz": "America/Denver"},
            "leads": [{"nombre": "Zoe Smith", "telefono": "+13035556024"}]}
    r = C.post("/admin/snapshots/cliff", headers=ADMIN, json=snap)
    ok(r.status_code == 200, "snapshot publicado para la prueba", r.text[:80])
    tok = C.get("/admin/enlaces/general/cliff", headers=ADMIN).json().get("token")
    ok(bool(tok), "el enlace general existe tras publicar")

    r = C.get("/s/cliff/", headers=gw("admin"))
    ok(r.status_code == 200, "el dashboard se sirve al admin por la suite", str(r.status_code))
    ok('<meta name="roaslytics-ajustes" content="/reportes|cliff|admin">' in r.text,
       "el admin recibe el meta de Ajustes con prefijo y slug")
    ok('id="tabAjustes"' in r.text, "la página lleva la pestaña Ajustes (oculta hasta que el código la enseña)")
    js = C.get("/app.js", headers=anon()).text
    ok("function ajustes(" in js and "roaslytics-ajustes" in js, "app.js lleva el código de la pestaña")

    r = C.get("/s/cliff/", headers=gw("equipo"))
    ok(r.status_code == 200 and "roaslytics-ajustes" not in r.text, "el rol equipo NO recibe el meta", str(r.status_code))

    for ruta in (f"/r/{tok}/", f"/r/{tok}/embed"):
        r = C.get(ruta, headers=anon())
        ok(r.status_code in (200, 403) and "roaslytics-ajustes" not in r.text, f"{ruta[:12]}… no lleva el meta", str(r.status_code))

    r = C.get("/admin/enlaces/general/cliff", headers=gw("admin"))
    ok(r.status_code == 200 and r.json().get("token") == tok, "GET enlace general por la suite", r.text[:80])
    r = C.get("/admin/menu-ghl/cliff", headers=gw("admin"))
    ok(r.status_code == 200 and r.json().get("location") == "LOC1", "GET menú de GHL por la suite", r.text[:80])
    r = C.post("/admin/enlaces/general/cliff/rotar?confirmar=cliff", headers=gw("admin", "POST"))
    ok(r.status_code == 200 and r.json().get("token") != tok, "rotar por la suite crea otro enlace", r.text[:80])
    r = C.post("/admin/enlaces/general/cliff/rotar?confirmar=cliff", headers=gw("equipo", "POST"))
    ok(r.status_code in (401, 403), "el rol equipo no puede rotar", str(r.status_code))
    r = C.get("/admin", headers=gw("admin"), follow_redirects=False)
    ok(r.status_code == 302 and "/admin" in r.headers.get("location", "") and SUITE in r.headers.get("location", ""),
       "/admin ya no es un panel: manda a la suite", r.headers.get("location", ""))

    print()
    if fallos:
        print(f"{len(fallos)} fallo(s)")
        return 1
    print("todo bien")
    return 0


if __name__ == "__main__":
    sys.exit(main())
