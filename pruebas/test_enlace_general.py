# -*- coding: utf-8 -*-
"""
Prueba del enlace general: que nazca solo, que sea SIEMPRE el mismo, y que no se apague.

    python pruebas/test_enlace_general.py

Lo que hay que demostrar aquí no es que se cree un enlace: es que **no cambia**. El
enlace general se pega una vez en GoHighLevel, así que cada cosa que podría cambiarlo
sin que nadie se dé cuenta es un fallo de los caros — el iframe de la web del cliente se
queda en blanco y el primero que lo ve es el cliente.

Así que se comprueba, en este orden:

  1. Nace con el primer reporte y no antes (un enlace sin datos parece roto).
  2. Publicar otra vez NO lo cambia. Ni cambiar las cuentas de Meta o de Google.
  3. Lo de dentro SÍ cambia: el snapshot nuevo y el visor nuevo llegan por el mismo
     token. Esto es lo que hace que no haya que regenerarlo.
  4. Nace empotrable en GoHighLevel, sin que nadie pegue los dominios.
  5. No se puede revocar por accidente, y rotarlo cuesta el identificador escrito.
"""
import os, pathlib, shutil, sys, tempfile

RAIZ = pathlib.Path(__file__).resolve().parent.parent
fallos = []


def ok(cond, etiqueta, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + etiqueta + (f"  {extra}" if extra else ""))
    if not cond:
        fallos.append(etiqueta)


