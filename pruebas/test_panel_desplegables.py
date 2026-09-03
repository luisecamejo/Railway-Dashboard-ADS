# -*- coding: utf-8 -*-
"""
Prueba del PANEL en un navegador de verdad: los desplegables de cuentas.

    python pruebas/test_panel_desplegables.py

Por qué en navegador y no comprobando el HTML: el panel es una página de una sola pieza
con ~700 líneas de JavaScript, y lo que puede romperse no es el marcado sino el orden en
que se cargan las cosas — que el desplegable se rellene ANTES de pintar la ficha, que al
cambiar de plataforma se rehaga con la lista correcta, que lo guardado que ya no está en
la lista no se pierda. Nada de eso se ve leyendo el archivo.

El servidor está falseado: se interceptan las llamadas a /admin/* y se responde con
datos fijos. Lo que se prueba es el panel, no el servicio.

El panel se pide a `rutas_panel._html()`, que es lo que sirve el servicio de verdad:
así se prueba también que las partes de `plantillas/panel/` se concatenan bien.
"""
import asyncio, json, pathlib, sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "web"))
from app.rutas_panel import _html as panel_html          # noqa: E402
fallos = []


def ok(cond, etiqueta, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + etiqueta + (f"  {extra}" if extra else ""))
    if not cond:
        fallos.append(etiqueta)


CUENTAS = {
    "ghl": {"cuentas": [
        {"id": "LOC-LIBRE", "nombre": "Clínica Libre", "usadaPor": None},
        {"id": "LOC-USADA", "nombre": "Clínica Ocupada", "usadaPor": "otro-cliente"},
        {"id": "4sSVbJbkgof1niHlBZyY", "nombre": "AestheticsbyCliff Med SPA", "usadaPor": "cliff"},
    ], "error": None},
    "meta": {"cuentas": [
        {"id": "1003698104483915", "nombre": "Cliff · Meta", "moneda": "USD",
         "tz": "America/Denver", "activa": True, "usadaPor": "cliff"},
        {"id": "555000111", "nombre": "Cuenta Nueva", "moneda": "EUR",
         "tz": "Europe/Madrid", "activa": False, "usadaPor": None},
    ], "error": None},
    # Google roto A PROPÓSITO: es el caso que más va a pasar y el que no puede
    # dejar la ficha inservible.
    "google": {"cuentas": [], "error": "el developer token no vale"},
}

CONFIG = {"cliente": "cliff", "config": {
    "nombre": "Aesthetics by Cliff", "slug": "cliff", "tz": "America/Denver",
    "ghlLocationId": "4sSVbJbkgof1niHlBZyY",
    "cuentas": [{"plataforma": "Meta", "id": "1003698104483915"},
                # Una cuenta de Google guardada que la lista NO trae (porque falló):
                # tiene que seguir ahí después de pintar la ficha.
                {"plataforma": "Google", "id": "580-642-2100"}]}}

ESTADO = {"version": "prueba", "almacen": "ficheros",
          "visor": {"hash": "abc123", "subido": "2026-09-03T00:00:00Z", "bytes": 200000,
                    "origen": "repositorio"},
          "clientes": [{"slug": "cliff", "nombre": "Aesthetics by Cliff",
                        "tz": "America/Denver", "ghl_location_id": "4sSVbJbkgof1niHlBZyY",
                        "ultimo_snapshot": "2026-09-02T09:31:00Z", "enlaces": 1,
                        "configurado": True}]}


