# -*- coding: utf-8 -*-
"""
Los clientes de ROASLytics SON las subcuentas de Sentinel Suite.

Hasta el 20-oct-2026 un cliente se daba de alta dos veces: en la suite (Subcuentas) y
otra vez en el panel de reportes. Quien olvidaba la segunda se encontraba, al abrir el
dashboard desde la suite, con «Este cliente todavía no está en reportes». De 14
subcuentas, 6 estaban así.

Ahora la suite es la única fuente: aquí se LEE su registro (`GET /api/v1/subaccounts`,
con el token de servicio) y se deja cada subcuenta activa dada de alta como cliente,
con su configuración de extracción sembrada (location de GHL, cuentas de Meta y de
Google, zona horaria y moneda). A un cliente nuevo se le encola además su primera
extracción, para que el dashboard exista sin que nadie pulse nada.

Cuándo corre:
  · al arrancar el servicio (en un hilo, unos segundos después);
  · cada SUITE_SINCRONIZAR_MIN minutos (10 por defecto), por si algo se perdió;
  · cuando la suite avisa (POST /admin/sincronizar, firmado por su gateway) al crear
    o editar una subcuenta: así el dashboard aparece al momento;
  · con el botón «Sincronizar con la suite» del panel.

Qué manda la suite y qué se conserva aquí:
  · La suite manda en nombre, location de GHL, cuentas de anuncios y zona horaria (si
    la tiene puesta). Son datos de IDENTIDAD del cliente y viven en un solo sitio.
  · Lo que es propio del reporte (productos, SOP de etapas, roles de vendedores) se
    configura en el panel de ROASLytics y NO se toca desde aquí.
  · Una subcuenta archivada en la suite no se borra aquí: borrar destruye enlaces
    empotrados en webs de clientes, y eso lo decide una persona en dos pasos.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

log = logging.getLogger("reportes.sincronizar")

SUITE_URL = os.environ.get("SUITE_URL", "").strip().rstrip("/")
# Token de servicio de la suite (ss_…, permiso subaccounts:read). Es el MISMO tipo de token
# que GHL_MCP_TOKEN, así que si no hay uno propio se reutiliza ese.
TOKEN = (os.environ.get("SUITE_SERVICE_TOKEN", "").strip()
         or (os.environ.get("GHL_MCP_TOKEN", "").strip()
             if os.environ.get("GHL_MCP_TOKEN", "").startswith("ss_") else ""))
CADA_MIN = int(os.environ.get("SUITE_SINCRONIZAR_MIN", "10"))

_cerrojo = threading.Lock()
_ultimo: dict = {"cuando": None, "resultado": None}


def activa() -> bool:
    return bool(SUITE_URL and TOKEN)


def subcuentas_de_la_suite(timeout: int = 15) -> list[dict]:
    """Las subcuentas ACTIVAS del registro de la suite. Lanza si la suite no contesta."""
    req = urllib.request.Request(
        f"{SUITE_URL}/api/v1/subaccounts",
        headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        datos = json.loads(r.read() or b"{}")
    return [s for s in (datos.get("subaccounts") or [])
            if isinstance(s, dict) and s.get("slug") and (s.get("status") or "active") == "active"]


def _cuentas(sub: dict) -> list[dict]:
    """De la forma de la suite (dos listas) a la del reporte (una lista con plataforma)."""
    out = []
    for cid in sub.get("meta_accounts") or []:
        if str(cid).strip():
            out.append({"plataforma": "Meta", "id": str(cid).strip()})
    for cid in sub.get("google_accounts") or []:
        if str(cid).strip():
            out.append({"plataforma": "Google", "id": str(cid).strip()})
    return out


def _misma_lista(a: list, b: list) -> bool:
    k = lambda c: (c.get("plataforma"), c.get("id"))        # noqa: E731
    return sorted(map(k, a or [])) == sorted(map(k, b or []))


def sincronizar(almacen, *, encolar: Optional[Callable[[str, str], object]] = None,
                ficha_crm: Optional[Callable[[str], dict]] = None,
                origen: str = "periodico") -> dict:
    """
    Una pasada. Devuelve un resumen {creados, actualizados, sinCambios, error}.

    `encolar(slug, por)` pone al cliente en la cola de extracción; `ficha_crm(location)`
    lee del CRM la zona horaria y la moneda cuando la suite no trae zona. Las dos se
    inyectan para que esto no dependa de main.py (y se pueda probar sin red).
    """
    res = {"creados": [], "actualizados": [], "sinCambios": 0, "error": None, "origen": origen}
    if not activa():
        res["error"] = "Sin SUITE_URL o sin token de servicio de la suite."
        return res
    if not _cerrojo.acquire(blocking=False):
        res["error"] = "Ya hay una sincronización en marcha."
        return res
    try:
        try:
            subs = subcuentas_de_la_suite()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as ex:
            res["error"] = f"La suite no devolvió las subcuentas: {ex}"
            log.warning("sincronización · %s", res["error"])
            return res

        for sub in subs:
            slug = str(sub["slug"]).strip().lower()
            nombre = (sub.get("name") or slug).strip()
            loc = (sub.get("ghl_location_id") or "").strip() or None
            tz = (sub.get("timezone") or "").strip() or None
            cuentas = _cuentas(sub)
            existente = almacen.cliente(slug)

            if existente is None:
                moneda = None
                if loc and ficha_crm:
                    try:
                        f = ficha_crm(loc)
                        tz = tz or f.get("tz")
                        moneda = f.get("moneda")
                    except Exception as ex:            # noqa: BLE001
                        log.warning("alta de %s: el CRM no contestó (%s)", slug, ex)
                almacen.guardar_cliente(slug, nombre, loc, tz)
                cfg = dict((almacen.cliente(slug) or {}).get("config") or {})
                cfg.update({"nombre": nombre, "slug": slug, "ghlLocationId": loc,
                            "cuentas": cuentas})
                if tz:
                    cfg["tz"] = tz
                    cfg["tzFuente"] = ("Subcuenta de Sentinel Suite" if sub.get("timezone")
                                       else f"Sub-cuenta de GoHighLevel {loc} (timezone)")
                if moneda:
                    cfg["moneda"] = moneda
                almacen.guardar_config(slug, cfg)
                res["creados"].append(slug)
                log.info("cliente creado desde la suite · %s · %s · %d cuenta(s) · tz %s",
                         slug, nombre, len(cuentas), tz or "sin zona")
                if encolar:
                    try:
                        encolar(slug, "suite")
                    except Exception as ex:            # noqa: BLE001
                        log.warning("no se pudo encolar la primera extracción de %s: %s", slug, ex)
                continue

            cfg = dict(existente.get("config") or {})
            cambios = []
            if nombre and cfg.get("nombre") != nombre:
                cfg["nombre"] = nombre; cambios.append("nombre")
            if loc and cfg.get("ghlLocationId") != loc:
                cfg["ghlLocationId"] = loc; cambios.append("ghl")
            if tz and cfg.get("tz") != tz:
                cfg["tz"] = tz; cfg["tzFuente"] = "Subcuenta de Sentinel Suite"; cambios.append("tz")
            if cuentas and not _misma_lista(cfg.get("cuentas") or [], cuentas):
                cfg["cuentas"] = cuentas; cambios.append("cuentas")
            if cambios:
                cfg.setdefault("slug", slug)
                almacen.guardar_cliente(slug, nombre, loc, tz)
                almacen.guardar_config(slug, cfg)
                res["actualizados"].append({"slug": slug, "cambios": cambios})
                log.info("cliente actualizado desde la suite · %s · %s", slug, ", ".join(cambios))
            else:
                res["sinCambios"] += 1
        return res
    finally:
        _ultimo.update({"cuando": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "resultado": res})
        _cerrojo.release()


def estado() -> dict:
    return {"activa": activa(), "cadaMin": CADA_MIN, **_ultimo}


def arrancar_periodico(almacen, **kw) -> None:
    """Hilo demonio: una pasada a los 15 s de arrancar y luego cada CADA_MIN minutos."""
    if not activa():
        log.info("sincronización con la suite apagada (falta SUITE_URL o el token de servicio)")
        return

    def bucle():
        time.sleep(15)
        while True:
            try:
                sincronizar(almacen, origen="periodico", **kw)
            except Exception as ex:                    # noqa: BLE001
                log.error("sincronización periódica: %s", ex)
            time.sleep(max(1, CADA_MIN) * 60)

    threading.Thread(target=bucle, name="sincronizar-suite", daemon=True).start()
