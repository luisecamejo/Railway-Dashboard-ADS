"""
Servicio de reportes · Sentinel Marketing
────────────────────────────────────────
Sirve un único visor (cacheable, igual para todos los clientes) y el snapshot de datos
de cada cliente por separado, detrás de un enlace con token.

Rutas públicas
    GET  /r/{token}                  → redirige a /r/{token}/
    GET  /r/{token}/                 → el visor
    GET  /r/{token}/snapshot.json    → los datos de ese cliente, según el modo del enlace
    GET  /app.js                     → el dashboard (versionado por hash, cacheable)
    GET  /salud                      → healthcheck
    GET  /robots.txt                 → prohibido indexar

Panel
    GET  /admin                      → la página (en rutas_panel.py); pide el token dentro

Rutas de administración (cabecera X-Admin-Token)
    GET  /admin/estado
    POST /admin/visor                → el dashboard.html de una pieza
    POST /admin/clientes
    POST /admin/snapshots/{slug}
    GET  /admin/snapshots/{slug}
    POST /admin/enlaces
    GET  /admin/enlaces
    POST /admin/enlaces/{token}/revocar
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                              RedirectResponse, Response)

from .almacen import abrir_almacen
from .rutas_panel import router as router_panel
from .privacidad import MODOS, aplicar
from .visor import partir_html

log = logging.getLogger("reportes")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
VERSION = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "dev")[:7]

app = FastAPI(title="Reportes Sentinel", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.include_router(router_panel)

almacen = abrir_almacen()

# ── el visor vive en el almacén, no en el repositorio ─────────────────────────
# Se sube con POST /admin/visor y se cachea en memoria. El hash de app.js versiona la
# URL, así que el navegador puede cachearlo para siempre y aun así recibir el nuevo
# cuando se sube un dashboard actualizado.
_cache: dict = {"hash": None, "index": None, "app": b"", "subido": None}


def _cargar_visor(forzar: bool = False) -> Optional[dict]:
    if _cache["hash"] and not forzar:
        return _cache
    v = almacen.visor()
    if not v:
        return None
    _cache.update({
        "hash": v["hash"],
        "index": v["index"].replace("'/app.js'", f"'/app.js?v={v['hash']}'"),
        "app": v["app"].encode("utf-8"),
        "subido": str(v.get("subido")),
    })
    log.info("visor cargado · app.js %.0f KB · hash %s",
             len(_cache["app"]) / 1024, _cache["hash"])
    return _cache


_cargar_visor()
log.info("servicio arriba · almacén %s · visor %s",
         almacen.tipo, _cache["hash"] or "SIN CARGAR")


# ══════════════════════════════════════════════════════════════════════════════
#  Utilidades
# ══════════════════════════════════════════════════════════════════════════════
def exige_admin(x_admin_token: str = Header(default="")) -> None:
    if not ADMIN_TOKEN:
        raise HTTPException(503, "El servicio no tiene ADMIN_TOKEN configurado.")
    if not hmac.compare_digest(x_admin_token or "", ADMIN_TOKEN):
        raise HTTPException(401, "Token de administración inválido.")


def _resolver_enlace(token: str) -> dict:
    e = almacen.enlace(token)
    if not e:
        raise HTTPException(404, "Este reporte no existe.")
    if e.get("revocado"):
        raise HTTPException(403, "Este enlace fue revocado.")
    cad = e.get("caduca")
    if cad:
        if isinstance(cad, str):
            cad = datetime.fromisoformat(cad)
        if cad.tzinfo is None:
            cad = cad.replace(tzinfo=timezone.utc)
        if cad < datetime.now(timezone.utc):
            raise HTTPException(403, "Este enlace ha caducado.")
    return e


_PAGINA_SIN_VISOR = (
    "<!doctype html><meta charset=utf-8><title>Reporte no disponible</title>"
    "<style>body{font:15px/1.6 system-ui;background:#0b0f14;color:#93a1b5;"
    "display:grid;place-items:center;height:100vh;margin:0;text-align:center;padding:24px}"
    "b{color:#e8edf4}</style>"
    "<div><p><b>El reporte todavía no está disponible.</b></p>"
    "<p>El visor no se ha cargado en el servicio. Avísale a tu contacto en Sentinel.</p></div>")

CABECERAS_PRIVADAS = {
    "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


# ══════════════════════════════════════════════════════════════════════════════
#  Público
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/salud")
def salud():
    v = _cargar_visor()
    return {"ok": True, "version": VERSION, "almacen": almacen.tipo,
            "visor": (v or {}).get("hash"), "visor_subido": (v or {}).get("subido")}


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return "User-agent: *\nDisallow: /\n"


@app.get("/", response_class=HTMLResponse)
def raiz():
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Reportes</title>"
        "<style>body{font:15px/1.6 system-ui;background:#0b0f14;color:#93a1b5;"
        "display:grid;place-items:center;height:100vh;margin:0;text-align:center}"
        "a{color:#8b7cf6}</style>"
        "<p>Servicio de reportes. Se accede con un enlace directo.<br>"
        "<a href=\"/admin\">Panel de administración</a></p>",
        headers=CABECERAS_PRIVADAS)


@app.get("/app.js")
def app_js(v: str = Query(default="")):
    vis = _cargar_visor()
    if not vis:
        raise HTTPException(503, "El visor no está cargado todavía.")
    inmutable = v == vis["hash"]
    return Response(
        vis["app"], media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "public, max-age=31536000, immutable" if inmutable
                                  else "public, max-age=300", **CABECERAS_PRIVADAS})


@app.get("/r/{token}")
def visor_sin_barra(token: str):
    return RedirectResponse(f"/r/{token}/", status_code=308)


@app.get("/r/{token}/", response_class=HTMLResponse)
def visor(token: str):
    _resolver_enlace(token)
    vis = _cargar_visor()
    if not vis:
        return HTMLResponse(_PAGINA_SIN_VISOR, status_code=503,
                            headers=CABECERAS_PRIVADAS)
    almacen.marcar_acceso(token)
    return HTMLResponse(vis["index"], headers={
        "Cache-Control": "private, max-age=60", **CABECERAS_PRIVADAS})


@app.get("/r/{token}/snapshot.json")
def snapshot(token: str):
    e = _resolver_enlace(token)
    datos = almacen.snapshot(e["cliente"])
    if datos is None:
        raise HTTPException(404, "Todavía no hay datos publicados para este cliente.")
    return JSONResponse(aplicar(datos, e.get("modo") or "cliente"), headers={
        "Cache-Control": "private, no-store", **CABECERAS_PRIVADAS})


# ══════════════════════════════════════════════════════════════════════════════
#  Administración
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/admin/estado", dependencies=[Depends(exige_admin)])
def admin_estado():
    v = _cargar_visor()
    return {"version": VERSION, "almacen": almacen.tipo,
            "visor": {"hash": (v or {}).get("hash"), "subido": (v or {}).get("subido"),
                      "bytes": len((v or {}).get("app") or b"")},
            "clientes": almacen.clientes()}


@app.post("/admin/visor", dependencies=[Depends(exige_admin)])
async def admin_subir_visor(peticion: Request):
    """Recibe el dashboard.html de una pieza, lo parte y guarda el visor.

    Actualizar el dashboard de TODOS los clientes es esta única llamada: no hace falta
    volver a desplegar el servicio ni regenerar ningún snapshot.
    """
    crudo = await peticion.body()
    if not crudo:
        raise HTTPException(400, "El cuerpo está vacío: manda el dashboard.html completo.")
    try:
        html = crudo.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "El archivo no es UTF-8.")
    try:
        partes = partir_html(html)
    except ValueError as ex:
        raise HTTPException(422, str(ex))

    almacen.guardar_visor(partes["index"], partes["app"], partes["hash"])
    _cargar_visor(forzar=True)
    log.info("visor actualizado · hash %s · app.js %.0f KB",
             partes["hash"], len(partes["app"]) / 1024)
    return {"ok": True, "hash": partes["hash"],
            "index_kb": round(len(partes["index"]) / 1024, 1),
            "app_kb": round(len(partes["app"]) / 1024, 1),
            "leads_en_el_archivo_subido": len(partes["datos"].get("leads") or [])}


@app.post("/admin/clientes", dependencies=[Depends(exige_admin)])
def admin_cliente(cuerpo: dict = Body(...)):
    slug = (cuerpo.get("slug") or "").strip().lower()
    if not slug or not slug.replace("-", "").isalnum():
        raise HTTPException(400, "slug obligatorio, solo letras, números y guiones.")
    nombre = (cuerpo.get("nombre") or "").strip()
    if not nombre:
        raise HTTPException(400, "nombre obligatorio.")
    return almacen.guardar_cliente(slug, nombre, cuerpo.get("ghl_location_id"),
                                   cuerpo.get("tz"), cuerpo.get("fuentes"))


@app.post("/admin/snapshots/{slug}", dependencies=[Depends(exige_admin)])
async def admin_publicar(slug: str, peticion: Request):
    if not almacen.cliente(slug):
        raise HTTPException(404, f"El cliente '{slug}' no existe. Créalo primero.")
    try:
        datos = json.loads(await peticion.body())
    except Exception as ex:
        raise HTTPException(400, f"El cuerpo no es JSON válido: {ex}")

    problemas = validar(datos)
    if problemas:
        raise HTTPException(422, {"error": "El snapshot no pasa las comprobaciones",
                                  "problemas": problemas})
    reg = almacen.publicar_snapshot(slug, datos)
    almacen.purgar_snapshots(slug, conservar=30)
    log.info("snapshot publicado · %s · %s leads · %.0f KB",
             slug, reg.get("n_leads"), (reg.get("bytes") or 0) / 1024)
    return {"ok": True, **{k: str(v) for k, v in reg.items()}}


@app.get("/admin/snapshots/{slug}", dependencies=[Depends(exige_admin)])
def admin_historial(slug: str):
    return {"cliente": slug, "historial": [
        {k: str(v) for k, v in h.items()} for h in almacen.historial(slug)]}


@app.post("/admin/enlaces", dependencies=[Depends(exige_admin)])
def admin_crear_enlace(cuerpo: dict = Body(...)):
    slug = (cuerpo.get("cliente") or "").strip().lower()
    if not almacen.cliente(slug):
        raise HTTPException(404, f"El cliente '{slug}' no existe.")
    modo = (cuerpo.get("modo") or "cliente").strip().lower()
    if modo not in MODOS:
        raise HTTPException(400, f"modo debe ser uno de {MODOS}")
    caduca = cuerpo.get("caduca")
    if caduca:
        try:
            caduca = datetime.fromisoformat(str(caduca).replace("Z", "+00:00"))
        except Exception:
            raise HTTPException(400, "caduca debe ser una fecha ISO (2026-12-31T00:00:00Z).")
    e = almacen.crear_enlace(slug, modo, cuerpo.get("nota"), caduca)
    return {"ok": True, "ruta": f"/r/{e['token']}/", **{k: str(v) for k, v in e.items()}}


@app.get("/admin/enlaces", dependencies=[Depends(exige_admin)])
def admin_enlaces(cliente: Optional[str] = None):
    return {"enlaces": [{k: str(v) for k, v in e.items()}
                        for e in almacen.enlaces(cliente)]}


@app.post("/admin/enlaces/{token}/revocar", dependencies=[Depends(exige_admin)])
def admin_revocar(token: str):
    if not almacen.revocar_enlace(token):
        raise HTTPException(404, "Ese enlace no existe.")
    return {"ok": True, "revocado": token}


# ══════════════════════════════════════════════════════════════════════════════
#  Comprobaciones de cuadre — un snapshot que no las pasa NO se publica.
#  Es la respuesta al hallazgo H-9 de la auditoría: la verificación deja de depender
#  de que alguien se acuerde de correrla a mano.
# ══════════════════════════════════════════════════════════════════════════════
def validar(d: Any) -> list[str]:
    p: list[str] = []
    if not isinstance(d, dict):
        return ["El snapshot no es un objeto JSON."]

    for clave in ("desde", "hasta", "leads", "cliente"):
        if clave not in d:
            p.append(f"Falta la clave obligatoria '{clave}'.")
    if p:
        return p

    leads = d.get("leads")
    if not isinstance(leads, list):
        return ["'leads' tiene que ser una lista."]

    if not isinstance(d.get("cliente"), dict) or not (d["cliente"].get("tz") or "").strip():
        p.append("El cliente no declara zona horaria ('cliente.tz'): sin ella las fechas "
                 "no son comparables con el CRM.")

    # ── fechas coherentes ────────────────────────────────────────────────
    try:
        datetime.fromisoformat(d["desde"])
        datetime.fromisoformat(d["hasta"])
        if d["hasta"] < d["desde"]:
            p.append(f"La ventana está invertida: desde {d['desde']} hasta {d['hasta']}.")
    except Exception:
        p.append("'desde' y 'hasta' tienen que ser fechas ISO (2026-05-03).")
        return p

    # ── los leads caen dentro de la ventana ──────────────────────────────
    # El criterio de extracción es la CREACIÓN DE LA OPORTUNIDAD ('fo'): eso sí tiene que
    # caer siempre dentro. El alta del contacto ('f') puede ser anterior — son los clientes
    # recurrentes — y en ese caso el lead tiene que venir marcado con rec=1. Si un lead
    # tiene 'f' fuera de la ventana SIN esa marca, el cohorte está mal armado.
    fuera_fo = [l.get("fo") for l in leads
                if l.get("fo") and not (d["desde"] <= l["fo"] <= d["hasta"])]
    if fuera_fo:
        p.append(f"{len(fuera_fo)} oportunidades se crearon fuera de la ventana "
                 f"(ej. {fuera_fo[0]}). La ventana debe cubrir exactamente lo extraído.")

    sin_marca = [l.get("n") for l in leads
                 if l.get("f") and not (d["desde"] <= l["f"] <= d["hasta"]) and not l.get("rec")]
    if sin_marca:
        p.append(f"{len(sin_marca)} leads tienen el alta del contacto antes de la ventana "
                 f"pero no están marcados como recurrentes (ej. {sin_marca[0]}). "
                 f"Con eso el CPL y el ROAS del periodo salen inflados.")

    # ── ids únicos: el bug de paginación que ya nos mordió una vez ────────
    ids = [l.get("id") for l in leads if l.get("id")]
    if len(ids) != len(set(ids)):
        p.append(f"Hay oportunidades duplicadas: {len(ids)} filas y {len(set(ids))} ids "
                 f"únicos. Suele ser el cursor de paginación saltándose registros.")

    # ── cada lead apunta a una etapa que existe ──────────────────────────
    etapas = {s.get("id") for s in (d.get("stages") or [])}
    if etapas:
        huerf = sum(1 for l in leads if l.get("ei") and l["ei"] not in etapas)
        if huerf and huerf > len(leads) * 0.5:
            p.append(f"{huerf} de {len(leads)} leads apuntan a una etapa que no está en "
                     f"'stages'. Parece que las etapas se extrajeron de otro pipeline.")

    # ── el gasto diario suma lo que dice sumar ───────────────────────────
    if d.get("granularidadGasto") == "dia":
        for c in (d.get("camps") or []):
            roto = next((w for w in (c.get("w") or [])
                         if w.get("s") and w.get("e") and w["s"] != w["e"]), None)
            if roto:
                p.append(f"La campaña '{c.get('n') or c.get('id')}' declara granularidad "
                         f"diaria pero tiene un bloque de {roto['s']} a {roto['e']}.")
                break

    # ── números que no deben ser NaN/Infinity al serializar ──────────────
    crudo = json.dumps(d, ensure_ascii=False, default=str)
    for token in ("NaN", "Infinity", "-Infinity"):
        if f":{token}" in crudo or f"[{token}" in crudo or f",{token}" in crudo:
            p.append(f"El snapshot contiene {token}, que no es JSON válido en todos los lectores.")

    return p
