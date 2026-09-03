# -*- coding: utf-8 -*-
"""
Prueba de /admin/cuentas: las listas que rellenan los desplegables del panel.

    python pruebas/test_cuentas.py

No toca ninguna API: sustituye los tres lectores (CRM, Meta, Google) por funciones
falsas. Lo que se comprueba no es que Meta conteste —eso solo se sabe en producción—
sino las tres cosas que tienen que aguantar cuando NO conteste:

  1. Que una fuente rota no se lleva por delante a las otras dos. Si Google falla, el
     panel se queda sin UN desplegable, no sin los tres.
  2. Que cada cuenta viene marcada con el cliente que ya la usa. La misma cuenta de
     anuncios en dos clientes suma su gasto dos veces y deja los dos CPL a la mitad.
  3. Que la caché no congela ese «en uso»: se recalcula en cada petición, porque cambia
     cada vez que alguien guarda un cliente.
"""
import os, pathlib, shutil, sys, tempfile

RAIZ = pathlib.Path(__file__).resolve().parent.parent
fallos = []


def ok(cond, etiqueta, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + etiqueta + (f"  {extra}" if extra else ""))
    if not cond:
        fallos.append(etiqueta)


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cuentas-"))
    os.environ["RUTA_DATOS_LOCAL"] = str(tmp)
    os.environ["ADMIN_TOKEN"] = "token-de-prueba"
    os.environ.pop("DATABASE_URL", None)
    sys.path.insert(0, str(RAIZ / "web"))

    from fastapi.testclient import TestClient
    from app import rutas_ficha
    from app.main import app, almacen

    c = TestClient(app)
    H = {"X-Admin-Token": "token-de-prueba"}

    # ── los tres lectores, falsos ─────────────────────────────
    llamadas = {"ghl": 0, "meta": 0}

    def ghl_falso():
        llamadas["ghl"] += 1
        return [{"id": "LOC-A", "nombre": "Clínica A"},
                {"id": "LOC-B", "nombre": "Clínica B"}]

    def meta_falso():
        llamadas["meta"] += 1
        return [{"id": "111222333", "nombre": "Cuenta A", "moneda": "USD",
                 "tz": "America/Denver", "activa": True},
                {"id": "999888777", "nombre": "Cuenta B", "moneda": "EUR",
                 "tz": "Europe/Madrid", "activa": False}]

    def google_roto():
        raise RuntimeError("el developer token no vale")

    rutas_ficha.cuentas_ghl = ghl_falso
    rutas_ficha.cuentas_meta = meta_falso
    rutas_ficha.cuentas_google = google_roto
    rutas_ficha._cache.clear()

    print("\n── una fuente rota no tumba a las otras ───────────────────")
    r = c.get("/admin/cuentas", headers=H)
    ok(r.status_code == 200, "la ruta contesta 200 aunque Google falle", r.status_code)
    d = r.json()
    ok(len(d["ghl"]["cuentas"]) == 2, "llegan las sub-cuentas del CRM")
    ok(len(d["meta"]["cuentas"]) == 2, "llegan las cuentas de Meta")
    ok(d["google"]["cuentas"] == [], "Google viene vacío")
    ok("developer token" in (d["google"]["error"] or ""),
       "y con el motivo exacto, para poder arreglarlo")
    ok(d["ghl"]["error"] is None and d["meta"]["error"] is None,
       "las que sí funcionan no traen error")

    print("\n── el nombre viaja con el identificador ───────────────────")
    a = d["meta"]["cuentas"][0]
    ok(a.get("nombre") and a.get("id"), "cada cuenta trae nombre e id")
    ok(a.get("moneda") and a.get("tz"), "y su moneda y su zona, para poder contrastar")
    ok(any(x["activa"] is False for x in d["meta"]["cuentas"]),
       "una cuenta pausada se marca pero NO se esconde (tiene gasto histórico)")

    print("\n── se avisa de lo que ya está en uso ──────────────────────")
    ok(all(x["usadaPor"] is None for x in d["ghl"]["cuentas"]),
       "sin clientes dados de alta, nada está en uso")

    almacen.guardar_cliente("clinica-a", "Clínica A", "LOC-A", "America/Denver", None)
    almacen.guardar_config("clinica-a", {"nombre": "Clínica A", "slug": "clinica-a",
                                         "tz": "America/Denver", "ghlLocationId": "LOC-A",
                                         "cuentas": [{"plataforma": "Meta",
                                                      "id": "111222333"}]})
    d2 = c.get("/admin/cuentas", headers=H).json()
    loc = {x["id"]: x["usadaPor"] for x in d2["ghl"]["cuentas"]}
    ads = {x["id"]: x["usadaPor"] for x in d2["meta"]["cuentas"]}
    ok(loc["LOC-A"] == "clinica-a", "el location ya asignado dice de quién es")
    ok(loc["LOC-B"] is None, "y el libre sigue libre")
    ok(ads["111222333"] == "clinica-a", "la cuenta de Meta ya asignada, igual")
    ok(ads["999888777"] is None, "y la libre, libre")

    print("\n── la caché no congela el «en uso» ───────────────────────")
    ok(llamadas["ghl"] == 1, "la segunda petición NO volvió a preguntar al CRM",
       f"{llamadas['ghl']} llamada(s)")
    ok(loc["LOC-A"] == "clinica-a",
       "pero el «en uso» sí se recalculó: es lo que cambia al guardar un cliente")

    r = c.get("/admin/cuentas?refrescar=1", headers=H)
    ok(r.status_code == 200 and llamadas["ghl"] == 2,
       "refrescar=1 salta la caché", f"{llamadas['ghl']} llamada(s)")

    print("\n── sigue protegida por el token ──────────────────────────")
    ok(c.get("/admin/cuentas").status_code == 401, "sin token, 401")

    print()
    shutil.rmtree(tmp, ignore_errors=True)
    if fallos:
        print(f"FALLOS ({len(fallos)}): " + " · ".join(fallos))
        return 1
    print("todo bien")
    return 0


if __name__ == "__main__":
    sys.exit(main())
