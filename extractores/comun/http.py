# -*- coding: utf-8 -*-
"""
Un cliente HTTP mínimo, con reintentos, sobre la librería estándar.

Por qué no `requests`: los extractores corren como tareas programadas en Railway y
cuanto menos haya que instalar, menos hay que mantener. urllib basta y evita una
dependencia más que auditar.

Lo que sí trae, porque hace falta de verdad contra APIs de terceros:
  · reintentos con espera creciente en 429 y 5xx (Meta devuelve 429 a poco que
    aprietes, y Google 503 en franjas de mantenimiento)
  · reintentos también cuando la conexión se CORTA a media respuesta, que no es
    lo mismo que un error de estado (ver CORTES_DE_RED)
  · respeta la cabecera Retry-After cuando viene
  · el cuerpo del error se propaga en el mensaje: un fallo de credenciales tiene
    que decir QUÉ dijo el servidor, no "error 400"
"""
from __future__ import annotations

import http.client
import json
import logging
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("extractor.http")

REINTENTABLES = {408, 425, 429, 500, 502, 503, 504}

# Cortes de red que merecen otro intento.
#
# ESTA LISTA EXISTE POR UN FALLO CONCRETO, así que no se toca sin leer esto:
# `http.client.IncompleteRead` NO es un `URLError`. Se levanta desde `r.read()`,
# cuando el servidor ya mandó las cabeceras con un 200 y después cerró la conexión
# sin entregar el cuerpo. Como no encajaba en ninguna de las dos cláusulas `except`
# de abajo, se escapaba del bucle de reintentos ENTERO: un corte de un segundo
# tumbaba la extracción del cliente completo, sin un solo reintento.
#
# El 11-sep-2026 eso costó 5 de los 6 clientes que fallaron en la misma pasada,
# todos con el mismo mensaje: "FALLÓ: IncompleteRead(0 bytes read)". El servicio
# de enfrente había respondido 200 en el proxy de Railway y sin errores de
# upstream; simplemente el cuerpo no llegó. Con un reintento se habrían salvado
# los cinco.
#
# `RemoteDisconnected` hereda de `ConnectionResetError` y de `BadStatusLine`, y
# `socket.timeout` es `TimeoutError` desde 3.10: se listan igualmente para que el
# porqué quede escrito y no dependa del árbol de herencia de turno.
CORTES_DE_RED = (
    http.client.IncompleteRead,
    http.client.RemoteDisconnected,
    http.client.BadStatusLine,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
    socket.timeout,
)


class ErrorHTTP(RuntimeError):
    def __init__(self, codigo: int, cuerpo: str, url: str):
        self.codigo, self.cuerpo = codigo, cuerpo
        # Se recorta el cuerpo: algunas APIs devuelven un HTML de 40 KB en un 500 y
        # eso llena los logs sin aportar nada.
        super().__init__(f"HTTP {codigo} en {url.split('?')[0]} · {cuerpo[:400]}")


def _espera(base: float, intento: int, tope: float = 60.0) -> float:
    """
    Espera creciente con algo de ruido.

    El ruido no es cosmético: los extractores van en serie pero comparten cuota
    con el resto de lo que hable con GoHighLevel en ese momento. Si todos los
    reintentos caen en el mismo instante exacto, vuelven a chocar contra el mismo
    límite y el segundo intento falla igual que el primero.
    """
    return min(base * (2 ** (intento - 1)) * (1 + random.random() * 0.3), tope)


def pedir(url: str, *, metodo: str = "GET", cabeceras: dict | None = None,
          cuerpo: bytes | None = None, intentos: int = 5,
          espera_base: float = 1.5, timeout: int = 90) -> tuple[int, bytes, dict]:
    """Devuelve (codigo, cuerpo, cabeceras). Levanta ErrorHTTP si no hay forma."""
    ultimo = None
    limpia = url.split("?")[0]
    for n in range(1, intentos + 1):
        req = urllib.request.Request(url, data=cuerpo, method=metodo,
                                     headers=cabeceras or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as ex:
            crudo = ex.read()
            texto = crudo.decode("utf-8", "replace")
            ultimo = ErrorHTTP(ex.code, texto, url)
            if ex.code not in REINTENTABLES or n == intentos:
                raise ultimo
            # Retry-After manda sobre nuestra espera: si el servidor dice cuánto,
            # insistir antes solo gasta cuota.
            ra = ex.headers.get("Retry-After") if ex.headers else None
            pausa = (float(ra) if (ra or "").strip().isdigit()
                     else _espera(espera_base, n))
            log.warning("HTTP %s en %s · reintento %d/%d en %.1fs",
                        ex.code, limpia, n, intentos, pausa)
            time.sleep(min(pausa, 60))
        except CORTES_DE_RED as ex:
            # La respuesta se cortó a medias. Se reintenta la petición entera:
            # todas las llamadas que pasan por aquí son lecturas o `tools/call`
            # del MCP, así que repetirlas no duplica nada.
            ultimo = RuntimeError(
                f"La conexión con {limpia} se cortó a media respuesta: "
                f"{type(ex).__name__}({ex})")
            if n == intentos:
                raise ultimo
            pausa = _espera(espera_base, n)
            log.warning("conexión cortada con %s (%s) · reintento %d/%d en %.1fs",
                        limpia, type(ex).__name__, n, intentos, pausa)
            time.sleep(pausa)
        except (urllib.error.URLError, TimeoutError) as ex:
            ultimo = RuntimeError(f"No se pudo conectar con {limpia}: {ex}")
            if n == intentos:
                raise ultimo
            # También se reintenta un fallo de DNS: la red privada de Railway tarda un
            # instante en resolver justo después de arrancar el contenedor.
            pausa = _espera(espera_base, n)
            log.warning("sin conexión con %s · reintento %d/%d en %.1fs",
                        limpia, n, intentos, pausa)
            time.sleep(pausa)
    raise ultimo  # pragma: no cover


def json_get(url: str, params: dict | None = None, *, cabeceras: dict | None = None,
             **kw) -> dict:
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    _c, cuerpo, _h = pedir(url, cabeceras={"Accept": "application/json",
                                           **(cabeceras or {})}, **kw)
    return json.loads(cuerpo or b"{}")


def json_post(url: str, datos, *, cabeceras: dict | None = None, **kw) -> dict:
    crudo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
    _c, cuerpo, _h = pedir(url, metodo="POST", cuerpo=crudo,
                           cabeceras={"Content-Type": "application/json",
                                      "Accept": "application/json",
                                      **(cabeceras or {})}, **kw)
    return json.loads(cuerpo or b"{}")
