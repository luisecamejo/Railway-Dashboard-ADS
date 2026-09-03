# -*- coding: utf-8 -*-
"""
Prueba de borrar un cliente: que se vaya TODO y que no se pueda borrar por accidente.

    python pruebas/test_borrado.py

Un borrado es la única operación del panel sin deshacer, así que lo que se comprueba
son las dos mitades del problema:

  1. Que **se lleva todo lo suyo**: snapshot, trozos crudos, enlaces, configuración y el
     fichero del disco. Un snapshot lleva nombres y teléfonos de pacientes; dejarlo
     detrás al dar de baja a un cliente es exactamente lo que no hay que hacer.
  2. Que **cuesta**: sin repetir el identificador exacto no se borra, y con una
     extracción en marcha tampoco.

Y una tercera que se olvida fácil: que borrar a UN cliente no toca a los demás.
"""
import os, pathlib, shutil, sys, tempfile

RAIZ = pathlib.Path(__file__).resolve().parent.parent
fallos = []


def ok(cond, etiqueta, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + etiqueta + (f"  {extra}" if extra else ""))
    if not cond:
        fallos.append(etiqueta)


def snap(nombre="Prueba", leads=3) -> dict:
    return {"cliente": {"nombre": nombre, "tz": "America/Denver"},
            "tz": "America/Denver", "desde": "2026-08-01", "hasta": "2026-08-31",
            "moneda": "USD",
            "leads": [{"id": f"L{i}", "n": f"Paciente {i}"} for i in range(leads)],
            "camps": [], "sp": {}}


