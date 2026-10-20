# -*- coding: utf-8 -*-
"""
El reporte dentro de la sub-cuenta de GoHighLevel: un enlace en su menú lateral.

    reporte publicado ──> enlace general (enlace_general.py) ──> ghl_menu_reporte (MCP)
                                                                  └─> «Reporte ROASLytics» en el
                                                                      menú de la sub-cuenta

Hasta el 20-oct-2026 esto se hacía a mano: copiar el iframe del panel y crear el «Custom
Menu Link» en la agencia de GoHighLevel, eligiendo la sub-cuenta. Se olvidaba, y el
cliente no veía su reporte dentro de su CRM. Ahora se instala solo tras la primera
publicación y se repara en las siguientes si hiciera falta.

Cómo: el MCP de GoHighLevel expone `ghl_menu_reporte` (crea o actualiza un Custom Menu
Link de empresa, visible solo en esa sub-cuenta, que abre la URL en un iframe). Hace
falta que la app de agencia del Marketplace tenga el permiso `custom-menu-link.write`;
sin él, el MCP devuelve error y aquí se guarda en la configuración del cliente
(`ghlMenuError`) para que el panel lo enseñe y el botón «Instalar» lo reintente.

La URL que se instala es la del enlace general empotrable, por la suite:
`https://suite…/reportes/r/<token>/embed`. No cambia nunca (ver enlace_general.py), así que
el menú se instala una vez; si el enlace se rota, la siguiente publicación lo actualiza.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from . import enlace_general, rutas_ficha, suite

log = logging.getLogger("reportes.menu_ghl")
router = APIRouter()

TITULO = os.environ.get("GHL_MENU_TITULO", "Reporte ROASLytics")
ICONO = os.environ.get("GHL_MENU_ICONO", "chart-line")
# Apagado explícito (p. ej. en pruebas o mientras falte el permiso en el Marketplace).
ACTIVO = os.environ.get("GHL_MENU_AUTO", "1").strip().lower() not in ("0", "false", "no")


def url_publica(token: str) -> str:
    """La URL empotrable tal como la abre el navegador: por la suite si existe."""
    base = (suite.SUITE_URL + suite.RUTA_SUITE) if suite.SUITE_URL else os.environ.get("URL_PUBLICA", "").rstrip("/")
    return f"{base}/r/{token}/embed"


def estado(almacen, slug: str) -> dict:
    c = almacen.cliente(slug)
    if not c:
        raise HTTPException(404, f"El cliente '{slug}' no existe.")
    cfg = c.get("config") or {}
    e = enlace_general.buscar(almacen, slug)
    return {"cliente": slug, "titulo": TITULO, "activo": ACTIVO,
            "location": cfg.get("ghlLocationId"),
            "url": url_publica(e["token"]) if e and (e.get("dominios") or []) else None,
            "menu": cfg.get("ghlMenu"), "error": cfg.get("ghlMenuError")}


def instalar(almacen, slug: str, *, forzar: bool = False) -> dict:
    """
    Deja el menú puesto en la sub-cuenta. Idempotente y barato cuando ya está.

    Devuelve {"estado": "instalado" | "ya-estaba" | "sin-location" | "sin-enlace" | "error"}.
    """
    c = almacen.cliente(slug)
    if not c:
        raise HTTPException(404, f"El cliente '{slug}' no existe.")
    cfg = dict(c.get("config") or {})
    loc = (cfg.get("ghlLocationId") or "").strip()
    if not loc:
        return {"estado": "sin-location"}
    e = enlace_general.buscar(almacen, slug)
    if not e or not (e.get("dominios") or []):
        return {"estado": "sin-enlace"}
    url = url_publica(e["token"])
    ya = cfg.get("ghlMenu") or {}
    if not forzar and ya.get("url") == url and ya.get("titulo") == TITULO:
        return {"estado": "ya-estaba", **ya}

    try:
        r = rutas_ficha._cliente_mcp().llamar("ghl_menu_reporte",
                                              {"client": loc, "titulo": TITULO, "url": url, "icono": ICONO})
        if not isinstance(r, dict):
            raise RuntimeError(f"respuesta inesperada del MCP: {str(r)[:200]}")
    except Exception as ex:                        # noqa: BLE001
        cfg["ghlMenuError"] = str(ex)[:400]
        almacen.guardar_config(slug, cfg)
        log.warning("menú de GoHighLevel · %s · no se pudo instalar: %s", slug, ex)
        return {"estado": "error", "error": cfg["ghlMenuError"]}

    cfg["ghlMenu"] = {"id": r.get("id"), "url": url, "titulo": TITULO, "accion": r.get("accion"),
                      "cuando": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}
    cfg.pop("ghlMenuError", None)
    almacen.guardar_config(slug, cfg)
    log.info("menú de GoHighLevel · %s · %s (%s) · %s", slug, r.get("accion"), r.get("id"), url)
    return {"estado": "instalado", **cfg["ghlMenu"]}


def tras_publicar(almacen, slug: str) -> None:
    """Gancho tras cada publicación: nunca tumba una publicación que ya salió bien."""
    if not ACTIVO:
        return
    try:
        instalar(almacen, slug)
    except Exception as ex:                        # noqa: BLE001
        log.warning("menú de GoHighLevel · %s · %s", slug, ex)


def montar(app, *, almacen, exige_admin):
    @router.get("/admin/menu-ghl/{slug}", dependencies=[Depends(exige_admin)])
    def ver(slug: str):
        return estado(almacen, slug)

    @router.post("/admin/menu-ghl/{slug}", dependencies=[Depends(exige_admin)])
    def poner(slug: str):
        """Instala (o reinstala) el menú ahora. Para reintentar tras arreglar el permiso."""
        r = instalar(almacen, slug, forzar=True)
        if r["estado"] == "sin-location":
            raise HTTPException(409, "Este cliente no tiene sub-cuenta de GoHighLevel.")
        if r["estado"] == "sin-enlace":
            raise HTTPException(409, "Todavía no hay enlace general empotrable: publica el reporte primero.")
        if r["estado"] == "error":
            raise HTTPException(502, r["error"])
        return r

    app.include_router(router)
