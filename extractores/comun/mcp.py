# -*- coding: utf-8 -*-
"""
Cliente MCP mínimo para hablar con el servicio `ghl-mcp` desde un programa.

El MCP está pensado para que lo use un modelo de lenguaje, no un script. Aun así
merece la pena hablarlo en vez de duplicar la extracción: `ghl-mcp` ya resuelve el
OAuth de agencia, el refresco de tokens y la caché de sub-cuentas, y todo eso ya
está en producción. Repetirlo aquí sería mantener dos veces lo mismo.

TODO lo que sigue está comprobado contra el mismo SDK que usa ghl-mcp
(@modelcontextprotocol/sdk 1.30) levantando una réplica de su http-server.js:

  1. `POST /mcp` con `Authorization: Bearer <GHL_MCP_HTTP_TOKEN>` → rol admin.
  2. NO hace falta `initialize`. El transporte va sin estado
     (`sessionIdGenerator: undefined`), así que cada POST crea un servidor nuevo y
     un `tools/call` a pelo funciona. Mandar `initialize` antes sería un viaje de
     red tirado, porque su resultado no se conserva para la petición siguiente.
  3. HAY QUE mandar `Accept: application/json, text/event-stream`. Con solo uno de
     los dos el SDK responde 406 "Client must accept both". Comprobado.
  4. La respuesta llega como SSE (`event: message` / `data: {...}`) aunque sea una
     sola petición. De ahí `_leer_sse`.
  5. El SDK valida la cabecera Host contra ALLOWED_HOSTS. Llamando por la red
     privada el Host es `ghl-mcp.railway.internal`, así que ESE nombre tiene que
     estar en la variable ALLOWED_HOSTS del servicio ghl-mcp o responde
     403 "Invalid Host". Comprobado también.
"""
from __future__ import annotations

import json
import logging

from .http import pedir

log = logging.getLogger("extractor.mcp")


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
        raise RuntimeError(f"Respuesta MCP ilegible: {texto[:300]}")
    return ultimo


class ClienteMCP:
    def __init__(self, base: str, token: str, *, timeout: int = 120):
        if not token:
            raise ValueError("Falta el token del MCP (GHL_MCP_TOKEN).")
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._id = 0

    def llamar(self, herramienta: str, argumentos: dict | None = None):
        """Invoca una herramienta y devuelve su resultado ya deserializado."""
        self._id += 1
        peticion = {"jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                    "params": {"name": herramienta, "arguments": argumentos or {}}}
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
        respuesta = _leer_sse(crudo)

        if "error" in respuesta:
            e = respuesta["error"] or {}
            raise RuntimeError(f"El MCP rechazó '{herramienta}': "
                               f"{e.get('message')} (código {e.get('code')})")
        res = respuesta.get("result") or {}
        # isError=true es un fallo de la HERRAMIENTA, no del protocolo: viene con
        # código 200 y sin "error". Si no se mira, un error se cuela como dato.
        if res.get("isError"):
            raise RuntimeError(f"'{herramienta}' devolvió error: "
                               f"{_texto(res)[:400]}")
        return _contenido(res)

    def herramientas(self) -> list[str]:
        """Nombres de las herramientas que este token puede ver. Para diagnóstico."""
        self._id += 1
        peticion = {"jsonrpc": "2.0", "id": self._id, "method": "tools/list", "params": {}}
        _c, crudo, _h = pedir(
            f"{self.base}/mcp", metodo="POST",
            cuerpo=json.dumps(peticion).encode("utf-8"),
            cabeceras={"Authorization": f"Bearer {self.token}",
                       "Content-Type": "application/json",
                       "Accept": "application/json, text/event-stream"},
            timeout=self.timeout)
        r = _leer_sse(crudo)
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
