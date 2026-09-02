# -*- coding: utf-8 -*-
"""
La zona horaria y la moneda salen del CRM, no de una casilla del panel.

    panel ──GET /admin/ficha?location=XXX──> reportes ──ghl_get_client──> GoHighLevel

POR QUÉ NO SE PREGUNTA
──────────────────────
Antes la zona horaria era un campo obligatorio del alta. Primero texto libre (una
trampa: `EST` o `Colombia` no dan error, dan fechas corridas), luego un desplegable de
418 zonas. Las dos versiones tienen el mismo defecto de fondo: **le piden al operador
un dato que el sistema ya conoce**, y una equivocación no se nota. El reporte sale, y
sale mal.

La sub-cuenta de GoHighLevel ya declara su `timezone` y su `currency`, y son EL dato
bueno, no una aproximación: es la zona con la que el propio CRM sella la hora de cada
lead y de cada oportunidad. Que el reporte use otra es exactamente lo que hace que los
números no cuadren con lo que el cliente ve en su CRM.

POR QUÉ EL CRM Y NO META NI GOOGLE
────────────────────────────────
Porque los días del reporte los ponen los leads, y los leads viven en el CRM. De las
plataformas de anuncios solo viene el gasto, y viene ya cuadrado en la zona de la
cuenta publicitaria, que no la decidimos nosotros. Una cuenta de anuncios creada por
la agencia puede estar en otra zona que el negocio — pasa a menudo — y en ese caso
tomar la zona de Meta desplazaría el día de CADA lead para arreglar el de los importes
de gasto: cambiar mil filas de sitio para colocar bien treinta.

Si algún día hace falta, Meta (`timezone_name` de la cuenta) y Google
(`customer.time_zone`) sirven de respaldo con la misma forma que esto.

SE PUEDE CORREGIR A MANO
────────────────────────
La detección no es un candado. Si la sub-cuenta está mal configurada en GoHighLevel,
el panel deja desbloquear el campo y elegir la zona a mano; lo que se guarda en la
config gana sobre lo detectado. Lo que ya no pasa es tener que teclearla siempre y
que un despiste salga en el reporte del cliente.
"""
from __future__ import annotations

import logging
import os
import pathlib
import sys
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query

log = logging.getLogger("reportes.ficha")

router = APIRouter()

TIMEOUT = int(os.environ.get("GHL_MCP_TIMEOUT_FICHA", "30"))


def _cliente_mcp():
    """
    El ClienteMCP vive con los extractores; se importa tarde, igual que en el refresco.

    `extractores/` no es un paquete instalado, solo una carpeta hermana dentro de la
    imagen, así que hay que ponerla en el path antes de importar.
    """
    raiz = pathlib.Path(__file__).resolve().parents[2] / "extractores"
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))
    from comun.mcp import ClienteMCP          # noqa: E402

    url = os.environ.get("GHL_MCP_URL", "")
    token = os.environ.get("GHL_MCP_TOKEN", "")
    if not url or not token:
        raise HTTPException(503, "Este servicio no tiene configurado el acceso al CRM "
                                 "(GHL_MCP_URL / GHL_MCP_TOKEN), así que no puede "
                                 "detectar la zona horaria. Ponla a mano.")
    return ClienteMCP(url, token, timeout=TIMEOUT)


def _zona_valida(tz: Optional[str]) -> bool:
    """Que exista de verdad. Un `timezone` raro en el CRM no se propaga a la config."""
    if not tz or not isinstance(tz, str):
        return False
    try:
        ZoneInfo(tz.strip())
        return True
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return False


def ficha_crm(location: str) -> dict:
    """
    Lo que el CRM sabe de una sub-cuenta: zona horaria, moneda, nombre y dónde está.

    Devuelve siempre un dict; `tz` puede venir a None si la sub-cuenta no la declara o
    declara una que no existe. Eso NO es un error del servicio: es información, y el
    panel la usa para pedir la zona a mano solo en ese caso.
    """
    mcp = _cliente_mcp()
    try:
        r = mcp.llamar("ghl_get_client", {"client": location})
    except Exception as ex:                       # noqa: BLE001
        log.warning("no se pudo leer la ficha de %s: %s", location, ex)
        raise HTTPException(502, f"El CRM no devolvió la sub-cuenta '{location}': {ex}")

    loc = (r or {}).get("location") if isinstance(r, dict) else None
    if not isinstance(loc, dict):
        loc = r if isinstance(r, dict) else {}
    neg = loc.get("business") if isinstance(loc.get("business"), dict) else {}

    tz = (loc.get("timezone") or neg.get("timezone") or "").strip() or None
    if not _zona_valida(tz):
        if tz:
            log.warning("la sub-cuenta %s declara una zona que no existe: %r", location, tz)
        tz = None

    moneda = (loc.get("currency") or "").strip().upper() or None
    ciudad = (loc.get("city") or neg.get("city") or "").strip() or None
    pais = (loc.get("country") or neg.get("country") or "").strip() or None

    return {"location": loc.get("id") or location,
            "nombre": (loc.get("name") or neg.get("name") or "").strip() or None,
            "tz": tz, "moneda": moneda, "ciudad": ciudad, "pais": pais,
            "web": (loc.get("website") or neg.get("website") or "").strip() or None,
            "fuente": "ghl"}


def montar(app, *, almacen, exige_admin, config_de=None):
    """Se monta desde main.py. `config_de` sirve para resolver el slug de un cliente."""

    @router.get("/admin/ficha", dependencies=[Depends(exige_admin)])
    def ficha_por_location(location: str = Query(..., min_length=6)):
        """
        Para el formulario de alta: todavía no hay cliente, solo un id de sub-cuenta.

        Es una sola llamada al CRM y tarda menos de un segundo, así que el panel la hace
        en cuanto pegas el location y rellena el resto de la ficha con lo que conteste.
        """
        return ficha_crm(location.strip())

    @router.get("/admin/ficha/{slug}", dependencies=[Depends(exige_admin)])
    def ficha_por_cliente(slug: str):
        """Para un cliente ya dado de alta: se saca su location de donde esté."""
        c = almacen.cliente(slug)
        if not c:
            raise HTTPException(404, f"El cliente '{slug}' no existe.")
        loc = None
        if config_de:
            loc = (config_de(slug) or {}).get("ghlLocationId")
        loc = loc or c.get("ghl_location_id")
        if not loc:
            raise HTTPException(409, "Este cliente no tiene location de GoHighLevel, así "
                                     "que no hay de dónde leer su zona horaria.")
        return ficha_crm(str(loc).strip())

    app.include_router(router)
    return router
