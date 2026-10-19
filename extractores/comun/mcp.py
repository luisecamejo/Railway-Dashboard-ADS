# -*- coding: utf-8 -*-
"""
Cliente MCP mínimo para hablar con el servicio `ghl-mcp` desde un programa.

El MCP está pensado para que lo use un modelo de lenguaje, no un script. Aun así
merece la pena hablarlo en vez de duplicar la extracción: `ghl-mcp` ya resuelve el
OAuth de agencia, el refresco de tokens y la caché de sub-cuentas, y todo eso ya
está en producción. Repetirlo aquí sería mantener dos veces lo mismo.

Desde el 17-oct-2026 (fase E) `ghl-mcp` vive detrás de Sentinel Suite y ya no tiene
token propio. Un programa NO entra por la suite (esa ruta exige el OAuth de una
persona): entra por el DOMINIO PROPIO del servicio con un TOKEN DE SERVICIO de la
suite (`ss_…`, Administración → Integraciones, permiso subaccounts:read), que el
MCP comprueba contra la suite y al que da solo lectura sobre todas las subcuentas
registradas. Apuntar GHL_MCP_URL al dominio antiguo da un 308 (ver `_explicar`).

TODO lo que sigue está comprobado contra el mismo SDK que usa ghl-mcp
(@modelcontextprotocol/sdk 1.30) levantando una réplica de su http-server.js:

  1. `POST /mcp` con `Authorization: Bearer <GHL_MCP_TOKEN>`. El token es el de
     servicio de la suite (antes era GHL_MCP_HTTP_TOKEN, que ya no existe).
  2. NO hace falta `initialize`. El transporte va sin estado
     (`sessionIdGenerator: undefined`), así que cada POST crea un servidor nuevo y
     un `tools/call` a pelo funciona. Mandar `initialize` antes sería un viaje de
     red tirado, porque su resultado no se conserva para la petición siguiente.
  3. HAY QUE mandar `Accept: application/json, text/event-stream`. Con solo uno de
     los dos el SDK responde 406 "Client must accept both". Comprobado.
  4. La respuesta llega como SSE (`event: message` / `data: {...}`) aunque sea una
     sola petición. De ahí `_leer_sse`.
  5. El SDK valida la cabecera Host contra ALLOWED_HOSTS del servicio MCP: el
     dominio al que apunte GHL_MCP_URL tiene que estar en esa variable o responde
     403 "Invalid Host". Comprobado también. (Red privada `*.railway.internal`:
     solo entre servicios del mismo proyecto, y el MCP vive en otro.)
"""
from __future__ import annotations

import itertools
import json
import logging
import random
import time

from .http import ErrorHTTP, pedir

log = logging.getLogger("extractor.mcp")

# Señales de que el fallo es PASAJERO y otro intento tiene sentido.
#
# Por qué se miran aquí y no en comun/http.py: cuando una herramienta del MCP
# falla, el protocolo responde con HTTP **200** y `isError: true`; el texto del
# error va dentro del cuerpo. Así que el reintento por código de estado que hay en
# `pedir()` NO ve estos fallos: para la capa HTTP la petición fue un éxito.
#
# El caso que lo motivó: el 11-sep-2026 la extracción de golden-rose murió con
#   'ghl_export_opportunities_compact' devolvió error:
#   GET /contacts/ -> 429: {"statusCode":429,"message":"Too Many Requests"}
# Un 429 de GoHighLevel es, por definición, "vuelve a preguntar en un rato": es el
# único error del que se sabe con certeza que el reintento es la respuesta correcta,
# y era justo el que no se reintentaba.
PASAJEROS = (
    "429", "too many requests", "rate limit", "ratelimit",
    "502", "503", "504", "bad gateway", "service unavailable", "gateway timeout",
    "timeout", "timed out", "etimedout",
    "econnreset", "socket hang up", "fetch failed", "network error",
)


def _es_pasajero(mensaje: str) -> bool:
    m = (mensaje or "").lower()
    return any(s in m for s in PASAJEROS)


class RespuestaIlegible(RuntimeError):
    """
    El cuerpo llegó pero no contiene una respuesta JSON-RPC.

    Se trata como pasajero: en la práctica es una respuesta truncada, el mismo
    síntoma que `IncompleteRead` pero con algún byte de más.
    """


def _leer_sse(crudo: bytes) -> dict:
    """
    Saca el objeto JSON-RPC de una respuesta SSE.

    Formato real: líneas `event: message` y `data: {...}`, separadas por línea en
    blanco. Puede venir más de un bloque; nos interesa el último que traiga
    `result` o `error`, que es la respuesta a nuestra única petición.
    """
    texto = crudo.decode("utf-8", "replace")
    ultimo = None
    for linea in texto.splitlines():
        if not linea.startswith("data:"):
            continue
        trozo = linea[5:].strip()
        if not trozo:
            continue
        try:
            obj = json.loads(trozo)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and ("result" in obj or "error" in obj):
            ultimo = obj
    if ultimo is None:
        # Si algún día deja de responder en SSE y manda JSON pelado, esto lo cubre.
        try:
            obj = json.loads(texto)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        raise RespuestaIlegible(f"Respuesta MCP ilegible: {texto[:300]!r}")
    return ultimo