def crudo_ghl() -> dict:
    return {"oportunidades": [{"oid": "o1", "created": "2026-08-02"}],
            "pipelines": [{"n": "Ventas", "stages": [{"id": "s1", "n": "Nuevo"}]}],
            "ventana": {"desde": "2026-08-01", "hasta": "2026-08-31"}}


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="borrado-"))
    os.environ["RUTA_DATOS_LOCAL"] = str(tmp)
    os.environ["ADMIN_TOKEN"] = "token-de-prueba"
    os.environ.pop("DATABASE_URL", None)
    sys.path.insert(0, str(RAIZ / "web"))

    from fastapi.testclient import TestClient
    from app.main import app, almacen
    from app import rutas_refrescar

    c = TestClient(app)
    H = {"X-Admin-Token": "token-de-prueba"}

    # ── dos clientes, uno con todo puesto ──────────────────────
    for slug, nombre in (("victima", "La Víctima"), ("vecino", "El Vecino")):
        almacen.guardar_cliente(slug, nombre, "LOC-" + slug, "America/Denver", None)
        almacen.guardar_config(slug, {"nombre": nombre, "slug": slug,
                                      "tz": "America/Denver", "ghlLocationId": "LOC-" + slug,
                                      "cuentas": [{"plataforma": "Meta", "id": "111222333"}]})
        almacen.publicar_snapshot(slug, snap(nombre))
        almacen.guardar_crudo(slug, "ghl", crudo_ghl())
    e1 = almacen.crear_enlace("victima", "cliente", "para el cliente")
    e2 = almacen.crear_enlace("victima", "demo", "caso de éxito")
    almacen.revocar_enlace(e2["token"])
    almacen.marcar_acceso(e1["token"])
    almacen.crear_enlace("vecino", "cliente", "el del vecino")

    print("\n── primero se ve QUÉ se va a destruir ─────────────────────")
    r = c.get("/admin/clientes/victima/borrado", headers=H)
    ok(r.status_code == 200, "la vista previa contesta", r.status_code)
    d = r.json()
    ok(d.get("nombre") == "La Víctima", "dice de quién es", str(d.get("nombre")))
    ok(d.get("enlaces") == 2 and d.get("enlacesActivos") == 1,
       "cuenta los enlaces y CUÁNTOS SIGUEN VIVOS (los empotrados dejan de funcionar)",
       f"{d.get('enlaces')} / {d.get('enlacesActivos')} activos")
    ok(d.get("snapshots") == 1, "cuenta los snapshots", str(d.get("snapshots")))
    ok(d.get("crudos") == 1, "y los trozos crudos", str(d.get("crudos")))
    ok(d.get("leads") == 3, "y las oportunidades que se van con él", str(d.get("leads")))
    ok(d.get("accesos") == 1, "y las visitas acumuladas", str(d.get("accesos")))
    ok(almacen.cliente("victima") is not None, "la vista previa NO ha borrado nada")

    print("\n── sin repetir el identificador NO se borra ─────────────────")
    ok(c.delete("/admin/clientes/victima", headers=H).status_code == 400,
       "sin confirmar → 400")
    ok(c.delete("/admin/clientes/victima?confirmar=victimaa", headers=H).status_code == 400,
       "con el identificador mal escrito → 400")
    ok(c.delete("/admin/clientes/victima?confirmar=vecino", headers=H).status_code == 400,
       "con el identificador de OTRO cliente → 400")
    ok(almacen.cliente("victima") is not None, "sigue ahí tras los tres intentos")
    ok(almacen.cliente("vecino") is not None, "y el vecino tampoco se ha tocado")

    print("\n── con una extracción en marcha, tampoco ────────────────────")
    from datetime import datetime, timezone
    rutas_refrescar._cola["victima"] = {"pedido": datetime.now(timezone.utc),
                                        "por": "prueba", "curso": True}
    r = c.delete("/admin/clientes/victima?confirmar=victima", headers=H)
    ok(r.status_code == 409, "extracción en curso → 409", r.status_code)
    ok(almacen.cliente("victima") is not None, "y el cliente sigue vivo")
    rutas_refrescar._cola.pop("victima", None)

    print("\n── ahora sí, y se lleva TODO ─────────────────────────────")
    antes_snap = (tmp / "snapshots" / "victima.json").exists()
    antes_crudo = (tmp / "crudos" / "victima").exists()
    ok(antes_snap and antes_crudo, "antes de borrar, los ficheros están en el disco")

    r = c.delete("/admin/clientes/victima?confirmar=victima", headers=H)
    ok(r.status_code == 200, "el borrado contesta 200", r.status_code)
    ok((r.json() or {}).get("enlaces") == 2, "y dice qué se llevó por delante")

    ok(almacen.cliente("victima") is None, "el cliente ya no existe")
    ok(almacen.snapshot("victima") is None, "su snapshot ya no existe")
    ok(almacen.crudos("victima") == {}, "sus trozos crudos ya no existen")
    ok(almacen.enlaces("victima") == [], "sus enlaces ya no existen")
    ok(almacen.historial("victima") == [], "su historial ya no existe")
    ok(not (tmp / "snapshots" / "victima.json").exists(),
       "el FICHERO del snapshot se borró del disco (llevaba datos de pacientes)")
    ok(not (tmp / "crudos" / "victima").exists(), "y la carpeta de crudos también")

    print("\n── un enlace de un cliente borrado ya no vale ─────────────────")
    ok(almacen.enlace(e1["token"]) is None, "el token desapareció del almacén")
    ok(c.get(f"/r/{e1['token']}/snapshot.json").status_code == 404,
       "y abrirlo da 404 (enlace que no existe), no un 409 de 'preparándose'")

    print("\n── el vecino sigue intacto ──────────────────────────────")
    ok(almacen.cliente("vecino") is not None, "el cliente")
    ok(almacen.snapshot("vecino") is not None, "su snapshot")
    ok(len(almacen.enlaces("vecino")) == 1, "su enlace")
    ok(list((almacen.crudos("vecino") or {}).keys()) == ["ghl"], "y su trozo crudo")
    ok((almacen.cliente("vecino") or {}).get("config", {}).get("tz") == "America/Denver",
       "y su configuración")

    print("\n── borrar dos veces no revienta ───────────────────────────")
    ok(c.delete("/admin/clientes/victima?confirmar=victima", headers=H).status_code == 404,
       "el segundo borrado → 404")
    ok(c.get("/admin/clientes/victima/borrado", headers=H).status_code == 404,
       "y su vista previa también")

    print("\n── el slug se puede reutilizar ────────────────────────────")
    r = c.post("/admin/clientes", headers=H,
               json={"slug": "victima", "nombre": "Otro Negocio", "tz": "Europe/Madrid"})
    ok(r.status_code == 200, "se puede volver a dar de alta el mismo identificador",
       r.status_code)
    ok(almacen.snapshot("victima") is None, "y arranca SIN los datos del anterior")

    print("\n── sigue protegido por el token ───────────────────────────")
    ok(c.delete("/admin/clientes/vecino?confirmar=vecino").status_code == 401,
       "sin token, 401")
    ok(almacen.cliente("vecino") is not None, "y el vecino sigue ahí")

    print()
    shutil.rmtree(tmp, ignore_errors=True)
    if fallos:
        print(f"FALLOS ({len(fallos)}): " + " · ".join(fallos))
        return 1
    print("todo bien")
    return 0


if __name__ == "__main__":
    sys.exit(main())
