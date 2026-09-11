# -*- coding: utf-8 -*-
"""
Que la extracción aguante: lo que falla de verdad, reproducido sin red.

Tres familias de fallo, cada una con su caso real detrás:

  1. META · el tiempo de espera de la Marketing API (subcode 1504018). El 11-sep-2026
     dejó a aesthetics-by-cliff sin reporte con los MISMOS 120 días que habían
     funcionado el día anterior. No se arregla insistiendo: se arregla pidiendo menos.

  2. EL RECORRIDO DE CLIENTES · que uno que falla no se lleve a los demás, que se le
     dé una segunda oportunidad y que varios puedan ir a la vez sin pisarse. Esto
     último es lo que decide si la pasada de 100 clientes dura una hora u ocho.

  3. GOOGLE · el access token dura una hora. Con 8 clientes nunca caducaba a mitad;
     con 100, sí.

No toca la red ni necesita credenciales.

    python pruebas/test_resistencia.py
"""
import datetime as dt
import json
import logging
import pathlib
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "extractores"))
logging.disable(logging.CRITICAL)   # el ruido del log no aporta aquí

from comun.http import ErrorHTTP                                    # noqa: E402
from comun.pasadas import ejecutar, partir_por_la_mitad, tramos     # noqa: E402

fallos = []


def ok(cond, etiqueta, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + etiqueta + (f"  {extra}" if extra else ""))
    if not cond:
        fallos.append(etiqueta)


def _dias(d, h):
    return (dt.date.fromisoformat(h) - dt.date.fromisoformat(d)).days + 1


# ══════════════════════════════════════════════════════════════════════════════
#  1 · Partir la ventana sin dejar huecos
# ══════════════════════════════════════════════════════════════════════════════
def prueba_tramos():
    print("\n== partir la ventana de fechas ==")
    t = tramos("2026-05-14", "2026-09-10", 30)
    ok(len(t) == 4, "120 días se parten en 4 tramos de 30", str(len(t)))
    ok(t[0][0] == "2026-05-14" and t[-1][1] == "2026-09-10",
       "los extremos se respetan")
    ok(sum(_dias(d, h) for d, h in t) == 120, "cubren los 120 días exactos")
    # Sin huecos ni solapes: el día siguiente al fin de un tramo es el inicio del otro.
    seguidos = all(dt.date.fromisoformat(t[i][1]) + dt.timedelta(days=1)
                   == dt.date.fromisoformat(t[i + 1][0]) for i in range(len(t) - 1))
    ok(seguidos, "no hay huecos ni solapes entre tramos")
    ok(tramos("2026-05-14", "2026-05-14", 30) == [("2026-05-14", "2026-05-14")],
       "un solo día es un solo tramo")
    ok(tramos("2026-09-10", "2026-05-14", 30) == [], "un rango invertido no da nada")

    m = partir_por_la_mitad("2026-05-14", "2026-09-10")
    ok(len(m) == 2 and _dias(*m[0]) + _dias(*m[1]) == 120,
       "partir por la mitad conserva los 120 días")
    ok(partir_por_la_mitad("2026-05-14", "2026-05-14") == [],
       "un solo día ya no se puede partir: ahí hay que rendirse, no inventar")


# ══════════════════════════════════════════════════════════════════════════════
#  2 · Meta: el tiempo de espera se resuelve pidiendo menos
# ══════════════════════════════════════════════════════════════════════════════
ERROR_TIMEOUT = {"error": {
    "message": "Invalid parameter", "type": "OAuthException", "code": 100,
    "error_subcode": 1504018, "is_transient": False,
    "error_user_title": "Se ha agotado el tiempo de espera para la solicitud",
    "error_user_msg": ("Prueba con un intervalo de fechas menor, recupera menos datos "
                       "o usa trabajos asincrónicos"),
    "fbtrace_id": "AGheUM2giG5Zctm-0yulUvT"}}
ERROR_CUOTA = {"error": {"message": "(#17) User request limit reached",
                         "type": "OAuthException", "code": 17, "is_transient": True}}
