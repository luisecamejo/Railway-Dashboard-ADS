# -*- coding: utf-8 -*-
"""
El cliente MCP contra el MCP detrás de Sentinel Suite (fase E, 17-oct-2026).

Lo que se rompió ese día: GHL_MCP_URL seguía apuntando al dominio antiguo del MCP,
que desde la fase E responde **308** a la ruta de la suite (y esa ruta exige el
OAuth de una persona, no un token de máquina). El panel decía «No pude leer la
lista: HTTP 308», que no dice dónde mirar. Aquí se comprueba que:

  · un 3xx se explica como «GHL_MCP_URL apunta al dominio antiguo»;
  · un 401/403 se explica como «GHL_MCP_TOKEN tiene que ser un token de servicio
    de la suite»;
  · ninguno de los dos se reintenta (son de configuración, no pasajeros);
  · con el dominio propio y un token `ss_…` válido, un `tools/call` a pelo (sin
    `initialize`) funciona igual que antes.

No toca la red ni necesita credenciales.

    python pruebas/test_mcp_servicio.py
"""
import http.server, json, logging, pathlib, sys, threading

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "extractores"))
logging.disable(logging.WARNING)

from comun.mcp import ClienteMCP                 # noqa: E402

fallos = []
PUERTO = 8893
SUITE = "https://suite.sentinelmarketing.net/mcp/ghl"
TOKEN_BUENO = "ss_token-de-servicio-de-prueba"


def ok(cond, etiqueta):
    print(("  ✓ " if cond else "  ✗ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


class MCPFalso(http.server.BaseHTTPRequestHandler):
    """
    Réplica de lo que hace http-server.js de ghl-mcp desde la fase E:
      · dominio antiguo (ruta /viejo/mcp): 308 a la suite, sin cuerpo útil;
      · dominio propio (ruta /mcp): sin `ss_` → 308; `ss_` desconocido → 401 JSON;
        `ss_` bueno → tools/call sin initialize, en SSE.
    """
    llamadas: list = []

    def log_message(self, *a):          # silencio
        pass

    def _json(self, code, obj, sse=False):
        d = (f"event: message\ndata: {json.dumps(obj)}\n\n" if sse
             else json.dumps(obj)).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/event-stream" if sse else "application/json")
        self.send_header("Content-Length", str(len(d)))
        self.end_headers()
        self.wfile.write(d)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(n) or b"{}")
        MCPFalso.llamadas.append(self.path)
        auth = self.headers.get("Authorization") or ""
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if self.path == "/viejo/mcp" or not token.startswith("ss_"):
            self.send_response(308)
            self.send_header("Location", SUITE)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if token != TOKEN_BUENO:
            self._json(401, {"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32000,
                                       "message": "Token de servicio de Sentinel Suite no valido, revocado o sin permiso subaccounts:read."}})
            return
        self._json(200, {"jsonrpc": "2.0", "id": req.get("id"),
                         "result": {"content": [{"type": "text",
                                                 "text": json.dumps({"clientes": [{"id": "LOC1"}]})}]}},
                   sse=True)


def main():
    s = http.server.ThreadingHTTPServer(("127.0.0.1", PUERTO), MCPFalso)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{PUERTO}"
    try:
        # 1. Dominio antiguo → 308 → error que nombra GHL_MCP_URL, sin reintentos
        MCPFalso.llamadas.clear()
        try:
            ClienteMCP(f"{base}/viejo", TOKEN_BUENO, intentos=4, espera_base=0.01).llamar("ghl_list_clients")
            ok(False, "el 308 del dominio antiguo tiene que fallar")
        except RuntimeError as ex:
            m = str(ex)
            ok("GHL_MCP_URL" in m and "308" in m and "dominio" in m,
               f"el 308 se explica como GHL_MCP_URL en el dominio antiguo: {m[:90]}…")
        ok(len(MCPFalso.llamadas) == 1, "un 308 no se reintenta (es de configuración)")

        # 2. Token que no es de servicio (el GHL_MCP_HTTP_TOKEN de antes) → 308 también
        try:
            ClienteMCP(base, "0486a750-token-antiguo", intentos=2, espera_base=0.01).llamar("ghl_list_clients")
            ok(False, "el token antiguo tiene que fallar")
        except RuntimeError as ex:
            ok("GHL_MCP_URL" in str(ex) or "308" in str(ex),
               "con el token antiguo el MCP redirige y el error lo dice")

        # 3. Token ss_ desconocido → 401 → error que nombra GHL_MCP_TOKEN, sin reintentos
        MCPFalso.llamadas.clear()
        try:
            ClienteMCP(base, "ss_revocado", intentos=4, espera_base=0.01).llamar("ghl_list_clients")
            ok(False, "un token revocado tiene que fallar")
        except RuntimeError as ex:
            m = str(ex)
            ok("GHL_MCP_TOKEN" in m and "401" in m and "token de servicio" in m,
               f"el 401 se explica como GHL_MCP_TOKEN inválido: {m[:90]}…")
        ok(len(MCPFalso.llamadas) == 1, "un 401 no se reintenta")

        # 4. Dominio propio + token ss_ bueno: tools/call a pelo funciona
        r = ClienteMCP(base, TOKEN_BUENO).llamar("ghl_list_clients")
        ok(r == {"clientes": [{"id": "LOC1"}]}, "con el dominio propio y el token de servicio, tools/call responde")
    finally:
        s.shutdown()

    print()
    if fallos:
        print(f"{len(fallos)} fallo(s):")
        for f in fallos:
            print("  -", f)
        sys.exit(1)
    print("todo bien")


if __name__ == "__main__":
    main()
