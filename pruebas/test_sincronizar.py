# -*- coding: utf-8 -*-
"""
Los clientes de ROASLytics son las subcuentas de Sentinel Suite (web/app/sincronizar.py).

Lo que se rompió y no debe volver: el 20-oct-2026 la suite tenía 14 subcuentas y
reportes 8 clientes. Seis equipos abrían el dashboard desde la suite y leían «Este
cliente todavía no está en reportes». Aquí se comprueba que:

  1. una subcuenta nueva en la suite se convierte en cliente con su configuración de
     extracción (location de GHL, cuentas de Meta y de Google, zona y moneda del CRM)
     y se le encola su primera extracción;
  2. un cliente que ya existía NO se reencola, y solo se toca lo que gobierna la suite
     (nombre, location, cuentas): productos, SOP y roles se conservan. La ZONA la decide
     el CRM: nunca se pisa con la de la suite, y una puesta desde la suite se corrige;
  3. una pasada sin cambios no escribe nada;
  4. si la suite no contesta, no se rompe nada: el error se devuelve y se registra;
  5. una subcuenta archivada no se crea ni se borra.

No toca la red ni necesita credenciales.

    python pruebas/test_sincronizar.py
"""
import logging, os, pathlib, shutil, sys, tempfile

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "web"))
logging.disable(logging.WARNING)

fallos = []