ERROR_TOKEN = {"error": {"message": "Invalid OAuth access token", "code": 190,
                         "type": "OAuthException", "is_transient": False}}


class _Graph(BaseHTTPRequestHandler):
    """Graph API de mentira. `tope_dias` decide cuándo se 'agota el tiempo'."""
    tope_dias = 15
    error_fijo = None
    peticiones = []
    rangos_servidos = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        import urllib.parse as up
        u = up.urlparse(self.path)
        q = {k: v[0] for k, v in up.parse_qs(u.query).items()}
        if not u.path.endswith("/insights"):
            return self._responde(200, {"name": "X", "timezone_name": "America/Denver",
                                        "currency": "USD"})
        tr = json.loads(q.get("time_range", "{}"))
        d, h = tr.get("since"), tr.get("until")
        type(self).peticiones.append((d, h))
        if type(self).error_fijo is not None:
            return self._responde(400, type(self).error_fijo)
        if _dias(d, h) > type(self).tope_dias:
            return self._responde(400, ERROR_TIMEOUT)
        type(self).rangos_servidos.append((d, h))
        # Una fila por día del rango, para poder contar que no falta ninguno.
        filas = []
        cur = dt.date.fromisoformat(d)
        fin = dt.date.fromisoformat(h)
        while cur <= fin:
            filas.append({"date_start": cur.isoformat(), "date_stop": cur.isoformat(),
                          "campaign_id": "1", "campaign_name": "C", "spend": "1.00",
                          "impressions": "10", "clicks": "2",
                          "actions": [{"action_type": "lead", "value": "1"}]})
            cur += dt.timedelta(days=1)
        self._responde(200, {"data": filas})

    def _responde(self, codigo, cuerpo):
        d = json.dumps(cuerpo).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(d)))
        self.end_headers()
        self.wfile.write(d)


def _servidor(puerto=8893):
    s = HTTPServer(("127.0.0.1", puerto), _Graph)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def prueba_meta_timeout():
    print("\n== Meta · el tiempo de espera de la Marketing API ==")
    import meta.extraer as M
    M.BASE = "http://127.0.0.1:8893/v26.0"
    real_sleep, time.sleep = time.sleep, lambda s: None
    s = _servidor()
    try:
        # a) el error real, clasificado como lo que es
        ex = ErrorHTTP(400, json.dumps(ERROR_TIMEOUT), "http://x/insights")
        ok(M.clasificar(ex) == "trocear",
           "el 1504018 se lee como 'pide menos', no como 'insiste'")
        ok(M.clasificar(ErrorHTTP(400, json.dumps(ERROR_CUOTA), "http://x")) == "esperar",
           "el límite de peticiones se lee como 'espera'")
        ok(M.clasificar(ErrorHTTP(400, json.dumps(ERROR_TOKEN), "http://x")) == "rendirse",
           "un token inválido se lee como 'ríndete': insistir no lo arregla")
        ok(M.clasificar(ErrorHTTP(500, "<html>vaya</html>", "http://x")) == "rendirse",
           "un cuerpo que no es de Meta no se interpreta a la ligera")

        # b) el caso completo: 120 días contra un servidor que no aguanta más de 15
        _Graph.tope_dias, _Graph.peticiones, _Graph.rangos_servidos = 15, [], []
        filas = M._insights_por_tramos(
            "123", "tok", {"level": "campaign", "time_increment": 1,
                           "fields": "campaign_id,spend", "limit": 500},
            "2026-05-14", "2026-09-10", "prueba")
        dias_unicos = {f["date_start"] for f in filas}
        ok(len(dias_unicos) == 120,
           "se recuperan los 120 días pese a que ninguna petición de 30 cabía",
           f"{len(dias_unicos)} días en {len(filas)} filas")
        ok(all(_dias(d, h) <= 15 for d, h in _Graph.rangos_servidos),
           "ningún rango servido pasó del tope del servidor")
        ok(len(_Graph.peticiones) > len(_Graph.rangos_servidos),
           "hubo intentos fallidos y se repartieron solos",
           f"{len(_Graph.peticiones)} intentos → {len(_Graph.rangos_servidos)} servidos")

        # c) un servidor mucho más estrecho: tiene que seguir bajando
        _Graph.tope_dias, _Graph.peticiones, _Graph.rangos_servidos = 4, [], []
        filas = M._insights_por_tramos(
            "123", "tok", {"level": "campaign", "time_increment": 1,
                           "fields": "campaign_id,spend", "limit": 500},
            "2026-08-01", "2026-08-30", "prueba")
        ok(len({f["date_start"] for f in filas}) == 30,
           "baja hasta donde haga falta: 30 días con tope de 4")

        # d) si NI UN DÍA se puede servir, hay que fallar en voz alta
        _Graph.tope_dias, _Graph.peticiones = 0, []
        try:
            M._insights_por_tramos("123", "tok",
                                   {"level": "campaign", "time_increment": 1,
                                    "fields": "x", "limit": 500},
                                   "2026-08-01", "2026-08-03", "prueba")
            ok(False, "un rango imposible tiene que levantar error")
        except RuntimeError as e:
            ok("hueco" in str(e),
               "un rango que no se puede servir FALLA en vez de devolver un agujero")

        # e) un error definitivo no se trocea ni se reintenta: se sube tal cual
        _Graph.error_fijo, _Graph.peticiones = ERROR_TOKEN, []
        try:
            M._insights_por_tramos("123", "tok",
                                   {"level": "campaign", "time_increment": 1,
                                    "fields": "x", "limit": 500},
                                   "2026-08-01", "2026-08-30", "prueba")
            ok(False, "un token inválido tiene que levantar error")
        except ErrorHTTP:
            ok(len(_Graph.peticiones) == 1,
               "un token inválido falla a la primera, sin gastar una sola llamada de más")
        finally:
            _Graph.error_fijo = None
    finally:
        s.shutdown()
        time.sleep = real_sleep