async def main() -> int:
    from playwright.async_api import async_playwright

    guardado = {}

    async def enruta(ruta):
        p = ruta.request.url.split("?")[0]
        def responde(datos, estado=200):
            return ruta.fulfill(status=estado, content_type="application/json",
                                body=json.dumps(datos))
        if p.endswith("/admin/estado"):      return await responde(ESTADO)
        if p.endswith("/admin/cuentas"):     return await responde(CUENTAS)
        if "/admin/config/" in p:
            if ruta.request.method == "POST":
                guardado.update(json.loads(ruta.request.post_data or "{}"))
                return await responde({"ok": True})
            return await responde(CONFIG)
        if "/admin/crudo/" in p:             return await responde({"fuentes": {}, "faltan": []})
        if "/admin/snapshots/" in p:         return await responde({"historial": []})
        if "/admin/refrescar/" in p:
            return await responde({"generado": "2026-09-02T09:31Z", "enCurso": False,
                                   "puede": True, "duracionMin": 20})
        if p.endswith("/admin/enlaces"):     return await responde({"enlaces": []})
        if "/admin/ficha" in p:
            return await responde({"tz": "Europe/Madrid", "moneda": "EUR",
                                   "nombre": "Clínica Libre", "ciudad": "Ponferrada",
                                   "pais": "ES"})
        return await responde({}, 404)

    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        pg = await b.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.route("**/admin/**", enruta)
        # El panel se sirve desde un origen de verdad para que sus fetch relativos
        # ('/admin/...') resuelvan igual que en producción y los intercepte la ruta.
        await pg.route("http://panel.local/", lambda r: r.fulfill(
            status=200, content_type="text/html; charset=utf-8",
            body=panel_html()))
        await pg.add_init_script("sessionStorage.setItem('tok_reportes','x')")
        await pg.goto("http://panel.local/", wait_until="domcontentloaded")
        await pg.wait_for_timeout(1200)

        print("\n── el alta ──────────────────────────────────────────")
        ok(len(errs) == 0, "la página carga sin errores de JS", "; ".join(errs[:2]))
        ops = await pg.eval_on_selector_all("#cloc option", "e=>e.map(o=>o.textContent)")
        ok(any("Clínica Libre" in o for o in ops), "el desplegable trae los nombres del CRM")
        ok(any("LOC-LIBRE" in o for o in ops), "y el identificador al lado")
        ok(any("YA EN otro-cliente" in o for o in ops),
           "y marca las sub-cuentas que ya usa otro cliente")
        ok(any("escribir el id a mano" in o for o in ops),
           "y deja siempre la salida de escribir el id a mano")

        # Elegir una sub-cuenta tiene que disparar la lectura del CRM
        await pg.select_option("#cloc", "LOC-LIBRE")
        await pg.wait_for_timeout(900)
        ficha = await pg.inner_text("#cficha")
        ok("Europe/Madrid" in ficha, "al elegirla, se lee su zona del CRM", ficha[:60])
        ok("EUR" in ficha, "y su moneda")
        ok((await pg.input_value("#cnom")) == "Clínica Libre",
           "y rellena el nombre, que es lo que había que teclear")

        # La salida a mano
        await pg.select_option("#cloc", "__a_mano__")
        await pg.wait_for_timeout(200)
        ok(await pg.is_visible("#clocman"), "«a mano» descubre la casilla de texto")

        print("\n── la ficha del cliente ──────────────────────────────────")
        await pg.click(".cli > summary")
        await pg.wait_for_timeout(900)
        ok(len(errs) == 0, "abrir la ficha no rompe nada", "; ".join(errs[:2]))

        loc = await pg.eval_on_selector(".klocsel", "e=>e.value")
        ok(loc == "4sSVbJbkgof1niHlBZyY", "el location guardado sale elegido", loc)

        filas = await pg.eval_on_selector_all(
            ".cuenta", "es=>es.map(f=>[f.querySelector('.kplat').value,"
                       "f.querySelector('.kidsel').value])")
        ok(filas == [["Meta", "1003698104483915"], ["Google", "580-642-2100"]],
           "las dos cuentas guardadas salen elegidas", str(filas))

        gtxt = await pg.eval_on_selector_all(
            ".cuenta:nth-child(2) .kidsel option", "e=>e.map(o=>o.textContent)")
        ok(any("no está en la lista" in o for o in gtxt),
           "la de Google, que la lista NO trae, se marca pero NO se pierde")
        nota = await pg.inner_text(".klistas")
        ok("developer token" in nota, "y se explica por qué falta esa lista", nota[:70])

        # Cambiar de plataforma tiene que rehacer el desplegable con la otra lista
        await pg.select_option(".cuenta:nth-child(1) .kplat", "Google")
        await pg.wait_for_timeout(200)
        ops2 = await pg.eval_on_selector_all(
            ".cuenta:nth-child(1) .kidsel option", "e=>e.map(o=>o.textContent)")
        ok(not any("Cliff · Meta" in o for o in ops2),
           "al pasar a Google ya no ofrece cuentas de Meta")
        await pg.select_option(".cuenta:nth-child(1) .kplat", "Meta")
        await pg.wait_for_timeout(200)
        ops3 = await pg.eval_on_selector_all(
            ".cuenta:nth-child(1) .kidsel option", "e=>e.map(o=>o.textContent)")
        ok(any("Cuenta Nueva" in o and "pausada" in o for o in ops3),
           "y una cuenta pausada se ofrece igual, marcada")

        print("\n── lo que se guarda ────────────────────────────────────")
        await pg.select_option(".cuenta:nth-child(1) .kidsel", "555000111")
        await pg.click(".kguardar")
        await pg.wait_for_timeout(600)
        ok(guardado.get("ghlLocationId") == "4sSVbJbkgof1niHlBZyY",
           "se manda el location elegido", str(guardado.get("ghlLocationId")))
        ids = [c["id"] for c in (guardado.get("cuentas") or [])]
        ok("555000111" in ids, "se manda el id de la cuenta elegida en el desplegable", str(ids))
        ok("580-642-2100" in ids,
           "y NO se pierde la de Google que no estaba en la lista", str(ids))
        ok(len(errs) == 0, "cero errores de JS en toda la prueba", "; ".join(errs[:3]))

        await b.close()

    print()
    if fallos:
        print(f"FALLOS ({len(fallos)}): " + " · ".join(fallos))
        return 1
    print("todo bien")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
