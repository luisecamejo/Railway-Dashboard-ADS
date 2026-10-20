# -*- coding: utf-8 -*-
"""
El reporte en el menú lateral de la sub-cuenta de GoHighLevel (web/app/menu_ghl.py).

Lo que se comprueba, con un MCP de mentira que registra lo que le piden:
  1. tras publicar, con enlace general empotrable y location, se llama a `ghl_menu_reporte`
     con la URL pública del enlace (…/r/<token>/embed) y queda anotado en la configuración;
  2. la siguiente publicación NO vuelve a llamar (idempotente y barato);
  3. si el MCP falla (p. ej. sin permiso custom-menu-link.write), la publicación no se
     rompe, el error queda en `ghlMenuError` y `instalar(forzar=True)` lo reintenta;
  4. sin location o sin enlace empotrable no se llama a nadie.

No toca la red ni necesita credenciales.

    python pruebas/test_menu_ghl.py
"""
import http.server, json, logging, os, pathlib, shutil, sys, tempfile, threading

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "web"))
sys.path.insert(0, str(RAIZ / "extractores"))
logging.disable(logging.WARNING)

fallos = []
PUERTO = 8894
llamadas = []
fallar = {"si": False}


def ok(cond, etiqueta):
    print(("  ✓ " if cond else "  ✗ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


class MCPFalso(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(n) or b"{}")
        p = req.get("params") or {}
        llamadas.append((p.get("name"), p.get("arguments")))
        if fallar["si"]:
            res = {"isError": True, "content": [{"type": "text", "text": "Error: GET /custom-menus/ -> 401: scope custom-menu-link.write missing"}]}
        else:
            res = {"content": [{"type": "text", "text": json.dumps({"accion": "creado", "id": "menu-1"})}]}
        d = f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'id': req.get('id'), 'result': res})}\n\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(d)))
        self.end_headers()
        self.wfile.write(d)


def main() -> int:
    os.environ["SUITE_URL"] = "https://suite.prueba"
    os.environ["GHL_MCP_URL"] = f"http://127.0.0.1:{PUERTO}"
    os.environ["GHL_MCP_TOKEN"] = "ss_prueba"
    os.environ["GHL_MCP_TIMEOUT_FICHA"] = "5"
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="menu-"))
    s = http.server.ThreadingHTTPServer(("127.0.0.1", PUERTO), MCPFalso)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        from app.almacen import AlmacenFicheros
        from app import enlace_general, menu_ghl as M

        alm = AlmacenFicheros(str(tmp))
        alm.guardar_cliente("cliff", "Aesthetics by Cliff", "LOC1", "America/Denver")
        alm.guardar_config("cliff", {"nombre": "Aesthetics by Cliff", "slug": "cliff", "tz": "America/Denver",
                                     "ghlLocationId": "LOC1", "cuentas": []})

        # 4. Sin enlace general todavía: no se llama a nadie
        r = M.instalar(alm, "cliff")
        ok(r["estado"] == "sin-enlace" and not llamadas, "sin enlace empotrable no se instala nada")

        # 1. Publicación: nace el enlace general y con él el menú
        enlace_general.tras_publicar(alm, "cliff")
        M.tras_publicar(alm, "cliff")
        tok = enlace_general.buscar(alm, "cliff")["token"]
        ok(len(llamadas) == 1 and llamadas[0][0] == "ghl_menu_reporte", "tras publicar se pide el menú al MCP")
        args = llamadas[0][1] or {}
        ok(args.get("client") == "LOC1" and args.get("url") == f"https://suite.prueba/reportes/r/{tok}/embed"
           and args.get("titulo") == "Reporte ROASLytics", f"con la sub-cuenta, el título y la URL pública del embed: {args}")
        cfg = (alm.cliente("cliff") or {}).get("config") or {}
        ok((cfg.get("ghlMenu") or {}).get("id") == "menu-1" and cfg["ghlMenu"]["url"] == args["url"],
           "queda anotado en la configuración del cliente")
        est = M.estado(alm, "cliff")
        ok(est["menu"]["id"] == "menu-1" and est["url"] == args["url"] and est["error"] is None, "el estado lo enseña")

        # 2. Otra publicación: no se repite
        M.tras_publicar(alm, "cliff")
        ok(len(llamadas) == 1, "la siguiente publicación no vuelve a llamar")

        # 3. El MCP falla: no se rompe, queda el error, y el botón reintenta
        fallar["si"] = True
        r = M.instalar(alm, "cliff", forzar=True)
        cfg = (alm.cliente("cliff") or {}).get("config") or {}
        ok(r["estado"] == "error" and "custom-menu-link" in (cfg.get("ghlMenuError") or ""),
           "si el MCP falla, el error queda en la configuración y la publicación no se rompe")
        fallar["si"] = False
        r = M.instalar(alm, "cliff", forzar=True)
        cfg = (alm.cliente("cliff") or {}).get("config") or {}
        ok(r["estado"] == "instalado" and not cfg.get("ghlMenuError"), "reintentar limpia el error")

        # 4b. Sin location
        alm.guardar_cliente("sinloc", "Sin Location", None, "Europe/Madrid")
        alm.guardar_config("sinloc", {"nombre": "Sin Location", "slug": "sinloc", "tz": "Europe/Madrid", "cuentas": []})
        enlace_general.tras_publicar(alm, "sinloc")
        n = len(llamadas)
        M.tras_publicar(alm, "sinloc")
        ok(len(llamadas) == n and M.instalar(alm, "sinloc")["estado"] == "sin-location", "sin sub-cuenta de GHL no se llama a nadie")
    finally:
        s.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fallos:
        print(f"{len(fallos)} fallo(s)")
        return 1
    print("todo bien")
    return 0


if __name__ == "__main__":
    sys.exit(main())
