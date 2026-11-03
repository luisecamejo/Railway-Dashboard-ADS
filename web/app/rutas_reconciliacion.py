# -*- coding: utf-8 -*-
"""
Rutas de la Reconciliación y de la Ficha de cliente (ver reconciliacion.py).

    /s/{slug}/reconciliacion/            la página (sola, sin el dashboard: es lo que ve el
                                         rol `vendedor`; el equipo la ve también como pestaña)
    /s/{slug}/reconciliacion/api         JSON: lotes + items (+ qué puede hacer quien mira)
    /s/{slug}/reconciliacion/api/lotes   POST: Claude registra una reconciliación entera
    /s/{slug}/reconciliacion/api/comprobar   GET: ¿estos contactos ya se reconciliaron?
    /s/{slug}/reconciliacion/api/items/{id}/decidir|cerrar|reabrir
    /s/{slug}/reconciliacion/api/lotes/{id}/cerrar      DELETE /lotes/{id}
    /s/{slug}/reconciliacion/export.xlsx el libro con el formato de siempre
    /s/{slug}/api/ficha                  GET/PUT: la Ficha de cliente (pestaña Ajustes)
    /reconciliacion.js                   el código de la página / pestaña

Quién puede qué lo dice `reconciliacion.PERMISOS` por rol de la suite (admin, equipo,
vendedor). La identidad la pone el middleware de la suite; aquí se recibe la persona
ya comprobada (`exige_persona`) y solo se mira el rol.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import pathlib
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import reconciliacion as rc
from . import seguridad as seg
from . import suite

log = logging.getLogger("reportes.reconciliacion")

RUTA_JS = pathlib.Path(__file__).resolve().parents[1] / "visor" / "reconciliacion.js"
_js: dict = {"code": b"", "hash": ""}


def _cargar_js() -> dict:
    if not _js["hash"] and RUTA_JS.exists():
        code = RUTA_JS.read_bytes()
        _js.update({"code": code, "hash": hashlib.sha256(code).hexdigest()[:12]})
    return _js


def _dinero_moneda(config: dict, snapshot: Optional[dict]) -> str:
    return str((config or {}).get("moneda") or (snapshot or {}).get("moneda") or "USD").upper()


def montar(app, *, almacen, exige_persona: Callable, leer_cuerpo: Callable, config_de: Callable):
    """
    exige_persona(peticion, slug) → Persona (rol admin/equipo/vendedor) o lanza 401/403.
    leer_cuerpo(peticion) → dict del JSON (con tope de tamaño).
    config_de(slug) → config del cliente (donde vive la ficha).
    """
    router = APIRouter()

    def _persona(peticion: Request, slug: str, accion: str):
        p = exige_persona(peticion, slug)
        if not rc.puede(p.rol, accion):
            raise HTTPException(403, {
                "ver": "No tienes acceso a la reconciliación de este cliente.",
                "crear": "Registrar una reconciliación es del equipo de Sentinel (admin o equipo).",
                "decidir": "No puedes decidir sobre esta reconciliación.",
                "cerrar": "Cerrar un registro (darlo por aplicado en el CRM) es del equipo de Sentinel.",
                "borrar": "Solo un administrador de ROASLytics puede borrar un lote.",
                "ficha": "La ficha del cliente la edita el equipo de Sentinel.",
                "exportar": "No puedes descargar esta reconciliación.",
            }.get(accion, "Sin permiso."))
        return p

    def _cliente(slug: str) -> dict:
        c = almacen.cliente(slug)
        if not c:
            raise HTTPException(404, "Este cliente no existe en ROASLytics.")
        return c

    async def _json(peticion: Request):
        try:
            crudo = await leer_cuerpo(peticion, 4, "cuerpo")
        except ValueError as ex:
            raise HTTPException(413, str(ex))
        if not crudo:
            return {}
        try:
            return json.loads(crudo)
        except ValueError:
            raise HTTPException(400, "El cuerpo no es JSON válido.")

    def _privado(datos, codigo=200):
        return JSONResponse(datos, status_code=codigo,
                            headers=seg.cabeceras({"Cache-Control": "private, no-store"}))

    # ── el código de la página ─────────────────────────────────────────────────────
    @router.get("/reconciliacion.js")
    def reconciliacion_js(v: str = ""):
        js = _cargar_js()
        if not js["hash"]:
            raise HTTPException(503, "Falta el código de la reconciliación en el servicio.")
        inmutable = v == js["hash"]
        return Response(js["code"], media_type="application/javascript; charset=utf-8",
                        headers={"Cache-Control": "public, max-age=31536000, immutable" if inmutable
                                 else "public, max-age=300", **seg.cabeceras()})

    # ── la página sola ─────────────────────────────────────────────────────────────
    @router.get("/s/{slug}/reconciliacion")
    def pagina_sin_barra(slug: str):
        return RedirectResponse(f"/s/{slug}/reconciliacion/", status_code=308)

    @router.get("/s/{slug}/reconciliacion/", response_class=HTMLResponse)
    def pagina(slug: str, peticion: Request):
        p = _persona(peticion, slug, "ver")
        c = _cliente(slug)
        pre = suite.prefijo(peticion)
        js = _cargar_js()
        e = html.escape
        nombre = e(c.get("nombre") or slug)
        barra = (f'<script src="{e(suite.SUITE_URL)}/suite-bar.js" data-tool="reportes" defer></script>'
                 if suite.SUITE_URL else "")
        volver = ("" if p.rol == "vendedor" else
                  f'<a class="rc-volver" href="{e(pre)}/s/{e(slug)}/">← Dashboard completo</a>')
        doc = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet">
<title>Reconciliación · {nombre} · ROASLytics</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>:root{{--bg:#0A0A0F;--pan:#101018;--pan2:#171724;--line:#23232f;--tx:#ECECF4;--dim:#8A8AA3;--faint:#5c5c72;--pu:#7C3AED;--pul:#a78bfa;--gr:#22c55e;--am:#f59e0b;--rd:#ef4444;--bl:#3b82f6;--d:'Montserrat',system-ui,sans-serif;--m:'JetBrains Mono',ui-monospace,monospace}}
html,body{{margin:0;background:var(--bg);color:var(--tx);font:13.5px/1.5 var(--d)}}
.rc-top{{padding:22px 28px 0;display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap}}
.rc-top h1{{margin:0;font-size:20px}} .rc-top p{{margin:4px 0 0;color:var(--dim)}}
.rc-volver{{color:var(--pul);text-decoration:none;font-weight:600}}
.rc-wrap{{padding:16px 28px 48px}}</style></head>
<body><div class="rc-top"><div><h1>Reconciliación · {nombre}</h1>
<p>Lo que Claude propone tras cruzar los pagos y las llamadas con el CRM, la decisión de cada fila y el historial de lo aplicado.</p></div>{volver}</div>
<div class="rc-wrap"><div id="reconc" data-pre="{e(pre)}" data-slug="{e(slug)}" data-rol="{e(p.rol)}" data-nombre="{nombre}" data-email="{e(p.email or '')}" data-suite="{e(suite.SUITE_URL)}"></div></div>
<script src="{e(pre)}/reconciliacion.js?v={js['hash']}"></script>{barra}</body></html>"""
        cab = seg.cabeceras({"Cache-Control": "private, no-store"}, csp_reporte=True)
        if suite.SUITE_URL:
            cab["Content-Security-Policy"] = (cab["Content-Security-Policy"]
                                              .replace("script-src 'self'", f"script-src 'self' {suite.SUITE_URL}")
                                              .replace("connect-src 'self'", f"connect-src 'self' {suite.SUITE_URL}"))
        return HTMLResponse(doc, headers=cab)

    # ── API ────────────────────────────────────────────────────────────────────────
    @router.get("/s/{slug}/reconciliacion/api")
    def listar(slug: str, peticion: Request, lote: str = "", estado: str = ""):
        p = _persona(peticion, slug, "ver")
        c = _cliente(slug)
        lotes = almacen.reconc_lotes(slug)
        items = almacen.reconc_items(slug, lote or None)
        if estado in ("abierto", "decidido", "cerrado"):
            items = [i for i in items if i.get("estado") == estado]
        elif estado == "pendiente":
            items = [i for i in items if i.get("estado") != "cerrado"]
        todos = almacen.reconc_items(slug) if (lote or estado) else items
        cfg = config_de(slug) or {}
        ficha = cfg.get("ficha") or {}
        return _privado({
            "cliente": {"slug": slug, "nombre": c.get("nombre")},
            "rol": p.rol,
            "permisos": sorted(rc.PERMISOS.get(p.rol, set())),
            "moneda": _dinero_moneda(cfg, None),
            "ficha": {k: ficha.get(k) for k in ("criterio_reconciliacion", "apps_reconciliacion",
                                                  "cohorte", "pipeline_nombre", "etapa_cierre")},
            "lotes": [rc.resumen_lote(l, todos) for l in lotes],
            "items": items,
            "total": {"abiertos": sum(1 for i in todos if i.get("estado") == "abierto"),
                      "decididos": sum(1 for i in todos if i.get("estado") == "decidido"),
                      "cerrados": sum(1 for i in todos if i.get("estado") == "cerrado")},
        })

    @router.get("/s/{slug}/reconciliacion/api/comprobar")
    def comprobar(slug: str, peticion: Request, opps: str = "", contactos: str = "",
                  telefonos: str = "", emails: str = ""):
        _persona(peticion, slug, "ver")
        _cliente(slug)
        parte = lambda s: [x.strip() for x in str(s or "").split(",") if x.strip()]  # noqa: E731
        claves = rc.claves_consulta(parte(opps), parte(contactos), parte(telefonos), parte(emails))
        if not claves:
            raise HTTPException(400, "Pasa opps, contactos, telefonos o emails separados por comas.")
        encontrados = rc.ya_reconciliados(almacen.reconc_items(slug), claves)
        return _privado({"consultadas": len(claves), "reconciliados": encontrados,
                         "sin_registro": [k for k in claves if k not in encontrados]})

    @router.post("/s/{slug}/reconciliacion/api/lotes")
    async def crear_lote(slug: str, peticion: Request):
        p = _persona(peticion, slug, "crear")
        _cliente(slug)
        cuerpo = await _json(peticion)
        if not isinstance(cuerpo, dict):
            raise HTTPException(400, "Manda un objeto con `lote` e `items`.")
        crudos = cuerpo.get("items")
        if not isinstance(crudos, list) or not crudos:
            raise HTTPException(400, "`items` tiene que ser una lista con al menos una fila.")
        if len(crudos) > 2000:
            raise HTTPException(413, "Demasiadas filas en un solo lote (máximo 2000).")
        forzar = bool(cuerpo.get("forzar"))
        por = p.email or p.rol
        try:
            lote = rc.normalizar_lote(cuerpo.get("lote") or {}, por)
            existentes = almacen.reconc_items(slug)
            nuevos, omitidos = [], []
            for i, crudo in enumerate(crudos):
                it = rc.normalizar_item(crudo, lote, por)
                dup = None if forzar else rc.duplicado_de(it, existentes)
                if dup:
                    omitidos.append({"fila": i, "contacto": it.get("contacto") or it.get("cliente_plataforma"),
                                     "motivo": f"ya reconciliado ({dup.get('estado')}"
                                               + (f", decisión {dup.get('decision')}" if dup.get("decision") else "") + ")",
                                     "existente": rc.resumen_item(dup)})
                    continue
                nuevos.append(it)
                existentes.append(it)   # dos filas iguales dentro del mismo lote tampoco
        except ValueError as ex:
            raise HTTPException(400, str(ex))
        if not nuevos:
            return _privado({"lote": None, "creados": 0, "omitidos": omitidos,
                             "aviso": "Todas las filas ya estaban reconciliadas: no se creó ningún lote."})
        almacen.reconc_guardar_lote(slug, lote)
        almacen.reconc_guardar_items(slug, nuevos)
        log.info("reconciliación %s · lote %s · %d filas (%d omitidas) · por %s",
                 slug, lote["id"], len(nuevos), len(omitidos), por)
        return _privado({"lote": rc.resumen_lote(lote, nuevos), "creados": len(nuevos),
                         "omitidos": omitidos, "items": nuevos}, 201)

    def _item(slug: str, id_: str) -> dict:
        it = almacen.reconc_item(slug, id_)
        if not it:
            raise HTTPException(404, "Ese registro no existe.")
        return it

    @router.post("/s/{slug}/reconciliacion/api/items/{id_}/decidir")
    async def decidir(slug: str, id_: str, peticion: Request):
        p = _persona(peticion, slug, "decidir")
        it = _item(slug, id_)
        cuerpo = await _json(peticion) or {}
        try:
            rc.decidir(it, cuerpo.get("decision"), cuerpo.get("nota"), p.email or p.rol)
        except ValueError as ex:
            raise HTTPException(400, str(ex))
        almacen.reconc_guardar_items(slug, [it])
        return _privado({"item": it})

    @router.post("/s/{slug}/reconciliacion/api/items/{id_}/cerrar")
    async def cerrar(slug: str, id_: str, peticion: Request):
        p = _persona(peticion, slug, "cerrar")
        it = _item(slug, id_)
        cuerpo = await _json(peticion) or {}
        try:
            rc.cerrar(it, cuerpo.get("resultado"), p.email or p.rol)
        except ValueError as ex:
            raise HTTPException(400, str(ex))
        almacen.reconc_guardar_items(slug, [it])
        _cerrar_lote_si_toca(slug, it["lote"])
        return _privado({"item": it})

    @router.post("/s/{slug}/reconciliacion/api/items/{id_}/reabrir")
    def reabrir(slug: str, id_: str, peticion: Request):
        p = _persona(peticion, slug, "cerrar")
        it = _item(slug, id_)
        rc.reabrir(it, p.email or p.rol)
        almacen.reconc_guardar_items(slug, [it])
        lote = almacen.reconc_lote(slug, it["lote"])
        if lote and lote.get("estado") == "cerrado":
            lote.update({"estado": "abierto", "cerrado_en": None})
            almacen.reconc_guardar_lote(slug, lote)
        return _privado({"item": it})

    def _cerrar_lote_si_toca(slug: str, lote_id: str) -> None:
        lote = almacen.reconc_lote(slug, lote_id)
        if not lote:
            return
        pend = [i for i in almacen.reconc_items(slug, lote_id) if i.get("estado") != "cerrado"]
        if not pend and lote.get("estado") != "cerrado":
            lote.update({"estado": "cerrado", "cerrado_en": rc.ahora()})
            almacen.reconc_guardar_lote(slug, lote)

    @router.post("/s/{slug}/reconciliacion/api/lotes/{lote_id}/cerrar")
    async def cerrar_lote(slug: str, lote_id: str, peticion: Request):
        """Cierra de golpe todo lo DECIDIDO del lote (lo que ya se aplicó en el CRM)."""
        p = _persona(peticion, slug, "cerrar")
        if not almacen.reconc_lote(slug, lote_id):
            raise HTTPException(404, "Ese lote no existe.")
        cuerpo = await _json(peticion) or {}
        solo = cuerpo.get("solo_decision")   # p. ej. "SI": cerrar solo las aprobadas
        cerrados = []
        for it in almacen.reconc_items(slug, lote_id):
            if it.get("estado") != "decidido":
                continue
            if solo and it.get("decision") != str(solo).upper():
                continue
            rc.cerrar(it, cuerpo.get("resultado"), p.email or p.rol)
            cerrados.append(it)
        if cerrados:
            almacen.reconc_guardar_items(slug, cerrados)
        _cerrar_lote_si_toca(slug, lote_id)
        return _privado({"cerrados": len(cerrados), "lote": almacen.reconc_lote(slug, lote_id)})

    @router.delete("/s/{slug}/reconciliacion/api/lotes/{lote_id}")
    def borrar_lote(slug: str, lote_id: str, peticion: Request):
        _persona(peticion, slug, "borrar")
        n = almacen.reconc_borrar_lote(slug, lote_id)
        if n < 0:
            raise HTTPException(404, "Ese lote no existe.")
        return _privado({"borrado": lote_id, "items": n})

    # ── Excel ──────────────────────────────────────────────────────────────────────
    @router.get("/s/{slug}/reconciliacion/export.xlsx")
    def exportar(slug: str, peticion: Request, lote: str = "", estado: str = ""):
        _persona(peticion, slug, "exportar")
        c = _cliente(slug)
        lotes = almacen.reconc_lotes(slug)
        if lote:
            lotes = [l for l in lotes if l["id"] == lote]
            if not lotes:
                raise HTTPException(404, "Ese lote no existe.")
        items = almacen.reconc_items(slug, lote or None)
        if estado == "pendiente":
            items = [i for i in items if i.get("estado") != "cerrado"]
        try:
            datos = rc.excel(c, lotes, items, _dinero_moneda(config_de(slug) or {}, None))
        except ImportError:
            raise HTTPException(503, "Falta openpyxl en el servicio: no se puede generar el Excel.")
        nombre = f"Reconciliacion_{slug}" + (f"_{lotes[0].get('periodo_desde') or ''}" if lote else "") + ".xlsx"
        return Response(datos, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{nombre}"',
                                 **seg.cabeceras({"Cache-Control": "private, no-store"})})

    # ── la Ficha de cliente ────────────────────────────────────────────────────────
    @router.get("/s/{slug}/api/ficha")
    def ficha_leer(slug: str, peticion: Request):
        p = exige_persona(peticion, slug)
        if p.rol == "vendedor":
            raise HTTPException(403, "La ficha del cliente es del equipo de Sentinel.")
        c = _cliente(slug)
        cfg = config_de(slug) or {}
        snap = almacen.snapshot(slug) or {}
        return _privado({
            "identidad": {   # solo lectura: viene de la suite
                "nombre": c.get("nombre"), "slug": slug, "ghl_location_id": c.get("ghl_location_id"),
                "tz": cfg.get("tz") or c.get("tz"),
                "cuentas": cfg.get("cuentas") or [],
                "moneda_detectada": cfg.get("moneda") or snap.get("moneda"),
            },
            "pipelines_crm": snap.get("pipelines") or [],
            "stages_crm": snap.get("stages") or [],
            "ficha": cfg.get("ficha") or {},
            "editable": rc.puede(p.rol, "ficha"),
        })

    @router.put("/s/{slug}/api/ficha")
    async def ficha_guardar(slug: str, peticion: Request):
        _persona(peticion, slug, "ficha")
        _cliente(slug)
        cuerpo = await _json(peticion)
        try:
            ficha = rc.normalizar_ficha(cuerpo if isinstance(cuerpo, dict) else {})
        except ValueError as ex:
            raise HTTPException(400, str(ex))
        cfg = dict(config_de(slug) or {})
        cfg["ficha"] = ficha
        if ficha.get("moneda"):
            cfg["moneda"] = ficha["moneda"].upper()
        almacen.guardar_config(slug, cfg)
        return _privado({"ficha": ficha})

    app.include_router(router)
