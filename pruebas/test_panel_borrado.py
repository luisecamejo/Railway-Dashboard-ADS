# -*- coding: utf-8 -*-
"""
Prueba en navegador de la zona de peligro del panel: dar de baja un cliente.

    python pruebas/test_panel_borrado.py

El servidor está falseado. Lo que se comprueba es lo único que puede fallar aquí y no
se ve leyendo el archivo: que el botón de borrar NO se pueda pulsar hasta que el
identificador esté escrito exacto, que con una extracción en marcha no se ofrezca
siquiera, y que la petición que sale lleve el `confirmar` correcto.

`enCurso` se prueba con un segundo cliente en vez de recargando la página: así queda
claro que el estado es POR CLIENTE y no una bandera global del panel.

El panel se pide a `rutas_panel._html()`, que es lo que sirve el servicio de verdad.
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


BORRADO = {
    "cliff": {"cliente": "cliff", "nombre": "Aesthetics by Cliff", "enCurso": False,
              "snapshots": 12, "enlaces": 3, "enlacesActivos": 2, "crudos": 3,
              "accesos": 47, "leads": 1083},
    "ocupado": {"cliente": "ocupado", "nombre": "El Ocupado", "enCurso": True,
                "snapshots": 1, "enlaces": 0, "enlacesActivos": 0, "crudos": 1,
                "accesos": 0, "leads": 5},
}

GENERAL = {
    "cliff": {"cliente": "cliff", "token": "TOKENGENERAL", "hayReporte": True,
              "ruta": "/r/TOKENGENERAL/", "rutaEmbed": "/r/TOKENGENERAL/embed",
              "dominios": ["https://app.gohighlevel.com"], "modo": "cliente",
              "accesos": 412, "creado": "2026-08-01T10:00:00Z"},
    # El ocupado NO tiene general: la vista previa tiene que decirlo sin inventárselo.
    "ocupado": {"cliente": "ocupado", "token": None, "hayReporte": False},
}

CUENTAS = {"ghl": {"cuentas": [], "error": None},
           "meta": {"cuentas": [], "error": None},
           "google": {"cuentas": [], "error": None}}

ESTADO = {"version": "prueba", "almacen": "ficheros",
          "visor": {"hash": "abc123", "subido": "2026-09-03T00:00:00Z", "bytes": 200000},
          "clientes": [
              {"slug": "cliff", "nombre": "Aesthetics by Cliff", "tz": "America/Denver",
               "ultimo_snapshot": "2026-09-02T09:31:00Z", "enlaces": 2, "configurado": True},
              {"slug": "ocupado", "nombre": "El Ocupado", "tz": "America/Denver",
               "ultimo_snapshot": None, "enlaces": 0, "configurado": True}]}


async def main() -> int:
    from playwright.async_api import async_playwright

    borrados = []

    async def enruta(ruta):
        url = ruta.request.url
        p = url.split("?")[0]
        def responde(datos, estado=200):
            return ruta.fulfill(status=estado, content_type="application/json",
                                body=json.dumps(datos))
        if ruta.request.method == "DELETE":
            borrados.append(url)
            return await responde({"ok": True, "snapshots": 12, "enlaces": 3})
        if p.endswith("/borrado"):
            slug = p.split("/admin/clientes/")[1].split("/")[0]
            return await responde(BORRADO.get(slug) or {}, 200 if slug in BORRADO else 404)
        if "/admin/enlaces/general/" in p:
            slug = p.split("/admin/enlaces/general/")[1].split("/")[0]
            return await responde(GENERAL.get(slug) or {"token": None})
        if p.endswith("/admin/estado"):   return await responde(ESTADO)
        if p.endswith("/admin/cuentas"):  return await responde(CUENTAS)
        if "/admin/config/" in p:         return await responde({"config": {}})
        if "/admin/crudo/" in p:          return await responde({"fuentes": {}, "faltan": []})
        if "/admin/snapshots/" in p:      return await responde({"historial": []})
        if "/admin/refrescar/" in p:
            return await responde({"generado": None, "enCurso": False, "duracionMin": 20})
        if p.endswith("/admin/enlaces"):  return await responde({"enlaces": []})
        return await responde({}, 404)

    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        pg = await b.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.route("**/admin/**", enruta)
        await pg.route("http://panel.local/", lambda r: r.fulfill(
            status=200, content_type="text/html; charset=utf-8",
            body=panel_html()))
        await pg.add_init_script("sessionStorage.setItem('tok_reportes','x')")
        await pg.goto("http://panel.local/", wait_until="domcontentloaded")
        await pg.wait_for_timeout(900)

        CLIFF = ".cli[data-slug=cliff]"
        OCUP = ".cli[data-slug=ocupado]"

        print("\n── la vista previa dice qué se destruye ────────────────────")
        await pg.click(CLIFF + " > summary")
        await pg.wait_for_timeout(700)
        ok(len(errs) == 0, "abrir la ficha no rompe nada", "; ".join(errs[:2]))
        ok(not await pg.is_visible(CLIFF + " .cuenta-atras"),
           "la zona de peligro empieza cerrada: no hay nada que pulsar sin querer")

        await pg.click(CLIFF + " .bbaja")
        await pg.wait_for_timeout(500)
        txt = await pg.inner_text(CLIFF + " .cuenta-atras")
        ok("12" in txt, "cuenta los reportes publicados", txt.split("\n")[1][:50] if "\n" in txt else "")
        ok("1083" in txt, "y las oportunidades del último")
        # Lo que más importa de la vista previa: que nombre el ENLACE GENERAL aparte. De
        # los enlaces activos es el único que sabemos seguro que está empotrado en la web
        # del cliente, así que un «si alguno está empotrado» se queda corto.
        ok("ENLACE GENERAL" in txt, "nombra el enlace general, en mayúsculas y aparte")
        ok("GoHighLevel" in txt,
           "y dice dónde está empotrado, que es la consecuencia concreta")
        ok("pierde el acceso" in txt, "y qué pasa: el cliente pierde el acceso")
        ok("412" in txt, "con las visitas que lleva ESE enlace")
        ok(await pg.eval_on_selector_all(
               CLIFF + " .cuenta-atras .grave", "e=>e.length") >= 1,
           "y va en rojo, no en la lista gris")
        ok("2" in txt, "sigue contando los enlaces activos en total")
        ok("47" in txt, "con las visitas acumuladas de todos")
        ok("revocado" in txt, "los ya revocados se cuentan aparte")
        ok(len(borrados) == 0, "ver la vista previa NO borra nada")

        print("\n── el botón no se puede pulsar sin escribir el slug ───────")
        ok(await pg.is_disabled(CLIFF + " .bya"), "arranca desactivado")
        await pg.fill(CLIFF + " .bconf", "cliff-")
        await pg.wait_for_timeout(120)
        ok(await pg.is_disabled(CLIFF + " .bya"), "con el identificador incompleto, sigue")
        await pg.fill(CLIFF + " .bconf", "Cliff")
        await pg.wait_for_timeout(120)
        ok(await pg.is_disabled(CLIFF + " .bya"), "y con otra caja de letras, también")
        await pg.fill(CLIFF + " .bconf", "cliff")
        await pg.wait_for_timeout(120)
        ok(not await pg.is_disabled(CLIFF + " .bya"), "solo se activa con el slug exacto")

        print("\n── con una extracción en marcha no se ofrece ──────────────")
        await pg.click(OCUP + " > summary")
        await pg.wait_for_timeout(700)
        await pg.click(OCUP + " .bbaja")
        await pg.wait_for_timeout(500)
        t2 = await pg.inner_text(OCUP + " .cuenta-atras")
        ok("extracción en marcha" in t2, "lo dice claro", t2[-90:].replace("\n", " "))
        ok("no hay enlace general" in t2,
           "y en un cliente sin general lo dice, en vez de callarse")
        ok(await pg.eval_on_selector_all(OCUP + " .bya", "e=>e.length") == 0,
           "y NO pinta el botón de borrar: no hay nada que pulsar")

        print("\n── lo que se manda al borrar ────────────────────────────")
        await pg.click(CLIFF + " .bya")
        await pg.wait_for_timeout(700)
        ok(len(borrados) == 1, "sale UNA petición de borrado", str(len(borrados)))
        ok("/admin/clientes/cliff?" in borrados[0], "contra el cliente correcto", borrados[0][-60:])
        ok("confirmar=cliff" in borrados[0], "y con el confirmar exacto")
        aviso = await pg.inner_text("#mcli")
        ok("dado de baja" in aviso,
           "el aviso se pone ARRIBA, porque la tarjeta desaparece", aviso[:60])
        ok("general incluido" in aviso,
           "y dice que el enlace general se fue con él", aviso[-80:])
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
