# -*- coding: utf-8 -*-
"""
Prueba en navegador del bloque «Enlace general» del panel.

    python pruebas/test_panel_general.py

Lo que se comprueba aquí es lo que no se ve leyendo el archivo:

  · Que al abrir la ficha de un cliente que YA tiene reporte pero todavía no tiene
    enlace general, el panel lo crea él solo. Es la mitad de «que se cree automático»:
    la otra mitad la hace el servidor al publicar, y esta es la que cubre a los clientes
    que ya estaban dados de alta antes.
  · Que el enlace general NO sale en la tabla de abajo. Si saliera, tendría un «revocar»
    que el servidor rechaza con un 409, y eso parece un fallo del panel.
  · Que sin reporte no se pide crearlo: se explica y se calla.

El servidor está falseado; lo que se prueba es el panel.
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


DOMS = ["https://app.gohighlevel.com", "https://*.gohighlevel.com",
        "https://*.msgsndr.com", "https://*.leadconnectorhq.com"]

# 'conreporte' tiene datos y NO tiene enlace general: el panel debe crearlo al abrir.
# 'sinreporte' no tiene datos: no debe pedir nada.
GENERAL = {
    "conreporte": {"cliente": "conreporte", "token": None, "hayReporte": True},
    "yatiene": {"cliente": "yatiene", "token": "TOKENVIEJO", "hayReporte": True,
                "ruta": "/r/TOKENVIEJO/", "rutaEmbed": "/r/TOKENVIEJO/embed",
                "dominios": DOMS, "modo": "cliente", "accesos": 412,
                "creado": "2026-08-01T10:00:00Z"},
    "sinreporte": {"cliente": "sinreporte", "token": None, "hayReporte": False},
}

CUENTAS = {"ghl": {"cuentas": [], "error": None},
           "meta": {"cuentas": [], "error": None},
           "google": {"cuentas": [], "error": None}}

ESTADO = {"version": "prueba", "almacen": "ficheros",
          "visor": {"hash": "abc123", "subido": "2026-09-03T00:00:00Z", "bytes": 200000},
          "clientes": [
              {"slug": "conreporte", "nombre": "Con Reporte", "tz": "America/Denver",
               "ultimo_snapshot": "2026-09-02T09:31:00Z", "enlaces": 0, "configurado": True},
              {"slug": "yatiene", "nombre": "Ya Tiene", "tz": "America/Denver",
               "ultimo_snapshot": "2026-09-02T09:31:00Z", "enlaces": 2, "configurado": True},
              {"slug": "sinreporte", "nombre": "Sin Reporte", "tz": "America/Denver",
               "ultimo_snapshot": None, "enlaces": 0, "configurado": True}]}

# La tabla de abajo recibe el general Y un adicional: el general tiene que desaparecer
# de ahí, porque ya se pinta arriba.
ENLACES = {"enlaces": [
    {"token": "TOKENVIEJO", "cliente": "yatiene", "modo": "cliente", "nota": "general",
     "revocado": "False", "creado": "2026-08-01T10:00:00Z", "accesos": "412",
     "dominios": DOMS, "general": True},
    {"token": "TOKENDEMO", "cliente": "yatiene", "modo": "demo", "nota": "caso de éxito",
     "revocado": "False", "creado": "2026-08-20T10:00:00Z", "accesos": "3",
     "dominios": [], "general": False}]}


async def main() -> int:
    from playwright.async_api import async_playwright

    posts = []

    async def enruta(ruta):
        url = ruta.request.url
        p = url.split("?")[0]
        def responde(datos, estado=200):
            return ruta.fulfill(status=estado, content_type="application/json",
                                body=json.dumps(datos))
        if "/admin/enlaces/general/" in p:
            slug = p.split("/admin/enlaces/general/")[1].split("/")[0]
            if ruta.request.method == "POST":
                posts.append(url)
                if p.endswith("/rotar"):
                    return await responde({"ok": True, "token": "TOKENNUEVO",
                                           "ruta": "/r/TOKENNUEVO/",
                                           "rutaEmbed": "/r/TOKENNUEVO/embed",
                                           "dominios": DOMS, "modo": "cliente",
                                           "accesos": 0, "creado": "2026-09-03T12:00:00Z"})
                # Crear: contesta como el servidor, con el enlace ya hecho.
                return await responde({"ok": True, "nuevo": True, "token": "TOKENFRESCO",
                                       "ruta": "/r/TOKENFRESCO/",
                                       "rutaEmbed": "/r/TOKENFRESCO/embed",
                                       "dominios": DOMS, "modo": "cliente",
                                       "accesos": 0, "creado": "2026-09-03T12:00:00Z"})
            return await responde(GENERAL.get(slug) or {"token": None, "hayReporte": False})
        if p.endswith("/admin/estado"):   return await responde(ESTADO)
        if p.endswith("/admin/cuentas"):  return await responde(CUENTAS)
        if "/admin/config/" in p:         return await responde({"config": {}})
        if "/admin/crudo/" in p:          return await responde({"fuentes": {}, "faltan": []})
        if "/admin/snapshots/" in p:      return await responde({"historial": []})
        if "/admin/refrescar/" in p:
            return await responde({"generado": "2026-09-02T09:31Z", "enCurso": False,
                                   "duracionMin": 20})
        if p.endswith("/admin/enlaces"):
            cli = url.split("cliente=")[-1] if "cliente=" in url else ""
            return await responde(ENLACES if cli == "yatiene" else {"enlaces": []})
        if "/borrado" in p:               return await responde({}, 404)
        return await responde({}, 404)

    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        pg = await b.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.route("**/admin/**", enruta)
        await pg.route("http://panel.local/", lambda r: r.fulfill(
            status=200, content_type="text/html; charset=utf-8", body=panel_html()))
        await pg.add_init_script("sessionStorage.setItem('tok_reportes','x')")
        await pg.goto("http://panel.local/", wait_until="domcontentloaded")
        await pg.wait_for_timeout(900)

        CON = ".cli[data-slug=conreporte]"
        YA = ".cli[data-slug=yatiene]"
        SIN = ".cli[data-slug=sinreporte]"

        print("\n── el que ya lo tiene: se pinta, no se recrea ─────────────────")
        await pg.click(YA + " > summary")
        await pg.wait_for_timeout(900)
        ok(len(errs) == 0, "abrir la ficha no rompe nada", "; ".join(errs[:2]))
        g = await pg.inner_text(YA + " .general")
        ok("/r/TOKENVIEJO/" in (await pg.input_value(YA + " .gurl")),
           "sale la URL del enlace que ya existe",
           await pg.input_value(YA + " .gurl"))
        ok(len([u for u in posts if "yatiene" in u]) == 0,
           "y NO se pide crear otro: ya hay uno", str(len(posts)))
        ok("app.gohighlevel.com" in g, "se ven los dominios donde se puede empotrar")
        ok("412 visitas" in g, "y las visitas que lleva", g[-70:].replace("\n", " "))
        ok(await pg.eval_on_selector_all(YA + " .gifr", "e=>e.length") == 1,
           "y hay botón de copiar iframe, porque tiene dominios")

        print("\n── el general NO sale en la tabla de abajo ────────────────────")
        filas = await pg.eval_on_selector_all(
            YA + " .tenlaces tbody tr", "es=>es.map(t=>t.textContent)")
        ok(len(filas) == 1, "solo queda el adicional en la tabla", str(len(filas)))
        ok("demo" in filas[0], "que es el demo", filas[0][:40])
        ok(not any("TOKENVIEJO" in f for f in filas),
           "el general no aparece: así no hay un «revocar» que el servidor rechaza")

        print("\n── el iframe que se copia apunta a /embed ─────────────────────")
        await pg.click(YA + " .gifr")
        await pg.wait_for_timeout(300)
        code = await pg.inner_text(YA + " .snip")
        ok("/r/TOKENVIEJO/embed" in code, "el snippet lleva la ruta de empotrar", code[:60])
        ok("<iframe" in code and "height" in code, "y es un iframe con altura")

        print("\n── con reporte y sin enlace, el panel lo crea ─────────────────")
        await pg.click(CON + " > summary")
        await pg.wait_for_timeout(1100)
        creados = [u for u in posts if "conreporte" in u and not u.endswith("/rotar")]
        ok(len(creados) == 1, "sale UNA petición de creación", str(len(creados)))
        ok((await pg.input_value(CON + " .gurl")).endswith("/r/TOKENFRESCO/"),
           "y se pinta el enlace nuevo", await pg.input_value(CON + " .gurl"))
        aviso = await pg.inner_text(CON + " .mgen")
        ok("creado" in aviso.lower(), "con un aviso de que se ha creado", aviso[:60])

        print("\n── sin reporte no se pide nada ────────────────────────────")
        await pg.click(SIN + " > summary")
        await pg.wait_for_timeout(1000)
        ok(len([u for u in posts if "sinreporte" in u]) == 0,
           "no se intenta crear un enlace que no mostraría nada")
        t = await pg.inner_text(SIN + " .general")
        ok("Preparar el reporte" in t, "y se dice qué hacer para tenerlo", t[:80])

        print("\n── rotar cuesta escribir el identificador ─────────────────────")
        await pg.click(YA + " .grotar")
        await pg.wait_for_timeout(300)
        ok(await pg.is_disabled(YA + " .gya"), "el botón arranca desactivado")
        ok("deja de funcionar" in (await pg.inner_text(YA + " .grot")),
           "y avisa de que rompe el iframe ya pegado")
        await pg.fill(YA + " .gconf", "yatien")
        await pg.wait_for_timeout(120)
        ok(await pg.is_disabled(YA + " .gya"), "mal escrito, sigue desactivado")
        await pg.fill(YA + " .gconf", "yatiene")
        await pg.wait_for_timeout(120)
        ok(not await pg.is_disabled(YA + " .gya"), "exacto, se activa")
        await pg.click(YA + " .gya")
        await pg.wait_for_timeout(700)
        rot = [u for u in posts if u.endswith("confirmar=yatiene") or "/rotar?" in u]
        ok(len(rot) == 1, "sale UNA petición de rotado", str(len(rot)))
        ok("confirmar=yatiene" in rot[0], "con el confirmar exacto", rot[0][-40:])
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