# ══════════════════════════════════════════════════════════════════════════════
#  3 · El recorrido de clientes
# ══════════════════════════════════════════════════════════════════════════════
def prueba_pasadas():
    print("\n== recorrido de clientes ==")
    objetivos = [{"slug": f"c{i}"} for i in range(5)]
    esperas = []
    nada = lambda s: esperas.append(s)

    # a) todo bien
    vistos = []
    ok(ejecutar(objetivos, lambda o: vistos.append(o["slug"]), dormir=nada) == 0,
       "con todos OK devuelve 0")
    ok(vistos == ["c0", "c1", "c2", "c3", "c4"], "los recorre todos, en orden")

    # b) uno falla siempre: los demás siguen, y devuelve 1
    vistos = []

    def uno_roto(o):
        vistos.append(o["slug"])
        if o["slug"] == "c2":
            raise RuntimeError("boom")

    ok(ejecutar(objetivos, uno_roto, dormir=nada) == 1, "si uno falla devuelve 1")
    ok(vistos.count("c2") == 2, "el roto se reintenta en la segunda pasada")
    # Esto es lo que hace que la segunda pasada sea barata: solo repite lo que falló.
    # Si repitiera a todos, con 100 clientes la pasada de reintento costaría otra
    # pasada entera y la extracción no cabría en la noche.
    ok(all(vistos.count(f"c{i}") == 1 for i in (0, 1, 3, 4)),
       "los que fueron bien NO se repiten: la segunda pasada solo cuesta los fallos")

    # c) un fallo PASAJERO se salva en la segunda pasada
    intentos = {"c2": 0}

    def pasajero(o):
        if o["slug"] == "c2":
            intentos["c2"] += 1
            if intentos["c2"] == 1:
                raise RuntimeError("429 Too Many Requests")

    ok(ejecutar(objetivos, pasajero, dormir=nada) == 0,
       "un fallo pasajero se recupera solo y la pasada acaba en verde")

    # d) la segunda pasada espera antes de repetir
    esperas.clear()
    ejecutar([{"slug": "x"}], lambda o: (_ for _ in ()).throw(RuntimeError("no")),
             enfriamiento=120, pausa_entre=0, dormir=nada)
    ok(120 in esperas, "antes de repetir se deja enfriar la cuota", str(esperas))

    # e) sin objetivos no es un error
    ok(ejecutar([], lambda o: None, dormir=nada) == 0, "una lista vacía no es un fallo")


