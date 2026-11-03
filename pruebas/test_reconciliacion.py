# -*- coding: utf-8 -*-
"""
La Reconciliación dentro de ROASLytics y la Ficha de cliente (nov-2026).

Comprueba, con el gateway de la suite simulado (mismo montaje que test_ajustes.py):
  1. el equipo registra un lote con filas de varios tipos; la regla del 15 % pre-rellena SI/NO;
     las informativas nacen cerradas;
  2. lo ya reconciliado se detecta (comprobar) y no se vuelve a proponer (omitidos), salvo forzar;
  3. el rol vendedor: entra SOLO a la reconciliación (el dashboard y el snapshot le dan 302/403),
     decide, pero no cierra ni borra;
  4. equipo decide y cierra; cerrar todo lo decidido del lote; el lote se cierra solo;
  5. el Excel sale con las pestañas esperadas;
  6. la Ficha se guarda y se lee; la pestaña Reconciliación se anuncia con el meta.

    python pruebas/test_reconciliacion.py
"""
import base64, hashlib, hmac, io, json, logging, os, pathlib, sys, tempfile, threading, time, uuid
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
os.environ["RUTA_DATOS_LOCAL"] = tempfile.mkdtemp(prefix="reconc-")
os.environ["SUITE_URL"] = SUITE
os.environ["SUITE_GATEWAY_SECRET"] = SECRETO
os.environ["GATEWAY_ONLY"] = "1"
os.environ["GHL_MENU_AUTO"] = "0"
os.environ.pop("DATABASE_URL", None)
logging.disable(logging.WARNING)

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app, almacen  # noqa: E402
from app import reconciliacion as rc  # noqa: E402

C = TestClient(app, base_url="https://reportes-production-a40d.up.railway.app")
ADMIN = {"X-Admin-Token": "token-de-prueba"}
fallos = []


def ok(cond, etiqueta, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + etiqueta + (f"  ({extra})" if extra and not cond else ""))
    if not cond:
        fallos.append(etiqueta)


def _firma(ts, user, role, subs, tok):
    return hmac.new(SECRETO.encode(), "\n".join([ts, user, role, subs, tok]).encode(), hashlib.sha256).hexdigest()


def jwt_de(rol, email, subs="*"):
    ahora = int(time.time())
    claims = {"iss": SUITE, "aud": "sentinel-suite", "sub": email, "iat": ahora, "exp": ahora + 900,
              "jti": str(uuid.uuid4()), "email": email, "name": email.split("@")[0],
              "tools": {"reportes": rol}, "subs": subs, "agency": "equipo", "adm": rol == "admin"}
    return jwt.encode(claims, CLAVE, algorithm="EdDSA", headers={"kid": "k1"})


def gw(rol, metodo="GET", email=None, subs="*"):
    email = email or f"{rol}@sentinelmarketing.net"
    tok, ts = jwt_de(rol, email, subs), str(int(time.time()))
    s = subs if isinstance(subs, str) else ",".join(subs)
    h = {"X-Forwarded-Prefix": "/reportes", "X-Forwarded-Proto": "https",
         "X-Forwarded-Host": "reportes-production-a40d.up.railway.app",
         "X-Suite-User": email, "X-Suite-Role": rol, "X-Suite-Subaccounts": s,
         "X-Suite-Timestamp": ts, "X-Suite-Signature": _firma(ts, email, rol, s, tok),
         "Authorization": f"Bearer {tok}", "X-Suite-Via": "session"}
    if metodo != "GET":
        h["Origin"] = SUITE
    return h


