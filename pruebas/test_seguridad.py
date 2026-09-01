"""
Comprobaciones de seguridad del servicio.

    python pruebas/test_seguridad.py

Cada una corresponde a una forma concreta en la que alguien podría llegar a los datos de
un cliente o generar un reporte sin permiso. Si alguna falla, no se despliega.
"""
import os
import pathlib
import sys
import tempfile

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "web"))

TOKEN = "token-de-prueba-solo-para-los-tests"
TMP = tempfile.mkdtemp(prefix="reportes-test-")
os.environ["ADMIN_TOKEN"] = TOKEN
os.environ["RUTA_DATOS_LOCAL"] = TMP
os.environ.pop("DATABASE_URL", None)

import logging  # noqa: E402
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("reportes").setLevel(logging.ERROR)
logging.getLogger("reportes.seguridad").setLevel(logging.ERROR)

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app, LIM_ADMIN, LIM_ENLACE  # noqa: E402

C = TestClient(app)
ADMIN = {"X-Admin-Token": TOKEN}
bien = []


def ok(nombre, cond, extra=""):
    bien.append(bool(cond))
    print(("  ✓ " if cond else "  ✗ ") + nombre + (f"  ({extra})" if extra and not cond else ""))


def csp(r):
    return r.headers.get("content-security-policy", "")


# ══════════════════════════════════════════════════════════════════════════════
#  1 · Nadie entra a administración sin el token
# ══════════════════════════════════════════════════════════════════════════════
print("1 · administración")
for ruta, metodo in [("/admin/estado", "get"), ("/admin/clientes", "post"),
                     ("/admin/visor", "post"), ("/admin/enlaces", "get"),
                     ("/admin/snapshots/x", "post"), ("/admin/snapshots/x", "get")]:
    r = C.post(ruta, json={}) if metodo == "post" else C.get(ruta)
    ok(f"{metodo.upper()} {ruta} sin token → 401", r.status_code == 401, r.status_code)

r = C.get("/admin/estado", headers={"X-Admin-Token": TOKEN[:-1] + "X"})
ok("un token casi correcto → 401", r.status_code == 401, r.status_code)
ok("con el token → 200", C.get("/admin/estado", headers=ADMIN).status_code == 200)

# La página del panel es pública a propósito (no lleva datos), pero no debe indexarse.
r = C.get("/admin")
# La página es solo el formulario: no lleva el token ni datos de ningún cliente.
# (Contiene la palabra "leads" porque describe columnas, no porque traiga datos.)
ok("la página del panel no lleva el token ni datos de clientes",
   r.status_code == 200 and TOKEN not in r.text and "Cliente Uno" not in r.text)