def prueba_concurrencia():
    print("\n== varios clientes a la vez (la pieza que escala) ==")
    objetivos = [{"slug": f"c{i}"} for i in range(12)]
    a_la_vez, pico = {"n": 0}, {"max": 0}
    candado = threading.Lock()
    hechos = []

    def lento(o):
        with candado:
            a_la_vez["n"] += 1
            pico["max"] = max(pico["max"], a_la_vez["n"])
        time.sleep(0.05)
        with candado:
            a_la_vez["n"] -= 1
            hechos.append(o["slug"])

    t0 = time.monotonic()
    codigo = ejecutar(objetivos, lento, concurrencia=4, pausa_entre=0, dormir=lambda s: None)
    dur = time.monotonic() - t0
    ok(codigo == 0, "con concurrencia sigue devolviendo 0")
    ok(sorted(hechos) == sorted(o["slug"] for o in objetivos),
       "no se pierde ni se repite ningún cliente", f"{len(hechos)} de 12")
    ok(pico["max"] > 1, "de verdad van varios a la vez", f"pico {pico['max']}")
    ok(pico["max"] <= 4, "y nunca más de los permitidos", f"pico {pico['max']}")
    ok(dur < 12 * 0.05 * 0.8, "tarda bastante menos que en serie",
       f"{dur:.2f}s frente a {12 * 0.05:.2f}s en serie")

    # Un fallo en paralelo no se pierde ni contamina a los demás.
    def roto_par(o):
        if o["slug"] in ("c3", "c7"):
            raise RuntimeError("boom " + o["slug"])

    ok(ejecutar(objetivos, roto_par, concurrencia=4, pausa_entre=0,
                dormir=lambda s: None) == 1,
       "en paralelo, los fallos se siguen contando")


# ══════════════════════════════════════════════════════════════════════════════
#  4 · Google: el access token dura una hora
# ══════════════════════════════════════════════════════════════════════════════
def prueba_google_token():
    print("\n== Google · caducidad del access token ==")
    import google.extraer as G

    pedidos = {"n": 0}

    def falso_pedir(url, **kw):
        pedidos["n"] += 1
        return 200, json.dumps({"access_token": f"tok{pedidos['n']}",
                                "expires_in": 3600}).encode(), {}

    real = G.pedir
    G.pedir = falso_pedir
    real_time = G.time.time
    try:
        for n, v in (("GOOGLE_CLIENT_ID", "a"), ("GOOGLE_CLIENT_SECRET", "b"),
                     ("GOOGLE_REFRESH_TOKEN", "c"), ("GOOGLE_DEVELOPER_TOKEN", "d")):
            import os
            os.environ[n] = v
        cred = G.Credenciales()

        ahora = [1_000_000.0]
        G.time.time = lambda: ahora[0]

        ok(cred.access_token() == "tok1", "la primera vez pide un token")
        ok(cred.access_token() == "tok1" and pedidos["n"] == 1,
           "y lo reutiliza mientras siga vivo")

        ahora[0] += 3400          # dentro del margen de 5 minutos antes de caducar
        ok(cred.access_token() == "tok2",
           "lo renueva ANTES de que caduque, no después de fallar")

        pedidos["n"] = 2
        ok(cred.access_token(forzar=True) == "tok3" and pedidos["n"] == 3,
           "y se puede forzar, que es lo que hace el reintento del 401")
    finally:
        G.pedir = real
        G.time.time = real_time


def main():
    prueba_tramos()
    prueba_meta_timeout()
    prueba_pasadas()
    prueba_concurrencia()
    prueba_google_token()
    print("\n" + (f"FALLOS: {fallos}" if fallos else "todo OK"))
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
