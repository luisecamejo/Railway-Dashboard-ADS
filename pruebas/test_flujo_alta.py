# -*- coding: utf-8 -*-
"""
Prueba del flujo de alta: sin datos no hay enlace, y un enlace bueno no miente.

    python pruebas/test_flujo_alta.py

Lo que de verdad demuestra:

  1. Que **no se puede crear un enlace de un cliente sin snapshot**. Es la corrección de
     un fallo real: el enlace se creaba igual y quien lo abría leía "Este reporte no
     existe", que suena a enlace roto cuando lo que faltaba eran los datos.
  2. Que el snapshot que falta responde **409 y no 404**, para que el visor pueda
     distinguir "tu enlace es malo" de "tus datos aún no están".
  3. Que en cuanto hay snapshot, el mismo enlace se crea y sirve los datos.
  4. Que la moneda viaja en el snapshot, porque el dashboard formatea con ella.

No toca el CRM ni la red: usa el almacén de ficheros en una carpeta temporal.
"""
import json, os, pathlib, shutil, sys, tempfile

RAIZ = pathlib.Path(__file__).resolve().parent.parent
fallos = []


def ok(cond, etiqueta, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + etiqueta + (f"  {extra}" if extra else ""))
    if not cond:
        fallos.append(etiqueta)


def snapshot_minimo() -> dict:
    return {"cliente": "Prueba", "tz": "Europe/Madrid",
            "desde": "2026-08-01", "hasta": "2026-08-31",
            "moneda": "EUR",
            "leads": [], "camps": [], "gasto": [], "sp": {}}


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="flujo-alta-"))
    os.environ["RUTA_DATOS_LOCAL"] = str(tmp)
    os.environ["ADMIN_TOKEN"] = "token-de-prueba"
    os.environ.pop("DATABASE_URL", None)
    sys.path.insert(0, str(RAIZ / "web"))

    from fastapi.testclient import TestClient
    from app.main import app, almacen

    c = TestClient(app)
    H = {"X-Admin-Token": "token-de-prueba"}

    print("\n── el alta ────────────────────────────────────────────────")
    # Sin location no se puede detectar nada, así que la zona se manda a mano: es el
    # camino de escape que tiene que seguir existiendo.
    r = c.post("/admin/clientes", headers=H,
               json={"slug": "prueba", "nombre": "Prueba", "tz": "Europe/Madrid"})
    ok(r.status_code == 200, "se da de alta un cliente con zona a mano", r.status_code)
    ok((r.json() or {}).get("tz") == "Europe/Madrid", "devuelve la zona que se guardó")
    ok((r.json() or {}).get("tzDetectada") is False, "y dice que NO la detectó del CRM")
    cfg = (almacen.cliente("prueba") or {}).get("config") or {}
    ok(cfg.get("tz") == "Europe/Madrid",
       "el alta siembra la config con la zona (antes había que teclearla dos veces)")

    print("\n── sin datos no hay enlace ──────────────────────────────────")
    r = c.post("/admin/enlaces", headers=H, json={"cliente": "prueba", "modo": "cliente"})
    ok(r.status_code == 409, "crear enlace sin snapshot → 409", r.status_code)
    ok("Preparar el reporte" in json.dumps(r.json(), ensure_ascii=False),
       "y el mensaje dice qué hacer, no solo que no")
    ok(len(almacen.enlaces("prueba")) == 0, "no quedó ningún enlace creado a medias")

    print("\n── el atajo de emergencia sigue existiendo ──────────────────────")
    r = c.post("/admin/enlaces", headers=H,
               json={"cliente": "prueba", "modo": "interno", "forzar": True})
    ok(r.status_code == 200, "con forzar:true sí se crea", r.status_code)
    tok = (r.json() or {}).get("token")

    print("\n── un enlace bueno sin datos NO dice 'no existe' ──────────────────")
    r = c.get(f"/r/{tok}/snapshot.json")
    ok(r.status_code == 409, "snapshot que falta → 409, no 404", r.status_code)
    ok("preparando" in json.dumps(r.json(), ensure_ascii=False),
       "y el motivo habla de datos, no de enlaces")

    r = c.get("/r/token-que-no-existe/snapshot.json")
    ok(r.status_code == 404, "un token inventado sigue siendo 404", r.status_code)
    ok(r.status_code != 409, "los dos casos ya NO se confunden")

    print("\n── con datos, todo funciona ───────────────────────────────")
    almacen.publicar_snapshot("prueba", snapshot_minimo())
    r = c.post("/admin/enlaces", headers=H, json={"cliente": "prueba", "modo": "cliente"})
    ok(r.status_code == 200, "ahora sí se crea el enlace", r.status_code)
    tok2 = (r.json() or {}).get("token")
    r = c.get(f"/r/{tok2}/snapshot.json")
    ok(r.status_code == 200, "y sirve los datos", r.status_code)
    ok((r.json() or {}).get("moneda") == "EUR",
       "la moneda llega al dashboard (si no, los euros se pintan con '$')")

    print("\n── la detección de zona no puede tumbar el alta ───────────────────")
    # Sin GHL_MCP_URL/TOKEN la detección falla. El cliente TIENE que crearse igual.
    for v in ("GHL_MCP_URL", "GHL_MCP_TOKEN"):
        os.environ.pop(v, None)
    r = c.post("/admin/clientes", headers=H,
               json={"slug": "sin-crm", "nombre": "Sin CRM",
                     "ghl_location_id": "LOCATIONQUENOEXISTE"})
    ok(r.status_code == 200, "se crea aunque el CRM no conteste", r.status_code)
    d = r.json() or {}
    ok(d.get("tz") in (None, ""), "se queda sin zona, que es la verdad")
    ok(bool(d.get("aviso")), "y avisa de por qué, para que se ponga a mano")
    ok(almacen.cliente("sin-crm") is not None, "el cliente existe de verdad")

    print()
    shutil.rmtree(tmp, ignore_errors=True)
    if fallos:
        print(f"FALLOS ({len(fallos)}): " + " · ".join(fallos))
        return 1
    print("todo bien")
    return 0


if __name__ == "__main__":
    sys.exit(main())