ok("la página del panel prohíbe indexarse", "noindex" in r.headers.get("x-robots-tag", ""))
ok("la raíz no anuncia el panel", "/admin" not in C.get("/").text)
ok("robots.txt prohíbe todo", "Disallow: /" in C.get("/robots.txt").text)
ok("/salud no cuenta nada del interior", C.get("/salud").json() == {"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  2 · Nadie llega a los datos de un cliente sin un enlace válido
# ══════════════════════════════════════════════════════════════════════════════
print("\n2 · acceso a los datos")
C.post("/admin/clientes", headers=ADMIN,
       json={"slug": "cliente-uno", "nombre": "Cliente Uno", "tz": "America/Denver"})
SNAP = {"desde": "2026-05-03", "hasta": "2026-08-30",
        "cliente": {"nombre": "Cliente Uno", "tz": "America/Denver"},
        "stages": [{"id": "e1", "n": "Uno", "p": 1, "pipe": "p1"}],
        "leads": [{"id": "a", "n": "Ana Pérez Secreta", "f": "2026-06-01",
                   "fo": "2026-06-01", "ei": "e1", "rec": 0}]}
r = C.post("/admin/snapshots/cliente-uno", headers=ADMIN, json=SNAP)
ok("se puede publicar un snapshot válido", r.status_code == 200, r.text[:120])

ok("un token de enlace inventado → 404", C.get("/r/inventado/").status_code == 404)
ok("su snapshot inventado → 404", C.get("/r/inventado/snapshot.json").status_code == 404)
ok("no hay ruta que sirva datos por identificador de cliente",
   C.get("/cliente-uno/snapshot.json").status_code == 404)
ok("el snapshot no se puede pedir a /admin sin token",
   C.get("/admin/snapshots/cliente-uno").status_code == 401)

tok = C.post("/admin/enlaces", headers=ADMIN,
             json={"cliente": "cliente-uno", "modo": "cliente"}).json()["token"]
r = C.get(f"/r/{tok}/snapshot.json")
ok("con enlace válido sí se sirven los datos", r.status_code == 200)
ok("el modo cliente trae el nombre real", "Ana Pérez Secreta" in r.text)

# Recorrido de rutas: el identificador se usa como nombre de fichero en el almacén.
for malo in ["../../etc/passwd", "..%2f..%2fetc%2fpasswd", "cliente-uno/../otro"]:
    r = C.post(f"/admin/snapshots/{malo}", headers=ADMIN, json=SNAP)
    ok(f"recorrido de rutas rechazado: {malo[:22]}", r.status_code in (404, 400, 405, 307),
       r.status_code)

# Modo demo: el enmascarado tiene que pasar en el SERVIDOR, no en el navegador.
dem = C.post("/admin/enlaces", headers=ADMIN,
             json={"cliente": "cliente-uno", "modo": "demo"}).json()["token"]
r = C.get(f"/r/{dem}/snapshot.json")
ok("el enlace demo NO transporta el nombre real", "Ana Pérez Secreta" not in r.text)
ok("el enlace demo enmascara con iniciales", "A. P. S." in r.text, r.text[:200])
ok("el enlace demo oculta el nombre del negocio", "Cliente (demo)" in r.text)

# Revocar y caducar cortan de verdad, también los datos.
C.post(f"/admin/enlaces/{tok}/revocar", headers=ADMIN)
ok("revocado: la página → 403", C.get(f"/r/{tok}/").status_code == 403)
ok("revocado: los datos → 403", C.get(f"/r/{tok}/snapshot.json").status_code == 403)

cad = C.post("/admin/enlaces", headers=ADMIN,
             json={"cliente": "cliente-uno", "caduca": "2020-01-01T00:00:00Z"}).json()["token"]
ok("caducado: los datos → 403", C.get(f"/r/{cad}/snapshot.json").status_code == 403)

# ══════════════════════════════════════════════════════════════════════════════
#  3 · Público pero no indexable, y no empotrable salvo donde se diga
# ══════════════════════════════════════════════════════════════════════════════
print("\n3 · indexación y empotrado")
libre = C.post("/admin/enlaces", headers=ADMIN, json={"cliente": "cliente-uno"}).json()["token"]
r = C.get(f"/r/{libre}/")
ok("el reporte prohíbe indexarse", "noindex" in r.headers.get("x-robots-tag", ""))
ok("no filtra la URL por el Referer", r.headers.get("referrer-policy") == "no-referrer")
ok("obliga a HTTPS en visitas futuras", "max-age=" in r.headers.get("strict-transport-security", ""))
ok("por defecto NO se puede empotrar", "frame-ancestors 'none'" in csp(r))
ok("y además manda X-Frame-Options para navegadores viejos",
   r.headers.get("x-frame-options") == "DENY")
ok("el reporte no puede pedir nada a terceros", "connect-src 'self'" in csp(r))
ok("el reporte no puede enviar formularios", "form-action 'none'" in csp(r))
ok("los datos nunca se cachean", "no-store" in C.get(f"/r/{libre}/snapshot.json")
   .headers.get("cache-control", ""))
ok("sin dominios, /embed → 403", C.get(f"/r/{libre}/embed").status_code == 403)

emb = C.post("/admin/enlaces", headers=ADMIN,
             json={"cliente": "cliente-uno",
                   "dominios": "app.gohighlevel.com, *.msgsndr.com"}).json()
ok("crear enlace empotrable devuelve su ruta de iframe",
   emb.get("rutaEmbed", "").endswith("/embed"))
r = C.get(f"/r/{emb['token']}/embed")
ok("el enlace empotrable declara solo esos dominios",
   "frame-ancestors https://app.gohighlevel.com https://*.msgsndr.com" in csp(r))
ok("y NO manda X-Frame-Options, que rompería el iframe",
   "x-frame-options" not in {k.lower() for k in r.headers})

for malo in ["javascript:alert(1)", "http://insecuro.com", "*", "'self'",
             "https://ok.com/ruta", "data:text/html,x"]:
    r = C.post("/admin/enlaces", headers=ADMIN,
               json={"cliente": "cliente-uno", "dominios": malo})
    ok(f"origen inválido rechazado: {malo[:24]}", r.status_code == 400, r.status_code)

# ══════════════════════════════════════════════════════════════════════════════
#  4 · Topes y límite de intentos
# ══════════════════════════════════════════════════════════════════════════════
print("\n4 · topes y límite de intentos")
r = C.post("/admin/visor", headers=ADMIN, content=b"x" * (9 * 1024 * 1024))
ok("un dashboard que pasa del tope → 413", r.status_code == 413, r.status_code)

LIM_ADMIN.acierto("testclient")
for _ in range(12):
    C.get("/admin/estado", headers={"X-Admin-Token": "malo"})
ok("tras muchos fallos, el token malo → 429",
   C.get("/admin/estado", headers={"X-Admin-Token": "malo"}).status_code == 429)
ok("pero el token BUENO sigue entrando (no se autobloquea)",
   C.get("/admin/estado", headers=ADMIN).status_code == 200)

LIM_ENLACE.acierto("testclient")
for i in range(45):
    C.get(f"/r/noexiste{i}/")
ok("sondear tokens inexistentes acaba en 429", C.get("/r/otromas/").status_code == 429)
# Se comprueba con el snapshot: /r/{t}/ devolvería 503 porque en las pruebas no se sube
# ningún dashboard, y eso no dice nada de si el enlace se resolvió.
ok("y un enlace VÁLIDO se sigue sirviendo",
   C.get(f"/r/{libre}/snapshot.json").status_code == 200)

print(f"\n{sum(bien)}/{len(bien)} comprobaciones de seguridad")
sys.exit(0 if all(bien) else 1)