class ClienteMCP:
    def __init__(self, base: str, token: str, *, timeout: int = 120,
                 intentos: int = 4, espera_base: float = 3.0):
        if not token:
            raise ValueError("Falta el token del MCP (GHL_MCP_TOKEN).")
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.intentos = max(1, intentos)
        self.espera_base = espera_base
        # itertools.count() reparte ids sin repetir aunque varios hilos pidan a la vez.
        # Hace falta desde que los extractores recorren clientes en paralelo: `self._id
        # += 1` no es una operación atómica y dos hilos podían salir con el mismo id.
        self._ids = itertools.count(1)

    def _post(self, peticion: dict) -> dict:
        try:
            _c, crudo, _h = pedir(
                f"{self.base}/mcp", metodo="POST",
                cuerpo=json.dumps(peticion, ensure_ascii=False).encode("utf-8"),
                cabeceras={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                    # Las DOS, o 406. No es cosmético.
                    "Accept": "application/json, text/event-stream",
                },
                timeout=self.timeout)
        except ErrorHTTP as ex:
            raise self._explicar(ex) from ex
        return _leer_sse(crudo)

    def _explicar(self, ex: ErrorHTTP) -> RuntimeError:
        """
        Los dos fallos de CONFIGURACIÓN que nadie debería tener que descifrar.

        Son definitivos (reintentar no los arregla) y, dichos como «HTTP 308» o
        «HTTP 401», mandan a quien mira el log a buscar en el sitio equivocado.
        El 17-oct-2026 el panel estuvo diciendo «No pude leer la lista: HTTP 308»
        cuando lo que pasaba era que GHL_MCP_URL seguía apuntando al dominio
        antiguo del MCP, que desde la fase E redirige a la suite.
        """
        if ex.codigo in (301, 302, 307, 308):
            return RuntimeError(
                f"GHL_MCP_URL ({self.base}) redirige (HTTP {ex.codigo}): es el dominio "
                "antiguo del MCP, que ahora manda a Sentinel Suite. Un programa no "
                "puede entrar por la suite: GHL_MCP_URL tiene que ser el dominio propio "
                "del servicio sentinel-mcp (ver extractores/README.md).")
        if ex.codigo in (401, 403):
            return RuntimeError(
                f"El MCP rechazó el token (HTTP {ex.codigo}): GHL_MCP_TOKEN tiene que "
                "ser un token de servicio de Sentinel Suite (ss_…, Administración → "
                "Integraciones, permiso subaccounts:read) vivo, no revocado "
                f"(ver extractores/README.md). Detalle: {ex.cuerpo[:200]}")
        return ex

    def llamar(self, herramienta: str, argumentos: dict | None = None):
        """
        Invoca una herramienta y devuelve su resultado ya deserializado.

        Reintenta cuando el error es pasajero (ver PASAJEROS). La espera arranca
        más alta que en `pedir()` a propósito: contra un 429 de GoHighLevel lo que
        hace falta es dejar pasar la ventana del límite, no insistir deprisa.

        Se puede llamar desde varios hilos: el transporte del MCP va sin estado, así
        que cada POST es independiente y los ids no se pisan.
        """
        ultimo = None
        for n in range(1, self.intentos + 1):
            peticion = {"jsonrpc": "2.0", "id": next(self._ids), "method": "tools/call",
                        "params": {"name": herramienta,
                                   "arguments": argumentos or {}}}
            try:
                respuesta = self._post(peticion)
            except RespuestaIlegible as ex:
                ultimo = ex
                if n == self.intentos:
                    raise
                self._dormir(herramienta, str(ex), n)
                continue

            if "error" in respuesta:
                e = respuesta["error"] or {}
                msg = f"{e.get('message')} (código {e.get('code')})"
                ultimo = RuntimeError(f"El MCP rechazó '{herramienta}': {msg}")
                if n == self.intentos or not _es_pasajero(msg):
                    raise ultimo
                self._dormir(herramienta, msg, n)
                continue

            res = respuesta.get("result") or {}
            # isError=true es un fallo de la HERRAMIENTA, no del protocolo: viene con
            # código 200 y sin "error". Si no se mira, un error se cuela como dato.
            if res.get("isError"):
                msg = _texto(res)[:400]
                ultimo = RuntimeError(f"'{herramienta}' devolvió error: {msg}")
                if n == self.intentos or not _es_pasajero(msg):
                    raise ultimo
                self._dormir(herramienta, msg, n)
                continue

            return _contenido(res)
        raise ultimo  # pragma: no cover

    def _dormir(self, herramienta: str, motivo: str, intento: int) -> None:
        pausa = min(self.espera_base * (2 ** (intento - 1)) *
                    (1 + random.random() * 0.3), 90.0)
        log.warning("'%s' falló por algo pasajero (%s) · reintento %d/%d en %.0fs",
                    herramienta, motivo[:160].replace("\n", " "),
                    intento, self.intentos, pausa)
        time.sleep(pausa)

    def herramientas(self) -> list[str]:
        """Nombres de las herramientas que este token puede ver. Para diagnóstico."""
        r = self._post({"jsonrpc": "2.0", "id": next(self._ids),
                        "method": "tools/list", "params": {}})
        if "error" in r:
            raise RuntimeError(f"tools/list falló: {(r['error'] or {}).get('message')}")
        return [t.get("name") for t in (r.get("result") or {}).get("tools") or []]


def _texto(res: dict) -> str:
    return "\n".join(c.get("text") or "" for c in (res.get("content") or [])
                     if c.get("type") == "text")


def _contenido(res: dict):
    """
    Un resultado MCP trae `content` (bloques) y a veces `structuredContent`.

    Se prefiere `structuredContent` cuando existe, porque ya es un objeto. Si no,
    se intenta parsear el texto como JSON —así vienen los exports de ghl-mcp— y si
    tampoco, se devuelve el texto tal cual para que el que llama decida.
    """
    if isinstance(res.get("structuredContent"), (dict, list)):
        return res["structuredContent"]
    txt = _texto(res)
    if not txt:
        return None
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        return txt