def main() -> int:
    almacen.guardar_cliente("cliff", "Aesthetics by Cliff", "LOC1", "America/Denver", None)
    almacen.guardar_config("cliff", {"nombre": "Aesthetics by Cliff", "slug": "cliff", "tz": "America/Denver",
                                     "ghlLocationId": "LOC1", "cuentas": [], "moneda": "USD"})
    snap = {"desde": "2026-09-01", "hasta": "2026-10-01", "generado": "2026-10-01T08:00Z",
            "cliente": {"nombre": "Aesthetics by Cliff", "tz": "America/Denver"}, "moneda": "USD",
            "pipelines": [{"id": "P1", "n": "01. Sales Pipeline"}], "stages": [{"id": "S9", "n": "Won Deals"}],
            "leads": []}
    r = C.post("/admin/snapshots/cliff", headers=ADMIN, json=snap)
    ok(r.status_code == 200, "snapshot publicado para la prueba", r.text[:80])

    print("\n1 · El equipo registra un lote")
    base = "/s/cliff/reconciliacion/api"
    lote = {"lote": {"titulo": "Reconciliación septiembre 2026", "periodo_desde": "2026-09-01", "periodo_hasta": "2026-09-30",
                     "criterio": "ventas", "plataforma": "Fresha", "total_export": 1500.5},
            "items": [
                {"tipo": "valor", "contacto": "Zoe Smith", "opp_id": "OPP1", "contact_id": "CT1", "telefono": "+1 (303) 555-6024",
                 "opp_creada": "2026-09-03", "estado_opp": "open", "valor_plataforma": 520, "valor_ghl": 0,
                 "evidencia": "Cruce por teléfono", "cruce_por": "teléfono"},
                {"tipo": "valor", "contacto": "Ana Pérez", "opp_id": "OPP2", "opp_creada": "2026-09-10", "estado_opp": "won",
                 "valor_plataforma": 100, "valor_ghl": 95},
                {"tipo": "fuera_periodo", "contacto": "Luis Viejo", "cliente_plataforma": "Luis Viejo", "contacto_alta": "2026-07-30",
                 "valor_plataforma": 520.56},
                {"tipo": "source", "contacto": "Pepe Llamada", "opp_id": "OPP3", "telefono": "3035550001", "fecha_evento": "2026-09-12 10:30",
                 "campana": "SEN_Google_Brand", "source_actual": "GBP", "source_propuesto": "Google", "propuesta": "Escribir source «Google»",
                 "evidencia": "Llamada el mismo día que la opp"},
                {"tipo": "cobro_sin_oportunidad", "cliente_platforma": "x", "cliente_plataforma": "Nadie Conocido", "telefono": "3035550002",
                 "valor_plataforma": 360},
            ]}
    r = C.post(base + "/lotes", headers=gw("equipo", "POST"), json=lote)
    ok(r.status_code == 201, "equipo crea el lote (201)", r.text[:200])
    j = r.json()
    ok(j["creados"] == 5 and not j["omitidos"], "5 filas creadas, ninguna omitida", json.dumps(j.get("omitidos"))[:120])
    its = {i["contacto"]: i for i in j["items"]}
    ok(its["Zoe Smith"]["propuesta_decision"] == "SI" and its["Zoe Smith"]["diferencia_pct"] == 100.0,
       "regla del 15 %: valor 0 → 100 % → SI", str(its["Zoe Smith"].get("diferencia_pct")))
    ok("marcar ganada" in (its["Zoe Smith"]["propuesta"] or ""), "la propuesta dice que también cambia el estado (open → won)")
    ok(its["Ana Pérez"]["propuesta_decision"] == "NO" and its["Ana Pérez"]["diferencia_pct"] == 5.0, "regla del 15 %: 5 % → NO")
    ok(its["Luis Viejo"]["informativo"] and its["Luis Viejo"]["estado"] == "cerrado", "fuera del periodo nace cerrado (informativo)")
    ok(its["Pepe Llamada"]["estado"] == "abierto" and not its["Pepe Llamada"]["informativo"], "la llamada de Google con propuesta sí se decide")
    ok("tel:3035556024" in its["Zoe Smith"]["claves"] and "opp:OPP1" in its["Zoe Smith"]["claves"], "claves normalizadas (teléfono sin +1)")
    lote_id = j["lote"]["id"]

    print("\n2 · Lo ya reconciliado se detecta")
    r = C.get(base + "/comprobar?opps=OPP1,OPP9&telefonos=%2B13035550001&emails=nadie@x.com", headers=gw("equipo"))
    ok(r.status_code == 200, "comprobar responde", r.text[:120])
    j = r.json()
    ok("opp:OPP1" in j["reconciliados"] and "tel:3035550001" in j["reconciliados"], "encuentra por opp y por teléfono", json.dumps(j)[:200])
    ok("opp:OPP9" in j["sin_registro"] and "email:nadie@x.com" in j["sin_registro"], "y dice cuáles no tienen registro")
    r = C.post(base + "/lotes", headers=gw("equipo", "POST"),
               json={"lote": {"titulo": "Repetido"}, "items": [{"tipo": "valor", "contacto": "Zoe Smith", "opp_id": "OPP1", "valor_plataforma": 600, "valor_ghl": 0},
                                                              {"tipo": "valor", "contacto": "Nueva Persona", "opp_id": "OPP7", "valor_plataforma": 300, "valor_ghl": 0}]})
    j = r.json()
    ok(r.status_code == 201 and j["creados"] == 1 and len(j["omitidos"]) == 1 and "ya reconciliado" in j["omitidos"][0]["motivo"],
       "la fila repetida se omite y la nueva entra", r.text[:200])
    lote2 = j["lote"]["id"]
    r = C.post(base + "/lotes", headers=gw("equipo", "POST"),
               json={"lote": {"titulo": "Solo repetidos"}, "items": [{"tipo": "valor", "contacto": "Zoe Smith", "opp_id": "OPP1", "valor_plataforma": 600, "valor_ghl": 0}]})
    ok(r.status_code == 200 and r.json()["lote"] is None, "si todo está repetido no se crea lote", r.text[:120])
    r = C.post(base + "/lotes", headers=gw("equipo", "POST"),
               json={"forzar": True, "lote": {"titulo": "Forzado"}, "items": [{"tipo": "valor", "contacto": "Zoe Smith", "opp_id": "OPP1", "valor_plataforma": 600, "valor_ghl": 0}]})
    ok(r.status_code == 201 and r.json()["creados"] == 1, "con forzar sí entra", r.text[:120])
    C.delete(base + "/lotes/" + r.json()["lote"]["id"], headers=gw("admin", "DELETE"))

    print("\n3 · El rol vendedor")
    V = dict(email="vendedor@cliente.com", subs=["cliff"])
    r = C.get("/s/cliff/", headers=gw("vendedor", **V), follow_redirects=False)
    ok(r.status_code == 302 and r.headers["location"].endswith("/s/cliff/reconciliacion/"), "el dashboard lo manda a la reconciliación", f"{r.status_code} {r.headers.get('location')}")
    r = C.get("/s/cliff/snapshot.json", headers=gw("vendedor", **V))
    ok(r.status_code == 403, "el snapshot le da 403", str(r.status_code))
    r = C.get("/s/cliff/refrescar", headers=gw("vendedor", **V))
    ok(r.status_code == 403, "refrescar le da 403", str(r.status_code))
    r = C.get("/s/cliff/api/ficha", headers=gw("vendedor", **V))
    ok(r.status_code == 403, "la ficha le da 403", str(r.status_code))
    r = C.get("/s/cliff/reconciliacion/", headers=gw("vendedor", **V))
    ok(r.status_code == 200 and 'data-rol="vendedor"' in r.text and "Dashboard completo" not in r.text,
       "ve la página de reconciliación, sin enlace al dashboard", str(r.status_code))
    ok("script-src 'self' " + SUITE in r.headers.get("content-security-policy", ""), "la CSP deja cargar la barra de la suite")
    r = C.get(base, headers=gw("vendedor", **V))
    j = r.json()
    ok(r.status_code == 200 and j["rol"] == "vendedor" and "decidir" in j["permisos"] and "cerrar" not in j["permisos"],
       "la API le dice que decide pero no cierra", r.text[:160])
    ok(len(j["items"]) == 6 and j["total"]["cerrados"] == 2, "ve todas las filas (6) con las 2 informativas ya cerradas", json.dumps(j["total"]))
    zoe = its["Zoe Smith"]["id"]
    r = C.post(f"{base}/items/{zoe}/decidir", headers=gw("vendedor", "POST", **V), json={"decision": "si", "nota": "Pagó el 5"})
    ok(r.status_code == 200 and r.json()["item"]["decision"] == "SI" and r.json()["item"]["decidido_por"] == "vendedor@cliente.com",
       "decide (SI) y queda su correo", r.text[:160])
    r = C.post(f"{base}/items/{zoe}/cerrar", headers=gw("vendedor", "POST", **V), json={})
    ok(r.status_code == 403, "no puede cerrar", str(r.status_code))
    r = C.delete(f"{base}/lotes/{lote_id}", headers=gw("vendedor", "DELETE", **V))
    ok(r.status_code == 403, "no puede borrar", str(r.status_code))
    r = C.post(base + "/lotes", headers=gw("vendedor", "POST", **V), json=lote)
    ok(r.status_code == 403, "no puede registrar lotes", str(r.status_code))
    r = C.get(base, headers=gw("vendedor", email="otro@cliente.com", subs=["otra"]), follow_redirects=False)
    ok(r.status_code in (302, 403), "un vendedor de OTRA subcuenta no entra", str(r.status_code))

    print("\n4 · Decidir y cerrar")
    r = C.post(f"{base}/items/{zoe}/decidir", headers=gw("equipo", "POST"), json={"decision": "duda"})
    ok(r.status_code == 200 and r.json()["item"]["decision"] == "DUDA", "el equipo cambia la decisión")
    r = C.post(f"{base}/items/{zoe}/cerrar", headers=gw("equipo", "POST"), json={"resultado": "Opp Value 520 y ganada"})
    ok(r.status_code == 200 and r.json()["item"]["estado"] == "cerrado" and r.json()["item"]["cerrado_por"] == "equipo@sentinelmarketing.net",
       "el equipo cierra con resultado", r.text[:160])
    r = C.post(f"{base}/items/{zoe}/decidir", headers=gw("equipo", "POST"), json={"decision": "SI"})
    ok(r.status_code == 400, "una fila cerrada no se decide (400)", str(r.status_code))
    r = C.post(f"{base}/items/{zoe}/reabrir", headers=gw("equipo", "POST"), json={})
    ok(r.status_code == 200 and r.json()["item"]["estado"] == "decidido", "reabrir vuelve a decidido")
    r = C.post(f"{base}/items/{zoe}/decidir", headers=gw("equipo", "POST"), json={"decision": "SI"})
    ok(r.status_code == 200 and r.json()["item"]["decision"] == "SI", "y se puede volver a decidir (SI)")
    r = C.post(f"{base}/items/{its['Pepe Llamada']['id']}/cerrar", headers=gw("equipo", "POST"), json={})
    ok(r.status_code == 400, "no se cierra sin decidir (400)", str(r.status_code))
    for nombre in ("Ana Pérez", "Pepe Llamada"):
        C.post(f"{base}/items/{its[nombre]['id']}/decidir", headers=gw("equipo", "POST"), json={"decision": "NO" if nombre == "Ana Pérez" else "SI"})
    r = C.post(f"{base}/lotes/{lote_id}/cerrar", headers=gw("admin", "POST"), json={"resultado": "Aplicado"})
    ok(r.status_code == 200 and r.json()["cerrados"] == 3 and r.json()["lote"]["estado"] == "cerrado",
       "cerrar lo decidido del lote cierra 3 y el lote queda cerrado", r.text[:200])
    r = C.get(base + "?lote=" + lote_id, headers=gw("admin"))
    j = r.json()
    ok(all(i["estado"] == "cerrado" for i in j["items"]) and j["lotes"][0]["efecto_si"] == 520.0 if j["lotes"] else False,
       "historial completo del lote y efecto de lo aprobado (520)", json.dumps([(l['id'], l.get('efecto_si')) for l in j['lotes']]))
    r = C.get(base + "?estado=pendiente", headers=gw("admin"))
    ok(r.status_code == 200 and len(r.json()["items"]) == 1 and r.json()["items"][0]["contacto"] == "Nueva Persona",
       "pendiente solo queda la del segundo lote", r.text[:160])

    print("\n5 · Excel")
    r = C.get("/s/cliff/reconciliacion/export.xlsx", headers=gw("vendedor", **V))
    ok(r.status_code == 200 and r.content[:2] == b"PK", "el vendedor descarga el Excel", str(r.status_code) + " " + r.text[:100])
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(r.content))
        nombres = wb.sheetnames
        ok(nombres[0] == "1 · Resumen" and any("Reconciliar valor" in n for n in nombres) and any("Fuera del periodo" in n for n in nombres),
           "pestañas: Resumen, Reconciliar valor, Fuera del periodo…", str(nombres))
        hv = wb[[n for n in nombres if "Reconciliar valor" in n][0]]
        ok([c.value for c in hv[4]][:8] == ["Opp creada", "Cliente (2nda plataforma de Reconciliación)", "Contacto GHL",
                                            "Valor a Reconciliar Total (2nda plataforma de Reconciliación)", "Valor a Reconciliar GHL",
                                            "Diferencia en porcentaje", "APRUEBAS", "TU NOTA"], "columnas obligatorias en su orden", str([c.value for c in hv[4]][:8]))
        ok(hv["C5"].value == "Zoe Smith · open → won" and hv["G5"].value == "SI", "fila de Zoe con open → won y APRUEBAS=SI", f"{hv['C5'].value} {hv['G5'].value}")
        ok(any(dv.formula1 == '"SI,NO,DUDA"' for dv in hv.data_validations.dataValidation), "desplegable SI/NO/DUDA")
    except ImportError:
        ok(False, "openpyxl instalado para leer el Excel")
    r = C.get("/s/cliff/reconciliacion/export.xlsx?lote=" + lote2, headers=gw("equipo"))
    ok(r.status_code == 200, "Excel de un solo lote")

    print("\n6 · Ficha y pestaña")
    r = C.put("/s/cliff/api/ficha", headers=gw("equipo", "PUT"),
              json={"moneda": "usd", "pipeline_nombre": "01. Sales Pipeline", "etapa_cierre": "Won Deals", "prefijo_campanas": "SEN_",
                    "apps_reconciliacion": "Fresha", "criterio_reconciliacion": "ventas", "ventana_dias": "30", "numero_control": 132,
                    "numeros": [{"numero": "+1 385-452-9111", "nombre": "General", "source": "—", "atribuye": "NO", "notas": "Caller ID"},
                                {"numero": "+1 385-355-7780", "nombre": "GBP", "source": "GBP", "atribuye": "sí"}, {}],
                    "sources": [{"source": "Meta", "prioridad": "1", "clase": "Default", "decision": "Aprobar Propuesta"}],
                    "pipelines": [{"pipeline": "01. Sales Pipeline", "oportunidades": "1566", "destino": "DESTINO", "mapeo": "—"}]})
    ok(r.status_code == 200, "equipo guarda la ficha", r.text[:160])
    f = r.json()["ficha"]
    ok(f["criterio_reconciliacion"] == "Ventas" and f["ventana_dias"] == 30 and len(f["numeros"]) == 2 and f["numeros"][1]["atribuye"] == "SÍ",
       "normalizada: criterio capitalizado, ventana numérica, filas vacías fuera, SÍ/NO", json.dumps(f)[:200])
    r = C.get("/s/cliff/api/ficha", headers=gw("admin"))
    j = r.json()
    ok(r.status_code == 200 and j["ficha"]["prefijo_campanas"] == "SEN_" and j["identidad"]["ghl_location_id"] == "LOC1" and j["editable"],
       "se lee con la identidad de la suite al lado", r.text[:160])
    ok(j["pipelines_crm"] and j["pipelines_crm"][0]["n"] == "01. Sales Pipeline", "ofrece los pipelines del snapshot")
    r = C.get(base, headers=gw("equipo"))
    ok(r.json()["ficha"]["criterio_reconciliacion"] == "Ventas", "la reconciliación enseña el criterio de la ficha")
    r = C.put("/s/cliff/api/ficha", headers=gw("equipo", "PUT"), json={"criterio_reconciliacion": "otra cosa"})
    ok(r.status_code == 400, "criterio inválido → 400")
    r = C.get("/s/cliff/", headers=gw("equipo"))
    ok(r.status_code == 200 and 'name="roaslytics-persona"' in r.text and "|cliff|equipo|" in r.text, "el dashboard anuncia la pestaña al equipo")
    ok('name="roaslytics-ajustes"' not in r.text, "y no la de Ajustes (solo admin)")
    r = C.get("/reconciliacion.js", headers=gw("equipo"))
    ok(r.status_code == 200 and "ROASReconc" in r.text, "el código de la pestaña se sirve")
    r = C.delete(f"{base}/lotes/{lote2}", headers=gw("equipo", "DELETE"))
    ok(r.status_code == 403, "equipo no borra lotes")
    r = C.delete(f"{base}/lotes/{lote2}", headers=gw("admin", "DELETE"))
    ok(r.status_code == 200 and r.json()["items"] == 1, "admin borra el lote y sus filas")

    print("\n7 · Borrar el cliente se lleva su reconciliación")
    ok(bool(almacen.reconc_lotes("cliff")), "había lotes")
    C.delete("/admin/clientes/cliff?confirmar=cliff", headers=ADMIN)
    almacen.guardar_cliente("cliff", "Aesthetics by Cliff", "LOC1", "America/Denver", None)
    ok(not almacen.reconc_lotes("cliff") and not almacen.reconc_items("cliff"), "sin lotes ni filas tras borrar")

    print()
    if fallos:
        print(f"FALLOS ({len(fallos)}):"); [print("  -", f) for f in fallos]
        return 1
    print("Todo bien.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        srv.shutdown()
