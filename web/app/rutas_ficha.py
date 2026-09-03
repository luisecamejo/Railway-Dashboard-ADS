# -*- coding: utf-8 -*-
"""
Lo que las plataformas ya saben, para no teclearlo en el panel.

    panel ──GET /admin/ficha?location=XXX──> reportes ──ghl_get_client──> GoHighLevel
    panel ──GET /admin/cuentas─────────────> reportes ──> GoHighLevel + Meta + Google

Dos cosas distintas con el mismo motivo de fondo:

  · `ficha` — la zona horaria y la moneda de UNA sub-cuenta ya dada de alta.
  · `cuentas` — la LISTA de sub-cuentas del CRM y de cuentas de anuncios a las que
    llegan nuestras credenciales, con su nombre, para elegirlas en un desplegable en
    vez de pegar un identificador a mano.

Un identificador tecleado a mano no falla: extrae de OTRA cuenta. Un dígito cambiado en
una cuenta de Meta da un reporte con el gasto de otro negocio, y nadie lo nota porque el
número tiene toda la pinta de ser bueno. Con la lista delante se elige por NOMBRE, que
es lo que la persona sabe, y el identificador lo pone el sistema.

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
import time
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query

log = logging.getLogger("reportes.ficha")

router = APIRouter()

TIMEOUT = int(os.environ.get("GHL_MCP_TIMEOUT_FICHA", "30"))

# Las listas de cuentas cambian de mes en mes, no de minuto en minuto, y la ficha de
# un cliente se abre muchas veces seguidas. Se cachean en memoria; `?refrescar=1`
# salta la caché para cuando se acaba de crear una cuenta en Meta o en el CRM.
CACHE_S = int(os.environ.get("CUENTAS_CACHE_S", "600"))
_cache: dict[str, tuple[float, dict]] = {}


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


# ═════════════════════════════════════════════════════════════════════════════
#  Las listas de cuentas disponibles
# ═════════════════════════════════════════════════════════════════════════════
def _extractores_en_path() -> None:
    raiz = pathlib.Path(__file__).resolve().parents[2] / "extractores"
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))


def cuentas_ghl() -> list[dict]:
    """Las sub-cuentas de la agencia. Una sola llamada al MCP, ya paginada por él."""
    r = _cliente_mcp().llamar("ghl_list_clients", {})
    filas = r if isinstance(r, list) else (r or {}).get("clients") or []
    out = []
    for c in filas:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "").strip()
        if cid:
            out.append({"id": cid, "nombre": (c.get("name") or "").strip() or cid})
    out.sort(key=lambda x: x["nombre"].lower())
    return out


def cuentas_meta() -> list[dict]:
    """
    Las cuentas de anuncios que ve NUESTRO token, que es lo que importa.

    No las que ve la persona en su Business Manager: si el token del usuario del sistema
    no tiene asignada una cuenta, elegirla en el panel daría una extracción vacía sin
    decir por qué. Preguntando por /me/adaccounts la lista ES exactamente el conjunto de
    cuentas de las que se puede extraer.
    """
    _extractores_en_path()
    from comun.http import json_get                    # noqa: E402

    token = os.environ.get("META_TOKEN", "").strip()
    if not token:
        raise HTTPException(503, "Este servicio no tiene META_TOKEN, así que no puede "
                                 "listar las cuentas de Meta.")
    version = os.environ.get("META_API_VERSION", "v26.0")
    url = f"https://graph.facebook.com/{version}/me/adaccounts"
    params = {"fields": "account_id,name,account_status,currency,timezone_name",
              "limit": "200", "access_token": token}
    out, n = [], 0
    while True:
        n += 1
        if n > 20:          # 4.000 cuentas; si se llega aquí es que algo pagina mal
            break
        r = json_get(url, params) if n == 1 else json_get(url)
        for c in (r.get("data") or []):
            cid = str(c.get("account_id") or "").strip()
            if not cid:
                continue
            out.append({"id": cid,
                        "nombre": (c.get("name") or "").strip() or cid,
                        "moneda": c.get("currency") or None,
                        "tz": c.get("timezone_name") or None,
                        # 1 = activa. Se marca en vez de esconderla: una cuenta pausada
                        # sigue teniendo gasto histórico que el reporte debe contar.
                        "activa": c.get("account_status") == 1})
        url = ((r.get("paging") or {}).get("next")) or ""
        if not url:
            break
        params = {}
    out.sort(key=lambda x: x["nombre"].lower())
    return out


def _modulo_google():
    """
    Carga `extractores/google/extraer.py` POR RUTA, no por su nombre de paquete.

    `import google.extraer` sería una trampa: `google` es también el paquete de las
    librerías de Google (protobuf, google-auth). Si algo importa una de ellas primero,
    `sys.modules["google"]` ya está ocupado y nuestro `google/` deja de encontrarse. Aquí
    no hay nada instalado que lo haga, pero la próxima dependencia que se añada podría, y
    el fallo saldría meses después en una ruta que hoy funciona. Cargarlo por ruta con un
    nombre propio lo deja fuera de esa discusión.
    """
    import importlib.util

    _extractores_en_path()        # el módulo hace `from comun.x import y` al cargarse
    if "extractores_google_extraer" in sys.modules:
        return sys.modules["extractores_google_extraer"]
    ruta = (pathlib.Path(__file__).resolve().parents[2]
            / "extractores" / "google" / "extraer.py")
    spec = importlib.util.spec_from_file_location("extractores_google_extraer", ruta)
    if not spec or not spec.loader:
        raise HTTPException(503, f"No encuentro el extractor de Google en {ruta}.")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["extractores_google_extraer"] = mod
    spec.loader.exec_module(mod)
    return mod


GAQL_CLIENTES = (
    "SELECT customer_client.id, customer_client.descriptive_name, "
    "customer_client.currency_code, customer_client.time_zone, "
    "customer_client.manager, customer_client.status "
    "FROM customer_client WHERE customer_client.status = 'ENABLED'")


def _guion(cid: str) -> str:
    """123-456-7890, que es como se escriben en el panel y en la config."""
    d = "".join(ch for ch in str(cid) if ch.isdigit())
    return f"{d[:3]}-{d[3:6]}-{d[6:]}" if len(d) == 10 else d


def cuentas_google() -> list[dict]:
    """
    Las cuentas colgadas de nuestra MCC.

    Se pregunta a la MCC por sus `customer_client`, que es UNA llamada y trae el nombre.
    La alternativa (`listAccessibleCustomers`) devuelve solo identificadores: habría que
    consultar cada cuenta por separado para saber cómo se llama, que es justo el dato
    por el que se hace esto.
    """
    ex_google = _modulo_google()
    Credenciales, consultar = ex_google.Credenciales, ex_google.consultar
    solo_digitos = ex_google.solo_digitos

    try:
        cred = Credenciales()
    except ValueError as ex:
        raise HTTPException(503, str(ex))
    mcc = solo_digitos(os.environ.get("GOOGLE_LOGIN_CUSTOMER_ID", ""))
    if not mcc:
        raise HTTPException(503, "Sin GOOGLE_LOGIN_CUSTOMER_ID no sé a qué MCC preguntarle "
                                 "por sus cuentas. Ponla en Railway o escribe el id a mano.")
    out = []
    for fila in consultar(cred, mcc, GAQL_CLIENTES):
        c = fila.get("customerClient") or {}
        cid = solo_digitos(c.get("id") or "")
        if not cid or c.get("manager"):     # las MCC no son cuentas de anuncios
            continue
        out.append({"id": _guion(cid),
                    "nombre": (c.get("descriptiveName") or "").strip() or _guion(cid),
                    "moneda": c.get("currencyCode") or None,
                    "tz": c.get("timeZone") or None,
                    "activa": True})
    out.sort(key=lambda x: x["nombre"].lower())
    return out


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

    # ── las listas para los desplegables ────────────────────────────────
    def _en_uso() -> dict:
        """
        Qué identificadores ya están asignados a un cliente, y a cuál.

        Se marca en la lista en vez de esconderlo: la MISMA cuenta de anuncios en dos
        clientes suma su gasto dos veces y deja los dos CPL a la mitad, y el location
        repetido haría que dos reportes mostraran los mismos leads. Verlo antes de
        elegir es más barato que descubrirlo cuando los números no cuadran.
        """
        loc, ads = {}, {}
        for c in (almacen.clientes() or []):
            slug = c.get("slug")
            cfg = (config_de(slug) if config_de else {}) or {}
            l = str(cfg.get("ghlLocationId") or c.get("ghl_location_id") or "").strip()
            if l:
                loc[l] = slug
            for a in (cfg.get("cuentas") or []):
                i = str(a.get("id") or "").strip()
                if i:
                    ads[i] = slug
                    ads["".join(ch for ch in i if ch.isdigit())] = slug
        return {"locations": loc, "cuentas": ads}

    def _lista(nombre: str, fn, usados: dict, clave: str) -> dict:
        """
        Cada fuente se resuelve por separado y con su propio error.

        Si Google no contesta, la lista de Meta y la del CRM tienen que llegar igual: el
        panel se queda sin UN desplegable, no sin los tres, y en ese hueco enseña la
        casilla de texto de siempre para no dejar a nadie atascado.
        """
        ahora = time.time()
        guardado = _cache.get(nombre)
        if guardado and ahora - guardado[0] < CACHE_S:
            datos = guardado[1]
        else:
            try:
                datos = {"cuentas": fn(), "error": None}
                _cache[nombre] = (ahora, datos)
            except HTTPException as ex:
                datos = {"cuentas": [], "error": str(ex.detail)}
            except Exception as ex:                    # noqa: BLE001
                log.warning("no se pudo listar %s: %s", nombre, ex)
                datos = {"cuentas": [], "error": f"{type(ex).__name__}: {ex}"}
        # El «en uso» NO se cachea: cambia cada vez que se guarda un cliente.
        marca = usados.get(clave) or {}
        cuentas = [{**c, "usadaPor": marca.get(c["id"]) or
                    marca.get("".join(ch for ch in c["id"] if ch.isdigit()))}
                   for c in datos["cuentas"]]
        return {"cuentas": cuentas, "error": datos["error"]}

    @router.get("/admin/cuentas", dependencies=[Depends(exige_admin)])
    def cuentas(refrescar: int = 0):
        """
        Todo lo que se puede elegir en el panel, con nombre y con quién lo usa ya.

        Las tres en una sola respuesta porque el panel las quiere a la vez y así es un
        viaje en vez de tres. Cacheado; `?refrescar=1` para después de crear una cuenta.
        """
        if refrescar:
            _cache.clear()
        usados = _en_uso()
        return {"ghl": _lista("ghl", cuentas_ghl, usados, "locations"),
                "meta": _lista("meta", cuentas_meta, usados, "cuentas"),
                "google": _lista("google", cuentas_google, usados, "cuentas"),
                "cacheSegundos": CACHE_S}

    app.include_router(router)
    return router