def ok(cond, etiqueta):
    print(("  ✓ " if cond else "  ✗ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def main() -> int:
    os.environ["SUITE_URL"] = "https://suite.prueba"
    os.environ["SUITE_SERVICE_TOKEN"] = "ss_prueba"
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sync-"))
    try:
        from app.almacen import AlmacenFicheros
        from app import sincronizar as S

        alm = AlmacenFicheros(str(tmp))
        SUITE = [
            {"slug": "nuevo-spa", "name": "Nuevo Spa", "status": "active",
             "ghl_location_id": "LOCnuevo", "meta_accounts": ["111"], "google_accounts": ["123-456-7890"],
             "timezone": None},
            {"slug": "viejo", "name": "Viejo Renombrado", "status": "active",
             "ghl_location_id": "LOCviejo", "meta_accounts": ["222", "333"], "google_accounts": [],
             "timezone": "Europe/Madrid"},
            {"slug": "archivado", "name": "Archivado", "status": "archived",
             "ghl_location_id": "LOCarch", "meta_accounts": [], "google_accounts": []},
            # Zona puesta desde la suite en una pasada anterior: si el CRM la declara, se corrige
            {"slug": "corregir", "name": "Corregir", "status": "active",
             "ghl_location_id": "LOCcorr", "meta_accounts": [], "google_accounts": [], "timezone": "Europe/Madrid"},
        ]
        alm.guardar_cliente("corregir", "Corregir", "LOCcorr", "Europe/Madrid")
        alm.guardar_config("corregir", {"nombre": "Corregir", "slug": "corregir", "tz": "Europe/Madrid",
                                        "ghlLocationId": "LOCcorr", "cuentas": [], "tzFuente": S.TZ_DE_LA_SUITE})
        # Un cliente que ya existía, con configuración propia del reporte
        alm.guardar_cliente("viejo", "Viejo", "LOCviejo", "America/New_York")
        alm.guardar_config("viejo", {"nombre": "Viejo", "slug": "viejo", "tz": "America/New_York",
                                     "ghlLocationId": "LOCviejo",
                                     "cuentas": [{"plataforma": "Meta", "id": "222"}],
                                     "productos": [{"nombre": "Botox"}], "sop": {"Ganado": ["won"]}})

        encolados, fichas = [], []
        S.subcuentas_de_la_suite = lambda timeout=15: [s for s in SUITE if s["status"] == "active"]

        def ficha(loc):
            fichas.append(loc)
            return {"tz": "America/Denver", "moneda": "USD"}

        r = S.sincronizar(alm, encolar=lambda s, por: encolados.append((s, por)), ficha_crm=ficha, origen="prueba")
        ok(r["error"] is None and r["creados"] == ["nuevo-spa"], f"se crea el cliente nuevo: {r['creados']}")
        c = alm.cliente("nuevo-spa") or {}
        cfg = c.get("config") or {}
        ok(cfg.get("ghlLocationId") == "LOCnuevo" and cfg.get("tz") == "America/Denver" and cfg.get("moneda") == "USD",
           "configuración sembrada con location, zona y moneda del CRM")
        ok(sorted((x["plataforma"], x["id"]) for x in cfg.get("cuentas", [])) == [("Google", "123-456-7890"), ("Meta", "111")],
           "las cuentas de Meta y Google de la suite pasan a la configuración")
        ok(sorted(fichas) == ["LOCcorr", "LOCnuevo"], f"al CRM se le pregunta por el nuevo y por el que tenía la zona de la suite: {fichas}")
        cc = (alm.cliente("corregir") or {}).get("config") or {}
        ok(cc.get("tz") == "America/Denver" and "GoHighLevel" in cc.get("tzFuente", ""),
           "una zona puesta desde la suite se corrige con la del CRM")
        ok(encolados == [("nuevo-spa", "suite")], f"solo el nuevo se encola para su primera extracción: {encolados}")

        v = (alm.cliente("viejo") or {}).get("config") or {}
        cambios = [x for x in r["actualizados"] if x["slug"] == "viejo"]
        ok(bool(cambios) and set(cambios[0]["cambios"]) == {"nombre", "cuentas"},
           f"el existente se actualiza solo en lo que gobierna la suite: {cambios}")
        ok(v.get("nombre") == "Viejo Renombrado" and v.get("tz") == "America/New_York"
           and len(v.get("cuentas", [])) == 2, "nombre y cuentas vienen de la suite; la zona del CRM NO se pisa con la de la suite")
        ok(v.get("productos") == [{"nombre": "Botox"}] and v.get("sop") == {"Ganado": ["won"]},
           "productos y SOP del reporte se conservan")
        ok(alm.cliente("archivado") is None, "una subcuenta archivada no se crea")

        r2 = S.sincronizar(alm, encolar=lambda s, por: encolados.append((s, por)), ficha_crm=ficha, origen="prueba")
        ok(not r2["creados"] and not r2["actualizados"] and r2["sinCambios"] == 3 and len(encolados) == 1,
           "la segunda pasada no cambia nada ni reencola")

        import urllib.error
        def caida(timeout=15):
            raise urllib.error.URLError("sin red")
        S.subcuentas_de_la_suite = caida
        r3 = S.sincronizar(alm, encolar=lambda s, por: encolados.append((s, por)), origen="prueba")
        ok("no devolvió" in (r3["error"] or "") and len(encolados) == 1, "si la suite no contesta, se informa y no se toca nada")
        ok(S.estado()["resultado"] is r3 and S.estado()["cuando"], "el estado recuerda la última pasada")

        # ── archivar en la suite apaga el cliente aquí ──────────────────────
        # El caso de golden-rose: se deja de ser cliente, se archiva la subcuenta, y
        # hasta el 26-oct-2026 el extractor seguía yendo a por él todas las noches.
        vivas = [s for s in SUITE if s["status"] == "active" and s["slug"] != "viejo"]
        S.subcuentas_de_la_suite = lambda timeout=15: vivas
        r4 = S.sincronizar(alm, encolar=lambda s, por: encolados.append((s, por)),
                           ficha_crm=ficha, origen="prueba")
        ok(r4["desactivados"] == ["viejo"],
           f"la subcuenta que ya no está activa en la suite se apaga: {r4['desactivados']}")
        ok((alm.cliente("viejo") or {}).get("activo") is False,
           "queda apagado, no borrado")
        v4 = (alm.cliente("viejo") or {}).get("config") or {}
        ok(v4.get("productos") == [{"nombre": "Botox"}],
           "y conserva lo suyo: apagar no destruye nada")
        ok([c for c in alm.clientes() if c["slug"] == "viejo"],
           "sigue en la lista de clientes, visible para quien quiera borrarlo a mano")

        # ── y desarchivar lo vuelve a encender ──────────────────────────────
        S.subcuentas_de_la_suite = lambda timeout=15: [s for s in SUITE if s["status"] == "active"]
        r5 = S.sincronizar(alm, encolar=lambda s, por: encolados.append((s, por)),
                           ficha_crm=ficha, origen="prueba")
        reactivado = [x for x in r5["actualizados"] if x["slug"] == "viejo"]
        ok(bool(reactivado) and "reactivado" in reactivado[0]["cambios"],
           f"volver a activarla en la suite lo reenciende: {r5['actualizados']}")
        ok((alm.cliente("viejo") or {}).get("activo") is True, "y vuelve a estar activo")

        # ── una lista vacía NO apaga a nadie ────────────────────────────────
        # Si la suite contestara 200 con una lista vacía por un fallo suyo, apagar a
        # todos los clientes dejaría a la agencia entera sin reportes esa noche.
        S.subcuentas_de_la_suite = lambda timeout=15: []
        r6 = S.sincronizar(alm, origen="prueba")
        ok(r6["desactivados"] == [],
           "una suite que devuelve cero subcuentas no apaga a nadie: eso es un fallo, no una baja")
        ok((alm.cliente("viejo") or {}).get("activo") is True, "nadie se apagó")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fallos:
        print(f"{len(fallos)} fallo(s)")
        return 1
    print("todo bien")
    return 0


if __name__ == "__main__":
    sys.exit(main())
