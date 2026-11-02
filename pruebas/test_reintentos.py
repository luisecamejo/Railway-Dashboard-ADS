# -*- coding: utf-8 -*-
"""
Los fallos que dejaron a 6 de 8 clientes sin reporte el 11-sep-2026, reproducidos.

Esta prueba existe para que no vuelvan. Los dos eran fallos PASAJEROS que el
código trataba como definitivos, así que un corte de un segundo costaba la
extracción completa de un cliente y el dashboard se quedaba enseñando datos de
ayer mientras el CRM ya enseñaba los de hoy:

  · IncompleteRead(0 bytes read) — 5 clientes. La conexión con ghl-mcp se cortó
    después del 200 y antes del cuerpo. No es un URLError, así que se escapaba
    del bucle de reintentos de comun/http.py.
  · 429 Too Many Requests de GoHighLevel — 1 cliente (golden-rose). Llega dentro
    de un HTTP **200** con isError=true, así que el reintento por código de
    estado no lo veía.

No toca la red ni necesita credenciales.

    python pruebas/test_reintentos.py
"""
import http.client, io, json, logging, pathlib, sys, time

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "extractores"))
logging.disable(logging.WARNING)

from comun import http as H                      # noqa: E402
from comun.mcp import ClienteMCP, SubcuentaInexistente   # noqa: E402
from comun.pasadas import FalloDefinitivo, ejecutar      # noqa: E402

fallos = []