def snap(leads=3, gasto=100.0) -> dict:
    return {"cliente": {"nombre": "La Clínica", "tz": "America/Denver"},
            "tz": "America/Denver", "desde": "2026-08-01", "hasta": "2026-08-31",
            "moneda": "USD",
            "leads": [{"id": f"L{i}", "n": f"Paciente {i}"} for i in range(leads)],
            "camps": [{"n": "Camp", "gasto": gasto}], "sp": {}}


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="general-"))
    os.environ["RUTA_DATOS_LOCAL"] = str(tmp)
    os.environ["ADMIN_TOKEN"] = "token-de-prueba"
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("DOMINIOS_EMPOTRADO", None)
    sys.path.insert(0, str(RAIZ / "web"))

    from fastapi.testclient import TestClient
    from app.main import app, almacen

    c = TestClient(app)
    H = {"X-Admin-Token": "token-de-prueba"}

    almacen.guardar_cliente("clinica", "La Clínica", "LOC-1", "America/Denver", None)
    almacen.guardar_config("clinica", {"nombre": "La Clínica", "slug": "clinica",
                                       "tz": "America/Denver", "ghlLocationId": "LOC-1",
                                       "cuentas": [{"plataforma": "Meta", "id": "111222333"}]})

    print("\n── sin reporte no hay enlace general ──────────────────────")
    r = c.get("/admin/enlaces/general/clinica", headers=H)
    ok(r.status_code == 200, "la consulta contesta igual", r.status_code)
    ok(r.json().get("token") is None, "y dice que todavía no hay ninguno")
    ok(r.json().get("hayReporte") is False,
       "y dice POR QUÉ: no hay reporte (es lo que el panel necesita para no crearlo)")
    r = c.post("/admin/enlaces/general/clinica", headers=H)
    ok(r.status_code == 409, "crearlo a la fuerza → 409, no un enlace muerto", r.status_code)

    print("\n── nace con el primer reporte, y ya empotrable ────────────")
    almacen.publicar_snapshot("clinica", snap())
    from app import enlace_general
    enlace_general.tras_publicar(almacen, "clinica")     # el gancho de la publicación

    r = c.get("/admin/enlaces/general/clinica", headers=H)
    g = r.json()
    tok = g.get("token")
    ok(bool(tok), "existe sin que nadie lo cree a mano", str(tok)[:12] + "…" if tok else "")
    ok(g.get("modo") == "cliente",
       "en modo cliente: hoy interno y cliente ven lo mismo, así que sirve para los dos")
    doms = g.get("dominios") or []
    ok(any("gohighlevel" in d for d in doms) and any("msgsndr" in d for d in doms),
       "y con los dominios de GoHighLevel puestos ya", str(len(doms)) + " dominios")
    ok(g.get("rutaEmbed") == f"/r/{tok}/embed",
       "así que trae ruta de iframe, que es para lo que se pide", str(g.get("rutaEmbed")))

    print("\n── EL MISMO, pase lo que pase ───────────────────────────")
    for _ in range(3):
        enlace_general.tras_publicar(almacen, "clinica")
    ok(c.post("/admin/enlaces/general/clinica", headers=H).json().get("token") == tok,
       "pedirlo otra vez devuelve el mismo, no uno nuevo")
    almacen.publicar_snapshot("clinica", snap(leads=9))
    enlace_general.tras_publicar(almacen, "clinica")
    ok(c.get("/admin/enlaces/general/clinica", headers=H).json().get("token") == tok,
       "publicar otro reporte NO lo cambia")
    almacen.guardar_config("clinica", {"nombre": "La Clínica", "slug": "clinica",
                                       "tz": "America/Denver", "ghlLocationId": "LOC-1",
                                       "cuentas": [{"plataforma": "Meta", "id": "111222333"},
                                                   {"plataforma": "Google", "id": "580-642-2100"}]})
    enlace_general.tras_publicar(almacen, "clinica")
    ok(c.get("/admin/enlaces/general/clinica", headers=H).json().get("token") == tok,
       "añadir una cuenta de Google tampoco")
    ok(len([e for e in almacen.enlaces("clinica")
            if e.get("general") and not e.get("revocado")]) == 1,
       "y sigue habiendo UNO: dos serían dos iframes y nadie sabría cuál está pegado")

    print("\n── lo de DENTRO sí se actualiza, por el mismo enlace ──────")
    d = c.get(f"/r/{tok}/snapshot.json").json()
    ok(len(d.get("leads") or []) == 9,
       "el mismo token sirve el ÚLTIMO snapshot, no el de cuando se creó",
       f"{len(d.get('leads') or [])} leads")
    almacen.publicar_snapshot("clinica", snap(leads=12, gasto=999.0))
    d2 = c.get(f"/r/{tok}/snapshot.json").json()
    ok(len(d2.get("leads") or []) == 12, "y se sigue actualizando sin tocar el enlace")
    # El visor servido se cachea en memoria a propósito (se sirve en cada visita de
    # reporte). Los dos caminos por los que cambia el diseño lo refrescan: un despliegue
    # arranca el proceso de cero y `POST /admin/visor` fuerza la recarga. Aquí se usa el
    # segundo, que es el camino de verdad, para que la prueba no dé por bueno un cambio
    # que en producción se habría quedado en la caché.
    almacen.guardar_visor("<html>DISEÑO NUEVO ./snapshot.json</html>", "x=1", "hash-nuevo")
    from app.main import _cargar_visor
    _cargar_visor(forzar=True)
    r = c.get(f"/r/{tok}/")
    ok("DISEÑO NUEVO" in r.text,
       "y un cambio de diseño del dashboard llega al enlace que ya está pegado")
    r = c.get(f"/r/{tok}/embed")
    ok(r.status_code == 200 and "DISEÑO NUEVO" in r.text, "también por la ruta del iframe")

    print("\n── se puede empotrar donde toca, y solo ahí ─────────────────")
    fa = c.get(f"/r/{tok}/embed").headers.get("content-security-policy", "")
    ok("frame-ancestors" in fa and "gohighlevel.com" in fa,
       "la cabecera autoriza a GoHighLevel", fa[fa.find("frame-ancestors"):][:60])
    ok("otraweb.com" not in fa, "y a nadie más")

    print("\n── no se apaga por accidente ────────────────────────────")
    r = c.post(f"/admin/enlaces/{tok}/revocar", headers=H)
    ok(r.status_code == 409, "revocarlo → 409: es el que está empotrado", r.status_code)
    ok("rotar" in (r.json().get("detalle") or r.text).lower(),
       "y el mensaje dice qué hacer en su lugar")
    ok(almacen.enlace(tok) and not almacen.enlace(tok).get("revocado"), "sigue vivo")

    print("\n── rotarlo cuesta el identificador escrito ────────────────────")
    ok(c.post("/admin/enlaces/general/clinica/rotar", headers=H).status_code == 400,
       "sin confirmar → 400")
    ok(c.post("/admin/enlaces/general/clinica/rotar?confirmar=clinic",
              headers=H).status_code == 400, "mal escrito → 400")
    ok(c.get("/admin/enlaces/general/clinica", headers=H).json().get("token") == tok,
       "y tras los dos intentos sigue siendo el mismo")

    r = c.post("/admin/enlaces/general/clinica/rotar?confirmar=clinica", headers=H)
    ok(r.status_code == 200, "con el identificador exacto, rota", r.status_code)
    nuevo = r.json().get("token")
    ok(nuevo and nuevo != tok, "y el token es otro")
    ok(almacen.enlace(tok).get("revocado") in (True, "True"), "el anterior queda revocado")
    ok(c.get(f"/r/{tok}/").status_code in (403, 404, 410),
       "y ya no abre", c.get(f"/r/{tok}/").status_code)
    ok(c.get(f"/r/{nuevo}/").status_code == 200, "el nuevo sí")
    ok(len([e for e in almacen.enlaces("clinica")
            if e.get("general") and not e.get("revocado")]) == 1,
       "y otra vez hay exactamente uno vivo")

    print("\n── los adicionales siguen funcionando igual ───────────────────")
    r = c.post("/admin/enlaces", headers=H,
               json={"cliente": "clinica", "modo": "demo", "nota": "caso de éxito"})
    ok(r.status_code == 200, "se puede crear un demo aparte", r.status_code)
    demo = r.json().get("token")
    ok(c.post(f"/admin/enlaces/{demo}/revocar", headers=H).status_code == 200,
       "y ese SÍ se revoca, que es para lo que están")
    ok(c.get("/admin/enlaces/general/clinica", headers=H).json().get("token") == nuevo,
       "sin tocar el general")

    print("\n── el borrado del cliente se lo lleva también ─────────────────")
    r = c.delete("/admin/clientes/clinica?confirmar=clinica", headers=H)
    ok(r.status_code == 200, "dar de baja al cliente funciona igual", r.status_code)
    ok(almacen.enlace(nuevo) is None, "y el enlace general ya no existe")
    ok(c.get(f"/r/{nuevo}/").status_code in (403, 404, 410), "ni abre")

    print("\n── sigue protegido por el token ───────────────────────────")
    ok(c.get("/admin/enlaces/general/clinica").status_code == 401, "consultar sin token, 401")
    ok(c.post("/admin/enlaces/general/clinica").status_code == 401, "crear sin token, 401")
    ok(c.post("/admin/enlaces/general/clinica/rotar?confirmar=clinica").status_code == 401,
       "rotar sin token, 401")

    print()
    shutil.rmtree(tmp, ignore_errors=True)
    if fallos:
        print(f"FALLOS ({len(fallos)}): " + " · ".join(fallos))
        return 1
    print("todo bien")
    return 0


if __name__ == "__main__":
    sys.exit(main())
