# -*- coding: utf-8 -*-
"""
Prueba del espinazo de la Fase 1: trozos crudos → construir → validar → publicar.

    python pruebas/test_fase1.py                      # solo lo que no necesita datos
    python pruebas/test_fase1.py --crudos CARPETA --esperado DATA.json

La carpeta debe tener config.json y crudo_{ghl,meta,google}.json. No están en el
repositorio porque llevan datos de pacientes.

Lo que de verdad demuestra, con --esperado: que el camino nuevo (tres extractores
independientes que dejan su trozo, y el servicio que junta y publica) produce EL MISMO
snapshot que el script hecho a mano para el primer cliente. Es lo que impide que
automatizar la extracción cambie los números en silencio.
"""
import argparse, copy, json, os, pathlib, shutil, sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
fallos = []


def ok(cond, etiqueta, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + etiqueta + (f"  {extra}" if extra else ""))
    if not cond:
        fallos.append(etiqueta)


def cliente_de_pruebas(tmp: pathlib.Path):
    os.environ["ADMIN_TOKEN"] = "T" * 43
    os.environ["RUTA_DATOS_LOCAL"] = str(tmp)
    os.environ.pop("DATABASE_URL", None)
    sys.path.insert(0, str(RAIZ / "web"))
    from app.main import app, almacen                      # noqa: E402
    from fastapi.testclient import TestClient              # noqa: E402
    import logging
    logging.disable(logging.INFO)
    return TestClient(app), almacen, {"X-Admin-Token": os.environ["ADMIN_TOKEN"]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--crudos")
    p.add_argument("--esperado")
    a = p.parse_args()

    tmp = pathlib.Path("/tmp/_pruebas_fase1")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    c, almacen, H = cliente_de_pruebas(tmp)
    from app.rutas_extraccion import comprobar_crudo        # noqa: E402

    print("\n== comprobaciones de un trozo crudo (sin datos reales) ==")
    ok(comprobar_crudo("ghl", {"oportunidades": [], "pipelines": []}),
       "un trozo de ghl sin oportunidades se rechaza")
    ok(comprobar_crudo("ghl", {"oportunidades": [{"oid": "a", "created": 1}, {"oid": "a", "created": 1}],
                               "pipelines": [{"n": "p", "stages": [{"id": "s"}]}]}),
       "oportunidades duplicadas se rechazan (bug del cursor de GHL)")
    ok(not comprobar_crudo("ghl", {"oportunidades": [{"oid": "a", "created": 1}],
                                   "pipelines": [{"n": "p", "stages": [{"id": "s"}]}]}),
       "un trozo de ghl mínimo y correcto pasa")
    ok(comprobar_crudo("meta", {"gastoDiario": [{"fecha": "2026-01-01", "red": "Facebook"}]}),
       "gasto de meta con red equivocada se rechaza")
    ok(comprobar_crudo("meta", {"gastoDiario": [{"fecha": "2026-01-01", "red": "Meta",
                                                "hasta": "2026-01-07"}]}),
       "una fila de gasto que cubre 7 días se rechaza")
    ok(not comprobar_crudo("meta", {"gastoDiario": []}),
       "un día sin gasto no es un error")
    ok(comprobar_crudo("google", {"gastoDiario": [{"fecha": "2026-01-01", "red": "Meta"}]}),
       "gasto de google declarado como Meta se rechaza")

    print("\n== rutas, sin autenticación ==")
    ok(c.post("/admin/crudo/x/ghl", content=b"{}").status_code in (401, 403),
       "dejar un trozo sin token está prohibido")
    ok(c.post("/admin/construir/x", json={}).status_code in (401, 403),
       "construir sin token está prohibido")
    ok(c.get("/admin/config/x").status_code in (401, 403),
       "leer la configuración sin token está prohibido")

    if not a.crudos:
        print("\n  · el resto se salta: no se pasó --crudos")
        print("\n" + (f"FALLOS: {fallos}" if fallos else "todo OK"))
        sys.exit(1 if fallos else 0)

    fx = pathlib.Path(a.crudos)
    cfg = json.loads((fx / "config.json").read_text(encoding="utf-8"))
    SLUG = cfg.get("slug") or "cliente"

    print("\n== flujo completo ==")
    r = c.post("/admin/clientes", headers=H,
               json={"slug": SLUG, "nombre": cfg["nombre"], "tz": cfg["tz"]})
    ok(r.status_code == 200, "cliente creado")
    ok(c.post(f"/admin/config/{SLUG}", headers=H, json=cfg).status_code == 200,
       "configuración guardada")
    ok(c.post(f"/admin/config/{SLUG}", headers=H, json={"nombre": "X"}).status_code == 400,
       "una configuración sin zona horaria se rechaza")
    r = c.post(f"/admin/construir/{SLUG}", headers=H, json={})
    ok(r.status_code == 409, "construir sin el trozo del CRM da un 409 que lo explica")

    for fuente in ("ghl", "meta", "google"):
        ruta = fx / f"crudo_{fuente}.json"
        if not ruta.exists():
            continue
        r = c.post(f"/admin/crudo/{SLUG}/{fuente}", headers=H, content=ruta.read_bytes())
        ok(r.status_code == 200, f"trozo de {fuente} aceptado",
           r.json().get("resumen", "")[:90] if r.status_code == 200 else r.text[:150])
    ok(c.post(f"/admin/crudo/{SLUG}/windsor", headers=H, content=b"{}").status_code == 400,
       "una fuente que no existe se rechaza")
    est = c.get(f"/admin/crudo/{SLUG}", headers=H).json()
    ok("ghl" in est["fuentes"], "el estado lista los trozos recibidos", est["faltan"])

    r = c.post(f"/admin/construir/{SLUG}", headers=H, json={"publicar": False})
    ok(r.status_code == 200 and r.json()["publicado"] is False,
       "ensayo en seco sin publicar")
    r = c.post(f"/admin/construir/{SLUG}", headers=H, json={})
    ok(r.status_code == 200 and r.json()["publicado"] is True, "construido y publicado",
       r.text[:200] if r.status_code != 200 else "")
    if r.status_code == 200:
        print("    " + (r.json()["resumen"] or "").replace("\n", "\n    ").strip())
        for av in r.json().get("avisos") or []:
            print("    aviso:", av[:120])

    tok = c.post("/admin/enlaces", headers=H,
                 json={"cliente": SLUG, "modo": "cliente"}).json()["token"]
    ok(c.get(f"/r/{tok}/").status_code == 200, "el visor se sirve")
    ok(c.get(f"/r/{tok}/snapshot.json").status_code == 200, "el snapshot se sirve")

    if a.esperado:
        print("\n== contra el snapshot de referencia ==")
        nuevo = almacen.snapshot(SLUG)
        viejo = json.loads(pathlib.Path(a.esperado).read_text(encoding="utf-8"))

        def limpia(d):
            d = copy.deepcopy(d)
            d.pop("generado", None)
            for l in d.get("leads", []):
                l.pop("de", None)
            # el orden de las llamadas depende de en qué orden respondió la API
            d["calls"] = sorted(d.get("calls") or [],
                                key=lambda x: (x["t"], str(x["c"]), x["d"], str(x["dur"])))
            return d

        x, y = limpia(viejo), limpia(nuevo)
        for clave in sorted(set(x) | set(y)):
            if clave in ("callWin", "sp", "calls"):
                continue        # la ventana de llamadas se amplió a propósito (H-3)
            ok(x.get(clave) == y.get(clave), f"{clave} idéntico")
        ok(len(y["calls"]) >= len(x["calls"]),
           "hay al menos tantas llamadas como antes (la ventana solo se amplía)",
           f"{len(x['calls'])} → {len(y['calls'])}")

    print("\n" + (f"FALLOS: {fallos}" if fallos else "todo OK"))
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