def ok(cond, etiqueta):
    print(("  ✓ " if cond else "  ✗ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


class _Resp(io.BytesIO):
    status = 200
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _sse(payload):
    return ("event: message\ndata: " + json.dumps(payload) + "\n\n").encode()


_OK = {"jsonrpc": "2.0", "id": 1,
       "result": {"content": [{"type": "text", "text": '{"meta":{},"filas":1}'}]}}


def _error_de_herramienta(texto):
    return {"jsonrpc": "2.0", "id": 1,
            "result": {"isError": True, "content": [{"type": "text", "text": texto}]}}


def prueba_corte_de_conexion():
    print("corte de conexión a media respuesta (IncompleteRead)")
    n = {"i": 0}

    def urlopen(req, timeout=None):
        n["i"] += 1
        if n["i"] < 3:
            raise http.client.IncompleteRead(b"")   # el error literal de producción
        return _Resp(b'{"ok":true}')

    H.urllib.request.urlopen = urlopen
    _c, cuerpo, _h = H.pedir("https://x/mcp", metodo="POST", cuerpo=b"{}")
    ok(json.loads(cuerpo) == {"ok": True}, "se reintenta y acaba devolviendo el dato")
    ok(n["i"] == 3, "hubo 3 intentos (antes del arreglo: 1, y el cliente perdido)")

    H.urllib.request.urlopen = lambda r, timeout=None: (
        _ for _ in ()).throw(http.client.IncompleteRead(b""))
    try:
        H.pedir("https://x/mcp")
        ok(False, "un corte permanente acaba levantando error")
    except RuntimeError as ex:
        ok("se cortó a media respuesta" in str(ex),
           "un corte permanente levanta un error que dice qué pasó")


def prueba_429_dentro_de_un_200():
    print("429 de GoHighLevel devuelto como isError con HTTP 200")
    err = _error_de_herramienta(
        'Error: GET /contacts/ -> 429: {"statusCode":429,"message":"Too Many Requests"}')
    n = {"i": 0}

    def urlopen(req, timeout=None):
        n["i"] += 1
        return _Resp(_sse(err if n["i"] < 3 else _OK))

    H.urllib.request.urlopen = urlopen
    c = ClienteMCP("https://x", "tok", intentos=4, espera_base=0.01)
    r = c.llamar("ghl_export_opportunities_compact", {"client": "golden-rose"})
    ok(r == {"meta": {}, "filas": 1}, "se reintenta pese a venir en un HTTP 200")
    ok(n["i"] == 3, "hubo 3 intentos (antes del arreglo: 1)")


def prueba_error_definitivo_no_se_reintenta():
    print("un error que NO es pasajero sigue fallando rápido")
    err = _error_de_herramienta("Error: GET /contacts/ -> 401: token invalido")
    n = {"i": 0}

    def urlopen(req, timeout=None):
        n["i"] += 1
        return _Resp(_sse(err))

    H.urllib.request.urlopen = urlopen
    try:
        ClienteMCP("https://x", "tok", intentos=4, espera_base=0.01).llamar("x")
        ok(False, "un 401 levanta error")
    except RuntimeError as ex:
        ok(n["i"] == 1 and "401" in str(ex),
           "un 401 falla al primer intento, sin gastar reintentos")


def prueba_sse_truncado():
    print("respuesta SSE cortada por la mitad")
    n = {"i": 0}

    def urlopen(req, timeout=None):
        n["i"] += 1
        return _Resp(b'event: message\ndata: {"jsonr' if n["i"] < 2 else _sse(_OK))

    H.urllib.request.urlopen = urlopen
    r = ClienteMCP("https://x", "tok", intentos=3, espera_base=0.01).llamar("x")
    ok(r == {"meta": {}, "filas": 1}, "se reintenta en vez de reventar")


def prueba_subcuenta_inexistente():
    """
    El fallo de 305-fenix-group del 1-nov-2026: su location id ya no está entre las
    sub-cuentas que ve el MCP. Antes se reintentaba en la segunda pasada (con dos
    minutos de enfriamiento) y el cron terminaba en rojo por un cliente al que ningún
    reintento iba a devolver.
    """
    print("la sub-cuenta pedida no existe para el MCP (location id desactivado o mal)")
    err = _error_de_herramienta(
        'Error: No hay ninguna sub-cuenta a tu alcance que coincida con '
        '"rfRm2ixhtHnkoZDtf7cd". Disponibles: AestheticsbyCliff Med SPA, ENROLL AMERICA INC')
    n = {"i": 0}

    def urlopen(req, timeout=None):
        n["i"] += 1
        return _Resp(_sse(err))

    H.urllib.request.urlopen = urlopen
    c = ClienteMCP("https://x", "tok", intentos=4, espera_base=0.01)
    try:
        c.llamar("ghl_list_pipelines", {"client": "rfRm2ixhtHnkoZDtf7cd"})
        ok(False, "levanta error")
    except SubcuentaInexistente as ex:
        ok(n["i"] == 1, "falla al primer intento, sin gastar reintentos")
        ok("rfRm2ixhtHnkoZDtf7cd" in str(ex) and "Sentinel Suite" in str(ex),
           "el error dice qué id falta y dónde se arregla (la subcuenta en la suite)")
        ok(isinstance(ex, FalloDefinitivo), "es un fallo definitivo para las pasadas")

    # Y las pasadas: el cliente roto no entra en la segunda pasada ni tumba el cron.
    llamadas = []
    esperas = []

    def procesar(o):
        llamadas.append(o["slug"])
        if o["slug"] == "305-fenix-group":
            raise SubcuentaInexistente("no existe")
        if o["slug"] == "cliff" and llamadas.count("cliff") == 1:
            raise RuntimeError("429 Too Many Requests")   # pasajero: sí se reintenta

    codigo = ejecutar([{"slug": "cliff"}, {"slug": "305-fenix-group"}, {"slug": "garage"}],
                      procesar, concurrencia=1, pausa_entre=0, enfriamiento=120,
                      dormir=esperas.append, reloj=lambda: 0.0)
    ok(llamadas.count("305-fenix-group") == 1, "el cliente roto se procesa UNA vez, sin segunda pasada")
    ok(llamadas.count("cliff") == 2 and codigo == 0,
       "el pasajero sí se reintenta y, recuperado, la pasada termina en verde (código 0)")
    ok(all(e == 0 for e in esperas[:-1]) and esperas[-1] == 120,
       "el enfriamiento se espera por el pasajero, no por el definitivo")

    llamadas.clear(); esperas.clear()
    codigo = ejecutar([{"slug": "305-fenix-group"}], procesar, concurrencia=1, pausa_entre=0,
                      enfriamiento=120, dormir=esperas.append, reloj=lambda: 0.0)
    ok(codigo == 2 and not esperas,
       "con SOLO_CLIENTE sobre el cliente roto (botón «Actualizar ahora»): código 2 y sin esperar")

    llamadas.clear()
    codigo = ejecutar([{"slug": "305-fenix-group"}, {"slug": "garage"}], procesar,
                      concurrencia=1, pausa_entre=0, enfriamiento=120,
                      dormir=esperas.append, reloj=lambda: 0.0)
    ok(codigo == 0 and len(llamadas) == 2 and not esperas,
       "solo definitivo + uno bien: código 0, sin segunda pasada ni enfriamiento")


def main():
    real_sleep, time.sleep = time.sleep, lambda s: None   # no esperar de verdad
    real_urlopen = H.urllib.request.urlopen
    try:
        prueba_corte_de_conexion()
        prueba_429_dentro_de_un_200()
        prueba_error_definitivo_no_se_reintenta()
        prueba_sse_truncado()
        prueba_subcuenta_inexistente()
    finally:
        time.sleep = real_sleep
        H.urllib.request.urlopen = real_urlopen
    print("\n" + (f"FALLOS: {fallos}" if fallos else "todo OK"))
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
