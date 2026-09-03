"""
Servicio de reportes · Sentinel Marketing
══════════════════════════════════════
Sirve un único visor (cacheable, igual para todos los clientes) y el snapshot de datos
de cada cliente por separado, detrás de un enlace con token.

Rutas públicas
    GET  /r/{token}                  → redirige a /r/{token}/
    GET  /r/{token}/                 → el visor
    GET  /r/{token}/embed            → el visor para meter en un iframe, solo si el enlace
                                       declara dominios; si no, 403
    GET  /r/{token}/snapshot.json    → los datos de ese cliente, según el modo del enlace
    GET  /app.js                     → el dashboard (versionado por hash, cacheable)
    GET  /salud                      → healthcheck, sin contar nada del interior
    GET  /robots.txt                 → prohibido indexar

Panel
    GET  /admin                      → la página (en rutas_panel.py); pide el token dentro

Rutas de administración (cabecera X-Admin-Token)
    GET  /admin/estado
    POST /admin/visor                → atajo de emergencia; el despliegue vuelve al del repo
    POST /admin/clientes             → detecta zona horaria y moneda en el CRM
    GET  /admin/clientes/{slug}/borrado  → qué se destruiría (no borra nada)
    DEL  /admin/clientes/{slug}      → borra el cliente y TODO lo suyo; sin deshacer
    GET  /admin/ficha?location=…     → lo que el CRM sabe de una sub-cuenta
    GET  /admin/ficha/{slug}
    GET  /admin/cuentas              → sub-cuentas del CRM y cuentas de anuncios
    POST /admin/snapshots/{slug}    → atajo de emergencia; ya no hay puerta en el panel
    GET  /admin/snapshots/{slug}
    POST /admin/enlaces              → 409 si el cliente todavía no tiene datos
    GET  /admin/enlaces
    POST /admin/enlaces/{token}/dominios
    POST /admin/enlaces/{token}/revocar

Rutas de extracción (Fase 1, en rutas_extraccion.py)
    POST /admin/config/{slug}         → configuración de construcción del cliente
    GET  /admin/config/{slug}
    POST /admin/crudo/{slug}/{fuente} → un extractor deja su trozo (ghl|meta|google)
    GET  /admin/crudo/{slug}          → qué trozos hay y de cuándo
    POST /admin/construir/{slug}      → junta, valida y publica el snapshot

Enlace general (en enlace_general.py) — uno por cliente, el que se empotra en GoHighLevel
    GET  /admin/enlaces/general/{slug}       → cuál es, y si el cliente ya tiene reporte
    POST /admin/enlaces/general/{slug}       → lo crea si falta; idempotente
    POST /admin/enlaces/general/{slug}/rotar → revoca el de ahora y crea otro

Refresco a demanda (en rutas_refrescar.py)
    GET  /r/{token}/refrescar         → ¿se puede refrescar? ¿hay uno en marcha?
    POST /r/{token}/refrescar         → lo pide el botón del dashboard
    GET  /admin/refrescar/{slug}      → lo mismo, desde el panel
    POST /admin/refrescar/{slug}      → sin límite de antigüedad; lo usa "Preparar el reporte"
    GET  /admin/refrescos             → la cola entera, solo lectura
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import pathlib
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                              RedirectResponse, Response)
from starlette.exceptions import HTTPException as ErrorHTTP

from .almacen import abrir_almacen
from .rutas_panel import router as router_panel
from . import rutas_extraccion
from . import rutas_refrescar
from . import rutas_ficha
from . import enlace_general
from .privacidad import MODOS, aplicar
from . import seguridad as seg
from .visor import partir_html

log = logging.getLogger("reportes")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
VERSION = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "dev")[:7]

# Topes de subida. El snapshot real de un cliente ronda 0,8 MB y el dashboard 1 MB;
# el margen es amplio a propósito, pero acotado: un cuerpo sin límite tumba el contenedor.
TOPE_VISOR_MB = float(os.environ.get("TOPE_VISOR_MB", 8))
TOPE_SNAPSHOT_MB = float(os.environ.get("TOPE_SNAPSHOT_MB", 60))

# Los tokens son de 192 bits: no se adivinan. Esto no está para eso, sino para que un bot
# no pueda machacar el servicio ni llenar los logs, y para que un intento quede registrado.
LIM_ADMIN = seg.Limitador(intentos=10, ventana=300, castigo=600, etiqueta="admin")
LIM_ENLACE = seg.Limitador(intentos=40, ventana=300, castigo=600, etiqueta="enlace")

app = FastAPI(title="Reportes Sentinel", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.include_router(router_panel)

almacen = abrir_almacen()

# ── el dashboard se versiona en el repositorio ─────────────────────
# `web/visor/dashboard.html` es la FUENTE DE VERDAD del dashboard: el código, sin datos.
# Cada despliegue lo parte y lo guarda, así que mejorar el dashboard es un commit y nada
# más. POST /admin/visor sigue existiendo como atajo de emergencia (cambiar el dashboard
# sin desplegar), pero el siguiente despliegue vuelve a poner el del repositorio.
#
# El visor servido se cachea en memoria. El hash de app.js versiona su URL, así que el
# navegador puede cachearlo para siempre y aun así recibir el nuevo cuando cambia.
# El dashboard va troceado en `web/visor/partes/*.part` (ver scripts/partir_en_partes.py).
# Se concatenan por orden de nombre y el resultado es el dashboard completo, byte a byte.
# Está partido para que mejorar una parte toque un archivo pequeño en vez de mover 220 KB.
RUTA_PARTES = pathlib.Path(__file__).resolve().parent.parent / "visor" / "partes"
RUTA_VISOR_UNICO = pathlib.Path(__file__).resolve().parent.parent / "visor" / "dashboard.html"


def _dashboard_del_repo() -> Optional[str]:
    partes = sorted(RUTA_PARTES.glob("*.part")) if RUTA_PARTES.exists() else []
    if partes:
        return "".join(p.read_text(encoding="utf-8") for p in partes)
    if RUTA_VISOR_UNICO.exists():   # respaldo: el dashboard en un solo archivo
        return RUTA_VISOR_UNICO.read_text(encoding="utf-8")
    return None

_cache: dict = {"hash": None, "index": None, "app": b"", "subido": None}


def _visor_del_repo() -> Optional[dict]:
    html = _dashboard_del_repo()
    if not html:
        return None
    try:
        return partir_html(html)
    except ValueError as ex:
        # Un dashboard mal formado en el repositorio no debe tumbar el servicio: se
        # registra y se sigue sirviendo el que hubiera guardado.
        log.error("el dashboard del repositorio no se puede partir: %s", ex)
        return None


def _sembrar_visor() -> None:
    """En cada despliegue, el dashboard del repositorio manda."""
    r = _visor_del_repo()
    if not r:
        log.warning("no hay dashboard en %s: el visor solo puede llegar por /admin/visor",
                    RUTA_PARTES)
        return
    actual = almacen.visor()
    if actual and actual.get("hash") == r["hash"]:
        log.info("visor del repositorio ya guardado · hash %s", r["hash"])
        return
    almacen.guardar_visor(r["index"], r["app"], r["hash"])
    log.info("visor sembrado desde el repositorio · hash %s%s", r["hash"],
             f" (sustituye a {actual['hash']})" if actual else "")


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


_sembrar_visor()
_cargar_visor()
log.info("servicio arriba · almacén %s · visor %s",
         almacen.tipo, _cache["hash"] or "SIN CARGAR")


# ════════════════════════════════════════════════════════════════════════════
#  Utilidades
# ════════════════════════════════════════════════════════════════════════════
def exige_admin(peticion: Request, x_admin_token: str = Header(default="")) -> None:
    """
    El token CORRECTO pasa siempre, incluso si desde esa IP hubo muchos fallos antes.

    Es deliberado. El token es de 256 bits y la comparación es de tiempo constante, así
    que la fuerza bruta no es la amenaza; el límite está para que un bot no machaque el
    servicio. Bloquear también al token bueno tendría dos efectos malos y ninguno bueno:
    equivocarse tecleando dejaría al operador fuera de su propio panel, y una IP
    compartida (una oficina detrás de un NAT) permitiría que un tercero se lo tirara.
    """
    if not ADMIN_TOKEN:
        raise HTTPException(503, "El servicio no tiene ADMIN_TOKEN configurado.")
    ip = seg.ip_cliente(peticion)
    if hmac.compare_digest(x_admin_token or "", ADMIN_TOKEN):
        LIM_ADMIN.acierto(ip)
        return
    bloqueado = LIM_ADMIN.fallo(ip, f"ruta {peticion.url.path}")
    if bloqueado:
        raise HTTPException(429, "Demasiados intentos fallidos desde esta conexión.",
                            headers={"Retry-After": str(bloqueado)})
    raise HTTPException(401, "Token de administración inválido.")


def _resolver_enlace(token: str, peticion: Request) -> dict:
    """
    Igual que en el panel: un enlace VÁLIDO se sirve siempre.

    Si el bloqueo alcanzara también a los enlaces buenos, cualquiera que trastee desde
    la misma IP que el cliente le tumbaría el reporte. Lo que se frena es el sondeo de
    tokens que no existen.
    """
    ip = seg.ip_cliente(peticion)
    e = almacen.enlace(token)
    if not e:
        bloqueado = LIM_ENLACE.fallo(ip, "token inexistente")
        if bloqueado:
            raise HTTPException(429, "Demasiadas peticiones a enlaces que no existen.",
                                headers={"Retry-After": str(bloqueado)})
        raise HTTPException(404, "Este reporte no existe.")
    if e.get("revocado"):
        LIM_ENLACE.fallo(ip, f"enlace revocado de {e.get('cliente')}")
        raise HTTPException(403, "Este enlace fue revocado.")
    cad = e.get("caduca")
    if cad:
        if isinstance(cad, str):
            cad = datetime.fromisoformat(cad)
        if cad.tzinfo is None:
            cad = cad.replace(tzinfo=timezone.utc)
        if cad < datetime.now(timezone.utc):
            LIM_ENLACE.fallo(ip, f"enlace caducado de {e.get('cliente')}")
            raise HTTPException(403, "Este enlace ha caducado.")
    LIM_ENLACE.acierto(ip)
    LIM_ENLACE.limpia()
    LIM_ADMIN.limpia()
    return e


def _dominios(e: dict) -> list[str]:
    """Orígenes donde ESTE enlace se puede empotrar. Vacío = en ninguno."""
    d = e.get("dominios") or []
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except Exception:
            d = []
    return [x for x in d if seg.origen_valido(x)]


CABECERAS_PRIVADAS = seg.cabeceras()


def _pagina(titulo: str, texto: str, *, codigo: int = 200,
            doms: list[str] | None = None) -> HTMLResponse:
    """
    Una página sobria para lo que ve el destinatario de un enlace.

    Antes estos casos devolvían el JSON crudo del error ({"detail": "..."}), y dentro de
    un iframe en la web del cliente eso es exactamente lo que se leía. Se sirve con los
    dominios del propio enlace cuando se conocen, para que el aviso se vea DENTRO del
    iframe en vez de dejar un hueco en blanco.
    """
    return HTMLResponse(
        "<!doctype html><html lang=es><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{titulo}</title>"
        "<style>body{font:15px/1.6 ui-sans-serif,system-ui,sans-serif;background:#0b0f14;"
        "color:#93a1b5;display:grid;place-items:center;min-height:100vh;margin:0;"
        "text-align:center;padding:28px}div{max-width:430px}"
        "b{color:#e8edf4;font-size:16px}p{margin:0 0 8px}</style>"
        f"<div><p><b>{titulo}</b></p><p>{texto}</p></div>",
        status_code=codigo,
        headers=seg.cabeceras(csp_reporte=True, empotrable_en=doms or None))


_MOTIVOS = {
    404: ("Este reporte no existe",
          "El enlace es incorrecto o se dio de baja. Pide uno nuevo a tu contacto en Sentinel."),
    403: ("Este enlace ya no es válido",
          "Se revocó o caducó. Pide uno nuevo a tu contacto en Sentinel."),
    409: ("Estamos preparando este reporte",
          "El enlace es correcto: lo que aún no ha terminado es la primera extracción de "
          "datos. Vuelve a abrirlo en un rato."),
    429: ("Demasiadas peticiones",
          "Espera unos minutos y vuelve a intentarlo."),
    503: ("El reporte todavía no está disponible",
          "Falta cargar el visor en el servicio. Avísale a tu contacto en Sentinel."),
}


@app.exception_handler(ErrorHTTP)
async def errores(peticion: Request, exc: ErrorHTTP):
    """
    Para las rutas del reporte, un error se ve como una página; para el resto, como JSON.

    El snapshot se queda en JSON a propósito: lo consume el cargador del visor, no una
    persona, y así el aviso lo pinta el propio dashboard con su estilo.
    """
    ruta = peticion.url.path
    es_reporte = ruta.startswith("/r/") and not ruta.endswith("snapshot.json")
    if es_reporte and exc.status_code in _MOTIVOS:
        titulo, texto = _MOTIVOS[exc.status_code]
        # Se recuperan los dominios del enlace (aunque esté revocado) para que el aviso
        # se pueda ver dentro del iframe donde estaba puesto.
        doms = []
        try:
            partes = ruta.split("/")
            if len(partes) > 2:
                e = almacen.enlace(partes[2])
                if e:
                    doms = _dominios(e)
        except Exception:
            pass
        return _pagina(titulo, texto, codigo=exc.status_code, doms=doms)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                        headers=getattr(exc, "headers", None) or CABECERAS_PRIVADAS)


# ════════════════════════════════════════════════════════════════════════════
#  Público
# ════════════════════════════════════════════════════════════════════════════
@app.get("/salud")
def salud():
    # Público a propósito (lo usa el healthcheck de Railway), así que no cuenta nada más:
    # el tipo de almacén, la versión del visor y su fecha viven en /admin/estado.
    return {"ok": True}


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return "User-agent: *\nDisallow: /\n"


@app.get("/", response_class=HTMLResponse)
def raiz():
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Reportes</title>"
        "<style>body{font:15px/1.6 system-ui;background:#0b0f14;color:#93a1b5;"
        "display:grid;place-items:center;height:100vh;margin:0}</style>"
        # Sin enlace al panel: quien tenga que entrar ya sabe la ruta, y no hace falta
        # anunciarle la superficie de administración a quien llegue de rebote.
        "<p>Servicio de reportes. Se accede con un enlace directo.</p>",
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


@app.get("/r/{token}/embed", response_class=HTMLResponse)
def embed(token: str, peticion: Request):
    """
    Misma página del reporte, pensada para ir dentro de un iframe.

    Existe como ruta aparte para dejar claro en los accesos qué visitas vienen empotradas,
    y porque así el enlace que se pega en GoHighLevel no es el mismo texto que el que se
    manda por WhatsApp. La regla de quién puede empotrar sigue siendo la del enlace:
    si no tiene dominios declarados, no se empotra en ningún sitio.
    """
    e = _resolver_enlace(token, peticion)
    doms = _dominios(e)
    if not doms:
        return _pagina("Este enlace no está autorizado para empotrarse",
                       "Hay que declarar en qué dominios se puede mostrar antes de usarlo "
                       "en un iframe. Se hace en el panel, en la columna «Empotrar».",
                       codigo=403)
    vis = _cargar_visor()
    if not vis:
        return _pagina(*_MOTIVOS[503], codigo=503, doms=doms)
    almacen.marcar_acceso(token)
    # El visor pide 'snapshot.json' relativo a su URL: desde /r/{t}/embed eso resolvería
    # a /r/{t}/snapshot.json igual, porque 'embed' no lleva barra final. Se deja explícito
    # para que un cambio futuro en la ruta no rompa la carga de datos en silencio.
    html = vis["index"].replace("'./snapshot.json'", f"'/r/{token}/snapshot.json'")
    return HTMLResponse(html, headers=seg.cabeceras(
        {"Cache-Control": "private, max-age=60"},
        csp_reporte=True, empotrable_en=doms))


@app.get("/r/{token}/", response_class=HTMLResponse)
def visor(token: str, peticion: Request):
    e = _resolver_enlace(token, peticion)
    doms = _dominios(e)
    vis = _cargar_visor()
    if not vis:
        return _pagina(*_MOTIVOS[503], codigo=503, doms=doms)
    almacen.marcar_acceso(token)
    return HTMLResponse(vis["index"], headers=seg.cabeceras(
        {"Cache-Control": "private, max-age=60"},
        csp_reporte=True, empotrable_en=doms))


@app.get("/r/{token}/snapshot.json")
def snapshot(token: str, peticion: Request):
    e = _resolver_enlace(token, peticion)
    datos = almacen.snapshot(e["cliente"])
    if datos is None:
        # 409 y no 404 A PROPÓSITO. El enlace es VÁLIDO; lo que falta son los datos. Con
        # 404 el cargador del visor pintaba "Este reporte no existe · el enlace es
        # incorrecto o se dio de baja", que es mentira y manda al cliente a pedir un
        # enlace nuevo que tampoco funcionaría. Ver el manejo del 409 en visor.py.
        raise HTTPException(409, "El reporte de este cliente todavía se está preparando: "
                                 "aún no hay una extracción de datos terminada.")
    # El enmascarado del modo demo se hace AQUÍ, en el servidor: un enlace demo nunca
    # transporta el nombre real de un paciente, así que da igual quién abra el inspector.
    return JSONResponse(aplicar(datos, e.get("modo") or "cliente"),
                        headers=seg.cabeceras({"Cache-Control": "private, no-store"}))


# ════════════════════════════════════════════════════════════════════════════
#  Administración
# ════════════════════════════════════════════════════════════════════════════
@app.get("/admin/estado", dependencies=[Depends(exige_admin)])
def admin_estado():
    v = _cargar_visor()
    repo = _visor_del_repo()
    return {"version": VERSION, "almacen": almacen.tipo,
            "visor": {"hash": (v or {}).get("hash"), "subido": (v or {}).get("subido"),
                      "bytes": len((v or {}).get("app") or b""),
                      # Si lo que se sirve no es el del repositorio, es una subida a mano
                      # que el siguiente despliegue va a sustituir. Conviene verlo.
                      "origen": ("repositorio" if repo and v and repo["hash"] == v["hash"]
                                 else "subida temporal" if v else None)},
            "clientes": almacen.clientes()}


@app.post("/admin/visor", dependencies=[Depends(exige_admin)])
async def admin_subir_visor(peticion: Request):
    """Atajo para cambiar el dashboard sin desplegar.

    El camino normal es un commit en `web/visor/partes/*.part`: Railway despliega y el
    servicio lo siembra solo. Esto sirve para probar algo en caliente o para salir de un
    apuro — y el siguiente despliegue vuelve a poner el del repositorio. Se dejó sin
    botón en el panel a propósito: la puerta invitaba a usarlo como camino normal.
    """
    try:
        crudo = await seg.leer_cuerpo(peticion, TOPE_VISOR_MB, "dashboard")
    except ValueError as ex:
        raise HTTPException(413, str(ex))
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
    repo = _visor_del_repo()
    return {"ok": True, "hash": partes["hash"],
            "index_kb": round(len(partes["index"]) / 1024, 1),
            "app_kb": round(len(partes["app"]) / 1024, 1),
            "leads_en_el_archivo_subido": len(partes["datos"].get("leads") or []),
            "temporal": bool(repo and repo["hash"] != partes["hash"])}


@app.post("/admin/clientes", dependencies=[Depends(exige_admin)])
def admin_cliente(cuerpo: dict = Body(...)):
    """
    Da de alta un cliente y le deja la ficha lista para extraer.

    Dos cosas que antes había que hacer a mano y ahora no:

    1. **La zona horaria se lee del CRM**, no se teclea (ver el comentario largo de
       rutas_ficha.py). Solo si la sub-cuenta no la declara se pide a mano.
    2. **Se siembra la configuración de construcción** con lo que ya se sabe. Antes el
       alta y la configuración eran dos formularios que pedían los mismos datos, y era
       fácil acabar con una zona horaria en la ficha y otra distinta en la config.
    """
    slug = (cuerpo.get("slug") or "").strip().lower()
    if not slug or not slug.replace("-", "").isalnum():
        raise HTTPException(400, "slug obligatorio, solo letras, números y guiones.")
    nombre = (cuerpo.get("nombre") or "").strip()
    if not nombre:
        raise HTTPException(400, "nombre obligatorio.")
    loc = (cuerpo.get("ghl_location_id") or "").strip() or None

    tz = (cuerpo.get("tz") or "").strip() or None
    moneda = (cuerpo.get("moneda") or "").strip().upper() or None
    detectado, aviso = None, None
    if loc and not tz:
        try:
            detectado = rutas_ficha.ficha_crm(loc)
            tz = detectado.get("tz")
            moneda = moneda or detectado.get("moneda")
        except Exception as ex:               # noqa: BLE001
            # Que el CRM no conteste NO impide dar de alta al cliente: se da de alta sin
            # zona y el panel la pide a mano. Fallar aquí dejaría al operador atascado
            # por algo que puede resolver él en diez segundos. Se atrapa TODO a propósito,
            # HTTPException incluida: ninguna forma de fallar la detección justifica no
            # poder crear un cliente.
            detalle = getattr(ex, "detail", None) or str(ex)
            aviso = (f"No se pudo leer la zona horaria del CRM ({detalle}). "
                     "Elígela a mano en la ficha del cliente.")
            log.warning("alta de %s sin zona detectada: %s", slug, detalle)

    c = almacen.guardar_cliente(slug, nombre, loc, tz, cuerpo.get("fuentes"))

    if tz:
        cfg = dict((almacen.cliente(slug) or {}).get("config") or {})
        cfg.update({"nombre": nombre, "slug": slug, "tz": tz, "ghlLocationId": loc})
        cfg.setdefault("cuentas", [])
        if moneda:
            cfg["moneda"] = moneda
        # De dónde salió la zona queda ESCRITO y viaja al snapshot (construir.py lo copia
        # a cliente.tzFuente). Cuando dentro de seis meses un número no cuadre, la primera
        # pregunta va a ser "¿y esta zona quién la puso?".
        cfg["tzFuente"] = ("Sub-cuenta de GoHighLevel " + str(loc) + " (timezone)"
                           if detectado and detectado.get("tz")
                           else "Puesta a mano en el panel")
        almacen.guardar_config(slug, cfg)

    out = {k: (str(v) if not isinstance(v, (dict, list, type(None))) else v)
           for k, v in (c or {}).items()}
    out.update({"tz": tz, "moneda": moneda,
                "tzDetectada": bool(detectado and detectado.get("tz")),
                "ciudad": (detectado or {}).get("ciudad"),
                "pais": (detectado or {}).get("pais")})
    if aviso:
        out["aviso"] = aviso
    return out


# ════════════════════════════════════════════════════════════════════════════
#  Borrar un cliente
#
#  Se hace en DOS pasos y no en uno: primero se pregunta qué se destruiría y solo
#  después se borra, repitiendo el slug. No es burocracia — no hay deshacer, y lo que
#  decide si borrar es seguro no es acordarse del nombre del cliente, es ver cuántos
#  ENLACES VIVOS se van con él. Uno de esos enlaces puede estar empotrado ahora mismo
#  en la web del cliente, y al borrarlo deja de funcionar sin que nadie se entere hasta
#  que alguien lo abre.
# ════════════════════════════════════════════════════════════════════════════
def _extraccion_en_curso(slug: str) -> bool:
    """
    ¿Hay una extracción pendiente o corriendo para este cliente?

    Importa porque el obrero de refresco escribe el trozo crudo AL TERMINAR. Si el
    cliente desaparece a mitad, ese POST se encuentra un cliente que no existe, el
    extractor sale con error y en el log queda un fallo que no es un fallo. Es más
    limpio negarse y decir que se espere.
    """
    try:
        return bool(rutas_refrescar._pendiente(slug))
    except Exception:                            # noqa: BLE001
        return False


@app.get("/admin/clientes/{slug}/borrado", dependencies=[Depends(exige_admin)])
def admin_borrado_previo(slug: str):
    """Qué se llevaría por delante el borrado. NO borra nada."""
    res = almacen.resumen_borrado(slug)
    if res is None:
        raise HTTPException(404, f"El cliente '{slug}' no existe.")
    c = almacen.cliente(slug) or {}
    return {"cliente": slug, "nombre": c.get("nombre"),
            "enCurso": _extraccion_en_curso(slug), **res}


@app.delete("/admin/clientes/{slug}", dependencies=[Depends(exige_admin)])
def admin_borrar_cliente(slug: str, confirmar: str = Query(default="")):
    """
    Borra el cliente y todo lo suyo: snapshots, trozos crudos, enlaces y configuración.

    `confirmar` tiene que venir con el slug EXACTO. Es lo único que separa un clic
    accidental de perder el historial de un cliente, así que se comprueba en el servidor
    y no solo en el panel.
    """
    res = almacen.resumen_borrado(slug)
    if res is None:
        raise HTTPException(404, f"El cliente '{slug}' no existe.")
    if confirmar != slug:
        raise HTTPException(400,
            f"Para borrar hay que repetir el identificador exacto del cliente: "
            f"manda ?confirmar={slug}. No hay deshacer.")
    if _extraccion_en_curso(slug):
        raise HTTPException(409,
            "Este cliente tiene una extracción en marcha. Espera a que termine (o a que "
            "caduque, unos 45 minutos) y vuelve a intentarlo: si se borra a mitad, el "
            "extractor falla al entregar sus datos y ensucia el log con un error falso.")

    borrado = almacen.borrar_cliente(slug)
    # Se registra con detalle A PROPÓSITO: es la única huella que queda de que estos
    # datos existieron, y la primera pregunta cuando alguien no encuentre un reporte va
    # a ser "¿esto se borró, y cuándo?".
    log.warning("CLIENTE BORRADO · %s · %s snapshots · %s enlaces (%s activos) · "
                "%s trozos crudos · %s visitas acumuladas",
                slug, borrado["snapshots"], borrado["enlaces"],
                borrado["enlacesActivos"], borrado["crudos"], borrado["accesos"])
    return {"ok": True, "borrado": slug, **borrado}


# El panel ya no ofrece subir el snapshot a mano: los datos los traen los extractores y el
# snapshot lo construye el servicio con la configuración del cliente. Una subida manual
# competía con eso (el cron de la madrugada la sobrescribía) y hacía creer que publicar un
# reporte era un paso manual. La ruta se queda como atajo de emergencia —restaurar a mano un
# snapshot bueno mientras se arregla un extractor— igual que POST /admin/visor.
@app.post("/admin/snapshots/{slug}", dependencies=[Depends(exige_admin)])
async def admin_publicar(slug: str, peticion: Request):
    if not almacen.cliente(slug):
        raise HTTPException(404, f"El cliente '{slug}' no existe. Créalo primero.")
    try:
        cuerpo = await seg.leer_cuerpo(peticion, TOPE_SNAPSHOT_MB, "snapshot")
    except ValueError as ex:
        raise HTTPException(413, str(ex))
    try:
        datos = json.loads(cuerpo)
    except Exception as ex:
        raise HTTPException(400, f"El cuerpo no es JSON válido: {ex}")

    problemas = validar(datos)
    if problemas:
        raise HTTPException(422, {"error": "El snapshot no pasa las comprobaciones",
                                  "problemas": problemas})
    reg = almacen.publicar_snapshot(slug, datos)
    almacen.purgar_snapshots(slug, conservar=30)
    enlace_general.tras_publicar(almacen, slug)
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

    crudos = cuerpo.get("dominios")
    doms = seg.normaliza_dominios(crudos)
    if crudos and not doms:
        raise HTTPException(400, "Ningún dominio válido. Se esperan orígenes https, "
                                 "con comodín opcional: https://app.gohighlevel.com, "
                                 "https://*.msgsndr.com")

    # UN ENLACE SIN DATOS ES UN ENLACE MUERTO. Se creaba igual, y quien lo abría leía
    # "Este reporte no existe": el enlace estaba bien, no había snapshot. El panel ya
    # avisa, pero la garantía tiene que estar aquí, porque el panel se puede saltar.
    # `forzar` existe para el caso raro de tener que crear el enlace antes que los datos
    # (por ejemplo, dejar el iframe puesto en la web del cliente); no lo usa el panel.
    if almacen.snapshot(slug) is None and not cuerpo.get("forzar"):
        raise HTTPException(409,
            f"El cliente '{slug}' todavía no tiene datos publicados, así que este enlace "
            "no mostraría nada. Pulsa «Preparar el reporte» en su ficha para extraer de "
            "GoHighLevel, Meta y Google, y crea el enlace cuando el reporte ya exista.")

    e = almacen.crear_enlace(slug, modo, cuerpo.get("nota"), caduca, doms)
    log.info("enlace creado · %s · modo %s · empotrable en %s",
             slug, modo, doms or "ningún sitio")
    return {"ok": True, "ruta": f"/r/{e['token']}/",
            "rutaEmbed": f"/r/{e['token']}/embed" if doms else None,
            "dominios": doms,
            **{k: str(v) for k, v in e.items() if k != "dominios"}}


@app.post("/admin/enlaces/{token}/dominios", dependencies=[Depends(exige_admin)])
def admin_dominios(token: str, cuerpo: dict = Body(...)):
    """
    Cambia dónde se puede empotrar un enlace que ya existe, sin cambiar su URL.

    Hace falta para GoHighLevel: si el cliente estrena dominio propio, se añade aquí y el
    iframe que ya está pegado sigue funcionando. Lista vacía = deja de ser empotrable.
    """
    e = almacen.enlace(token)
    if not e:
        raise HTTPException(404, "Ese enlace no existe.")
    if e.get("revocado"):
        raise HTTPException(400, "Ese enlace está revocado.")
    crudos = cuerpo.get("dominios")
    doms = seg.normaliza_dominios(crudos)
    if crudos and not doms:
        raise HTTPException(400, "Ningún dominio válido.")
    almacen.dominios_enlace(token, doms)
    log.info("dominios del enlace de %s → %s", e.get("cliente"), doms or "ninguno")
    return {"ok": True, "dominios": doms}


@app.get("/admin/enlaces", dependencies=[Depends(exige_admin)])
def admin_enlaces(cliente: Optional[str] = None):
    def fila(e):
        f = {k: str(v) for k, v in e.items() if k != "dominios"}
        f["dominios"] = _dominios(e)
        return f
    return {"enlaces": [fila(e) for e in almacen.enlaces(cliente)]}


@app.post("/admin/enlaces/{token}/revocar", dependencies=[Depends(exige_admin)])
def admin_revocar(token: str):
    e = almacen.enlace(token)
    if not e:
        raise HTTPException(404, "Ese enlace no existe.")
    # EL ENLACE GENERAL NO SE REVOCA POR AQUÍ. Es el que está empotrado en la web del
    # cliente, y revocarlo la deja en blanco sin que nada avise desde el panel. Para el
    # día que haya que cortarlo de verdad está «rotar», que pide el identificador escrito
    # y dice qué rompe.
    if e.get("general"):
        raise HTTPException(409,
            f"Ese es el enlace general de '{e.get('cliente')}': el que está empotrado en "
            "GoHighLevel y el que se comparte. No se revoca desde aquí porque dejaría su "
            "web en blanco. Si de verdad hay que invalidarlo, usa «rotar el enlace "
            "general» en su ficha, que crea otro en el mismo paso.")
    if not almacen.revocar_enlace(token):
        raise HTTPException(404, "Ese enlace no existe.")
    log.info("enlace revocado · cliente %s · modo %s",
             (e or {}).get("cliente"), (e or {}).get("modo"))
    return {"ok": True, "revocado": token}


# ════════════════════════════════════════════════════════════════════════════
#  Comprobaciones de cuadre — un snapshot que no las pasa NO se publica.
#  Es la respuesta al hallazgo H-9 de la auditoría: la verificación deja de depender
#  de que alguien se acuerde de correrla a mano.
# ════════════════════════════════════════════════════════════════════════════
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

    # ── fechas coherentes ───────────────────────────────
    try:
        datetime.fromisoformat(d["desde"])
        datetime.fromisoformat(d["hasta"])
        if d["hasta"] < d["desde"]:
            p.append(f"La ventana está invertida: desde {d['desde']} hasta {d['hasta']}.")
    except Exception:
        p.append("'desde' y 'hasta' tienen que ser fechas ISO (2026-05-03).")
        return p

    # ── los leads caen dentro de la ventana ────────────────────────
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

    # ── ids únicos: el bug de paginación que ya nos mordió una vez ────
    ids = [l.get("id") for l in leads if l.get("id")]
    if len(ids) != len(set(ids)):
        p.append(f"Hay oportunidades duplicadas: {len(ids)} filas y {len(set(ids))} ids "
                 f"únicos. Suele ser el cursor de paginación saltándose registros.")

    # ── cada lead apunta a una etapa que existe ──────────────────
    etapas = {s.get("id") for s in (d.get("stages") or [])}
    if etapas:
        huerf = sum(1 for l in leads if l.get("ei") and l["ei"] not in etapas)
        if huerf and huerf > len(leads) * 0.5:
            p.append(f"{huerf} de {len(leads)} leads apuntan a una etapa que no está en "
                     f"'stages'. Parece que las etapas se extrajeron de otro pipeline.")

    # ── el gasto diario suma lo que dice sumar ─────────────────
    if d.get("granularidadGasto") == "dia":
        for c in (d.get("camps") or []):
            roto = next((w for w in (c.get("w") or [])
                         if w.get("s") and w.get("e") and w["s"] != w["e"]), None)
            if roto:
                p.append(f"La campaña '{c.get('n') or c.get('id')}' declara granularidad "
                         f"diaria pero tiene un bloque de {roto['s']} a {roto['e']}.")
                break

    # ── números que no deben ser NaN/Infinity al serializar ──────────────────
    crudo = json.dumps(d, ensure_ascii=False, default=str)
    for token in ("NaN", "Infinity", "-Infinity"):
        if f":{token}" in crudo or f"[{token}" in crudo or f",{token}" in crudo:
            p.append(f"El snapshot contiene {token}, que no es JSON válido en todos los lectores.")

    return p


# ════════════════════════════════════════════════════════════════════════════
#  Rutas de la Fase 1 (extracción) — se montan al final porque necesitan
#  exige_admin y validar, que se definen arriba.
# ════════════════════════════════════════════════════════════════════════════
rutas_extraccion.montar(app, almacen=almacen, exige_admin=exige_admin,
                        validar=validar, leer_cuerpo=seg.leer_cuerpo,
                        tope_mb=TOPE_SNAPSHOT_MB)

# El botón de "actualizar ahora" del dashboard. Necesita _resolver_enlace, que es lo que
# convierte el token de un enlace en su cliente: el enlace ES la autorización para
# refrescar, porque ya da acceso a todos los datos de ese cliente.
rutas_refrescar.montar(app, almacen=almacen, exige_admin=exige_admin,
                       resolver_enlace=_resolver_enlace)

# La zona horaria y la moneda las lee del CRM, para no pedírselas a nadie.
rutas_ficha.montar(app, almacen=almacen, exige_admin=exige_admin,
                   config_de=lambda s: (almacen.cliente(s) or {}).get("config") or {})
enlace_general.montar(app, almacen=almacen, exige_admin=exige_admin)
