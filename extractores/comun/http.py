# -*- coding: utf-8 -*-
"""
Un cliente HTTP mínimo, con reintentos, sobre la librería estándar.

Por qué no `requests`: los extractores corren como tareas programadas en Railway y
cuanto menos haya que instalar, menos hay que mantener. urllib basta y evita una
dependencia más que auditar.

Lo que sí trae, porque hace falta de verdad contra APIs de terceros:
  · reintentos con espera creciente en 429 y 5xx (Meta devuelve 429 a poco que
    aprietes, y Google 503 en franjas de mantenimiento)
  · respeta la cabecera Retry-After cuando viene
  · el cuerpo del error se propaga en el mensaje: un fallo de credenciales tiene
    que decir QUÉ dijo el servidor, no "error 400"
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("extractor.http")

REINTENTABLES = {408, 429, 500, 502, 503, 504}


class ErrorHTTP(RuntimeError):
    def __init__(self, codigo: int, cuerpo: str, url: str):
        self.codigo, self.cuerpo = codigo, cuerpo
        # Se recorta el cuerpo: algunas APIs devuelven un HTML de 40 KB en un 500 y
        # eso llena los logs sin aportar nada.
        super().__init__(f"HTTP {codigo} en {url.split('?')[0]} · {cuerpo[:400]}")


def pedir(url: str, *, metodo: str = "GET", cabeceras: dict | None = None,
          cuerpo: bytes | None = None, intentos: int = 4,
          espera_base: float = 1.5, timeout: int = 90) -> tuple[int, bytes, dict]:
    """Devuelve (codigo, cuerpo, cabeceras). Levanta ErrorHTTP si no hay forma."""
    ultimo = None
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
            pausa = float(ra) if (ra or "").strip().isdigit() else espera_base * (2 ** (n - 1))
            log.warning("HTTP %s en %s · reintento %d/%d en %.1fs",
                        ex.code, url.split("?")[0], n, intentos, pausa)
            time.sleep(min(pausa, 60))
        except (urllib.error.URLError, TimeoutError) as ex:
            ultimo = RuntimeError(f"No se pudo conectar con {url.split('?')[0]}: {ex}")
            if n == intentos:
                raise ultimo
            # También se reintenta un fallo de DNS: la red privada de Railway tarda un
            # instante en resolver justo después de arrancar el contenedor.
            pausa = espera_base * (2 ** (n - 1))
            log.warning("sin conexión con %s · reintento %d/%d en %.1fs",
                        url.split("?")[0], n, intentos, pausa)
            time.sleep(min(pausa, 60))
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
