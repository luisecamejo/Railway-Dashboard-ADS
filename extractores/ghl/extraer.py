# -*- coding: utf-8 -*-
"""
extractor-ghl · oportunidades, pipelines, vendedores y llamadas desde GoHighLevel.

Habla con el servicio `ghl-mcp` que ya está en producción. No se reimplementa la
extracción contra la API de GHL a propósito: ghl-mcp ya resuelve el OAuth de agencia,
el refresco de tokens y la caché de sub-cuentas, y duplicar eso serían dos sitios
donde arreglar el mismo fallo.

Decisiones que costaron una prueba cada una:

  · LLAMADAS. `/conversations/search` no se puede paginar desde aquí: su cursor
    (`startAfterDate`) necesita DOS valores y el paso de parámetros del MCP solo
    admite escalares. La API lo dice tal cual:
        "search_after has 1 value(s) but sort has 2"
    Así que las conversaciones NO se recorren por ahí. Se usa el export de
    vendedores, que sí pagina por offset, y de sus filas se sacan las que traen
    contadores de llamada (coCon/coSin/ciAtt/ciPerd > 0). Solo a ESAS se les piden
    los mensajes de tipo TYPE_CALL. Baja de ~1.986 conversaciones a ~170: un
    trabajo nocturno de minutos en vez de media hora, y sin gastar cuota de GHL
    preguntando por conversaciones que no tienen ni una llamada.

  · VALOR DE LA OPORTUNIDAD. El export de vendedores trae `opp.status` pero no su
    importe, y el dashboard necesita el importe para atribuir ingreso al vendedor.
    Se cruza por `opp.id` contra las oportunidades ya extraídas, que sí lo traen.

  · LA VENTANA la calcula este extractor en la zona horaria DEL NEGOCIO, no en UTC.
    Un contenedor de Railway va en UTC: usar su fecha metería los leads de la noche
    en el día siguiente y los bordes del rango no cuadrarían con el CRM.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

# En Railway el directorio raíz del servicio es /extractores, así que el paquete
# "extractores" NO existe dentro del contenedor: lo que hay en la raíz es comun/, ghl/,
# meta/. Se añade esa carpeta al path y se importa "comun.x", que funciona igual
# corriendo desde la raíz del repositorio que desde /extractores.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comun.fechas import ventana
from comun.mcp import ClienteMCP
from comun.reportes import Reportes

log = logging.getLogger("extractor.ghl")

# Ventana de llamadas y de vendedores. Por defecto la misma que el reporte, que es
# lo que cierra el hallazgo H-3 de la auditoría (antes eran 30 días contra 120 y el
# Call Report salía vacío en el 75% del rango). Se puede acortar si hace falta
# recortar tiempo de ejecución.
DIAS_VENDEDORES = int(os.environ.get("GHL_DIAS_VENDEDORES", "120"))
TOPE_PAGINAS = int(os.environ.get("GHL_TOPE_PAGINAS", "400"))
PAUSA = float(os.environ.get("GHL_PAUSA_LLAMADAS", "0.05"))


def _ndjson(texto) -> tuple[dict, list[dict]]:
    """
    Los exports de ghl-mcp devuelven NDJSON: la primera línea es {"meta":{...}} y
    cada línea siguiente un registro. Devuelve (meta, registros).
    """
    if isinstance(texto, (dict, list)):
        # Si algún día el MCP devolviera ya un objeto, no hay que tocar nada más.
        if isinstance(texto, dict) and "meta" in texto:
            return texto.get("meta") or {}, texto.get("datos") or []
        return {}, texto if isinstance(texto, list) else [texto]
    meta, filas = {}, []
    for linea in str(texto).splitlines():
        linea = linea.strip()
        if not linea:
            continue
        try:
            o = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if isinstance(o, dict) and "meta" in o and not filas and not meta:
            meta = o["meta"] or {}
        elif isinstance(o, dict):
            filas.append(o)
    return meta, filas


def _paginar_cursor(mcp: ClienteMCP, herramienta: str, args: dict,
                    etiqueta: str) -> list[dict]:
    """Recorre un export de ghl-mcp mientras `meta.hasMore` sea true."""
    todo, cursor, n = [], None, 0
    while True:
        n += 1
        if n > TOPE_PAGINAS:
            raise RuntimeError(f"{etiqueta}: más de {TOPE_PAGINAS} páginas. No devuelvo "
                               f"datos a medias que parecerían completos.")
        peticion = dict(args)
        if cursor:
            peticion["cursor"] = cursor
        meta, filas = _ndjson(mcp.llamar(herramienta, peticion))
        todo.extend(filas)
        if not meta.get("hasMore"):
            break
        cursor = meta.get("cursor")
        if not cursor:
            log.warning("%s dice hasMore pero no manda cursor: se para en %d filas",
                        etiqueta, len(todo))
            break
    log.info("%s · %d filas en %d página(s)", etiqueta, len(todo), n)
    return todo


def pipelines(mcp: ClienteMCP, loc: str) -> list[dict]:
    """Pipelines con sus etapas EN ORDEN y con el nombre literal del CRM."""
    r = mcp.llamar("ghl_list_pipelines", {"client": loc})
    crudos = r.get("pipelines") if isinstance(r, dict) else r
    fuera = []
    for p in crudos or []:
        etapas = p.get("stages") or p.get("etapas") or []
        fuera.append({
            "id": p.get("id"),
            "n": p.get("name") or p.get("n") or "",
            # El orden importa: es lo único, junto al estado, que el dashboard puede
            # dar por igual en todos los clientes. Si el CRM manda 'position', manda ese.
            "stages": [{"id": e.get("id"), "n": e.get("name") or e.get("n") or ""}
                       for e in sorted(etapas, key=lambda e: e.get("position", 0))],
        })
    return fuera


def usuarios(mcp: ClienteMCP, loc: str) -> dict:
    """{userId: {"n": nombre}}. Sin esto las llamadas y los cierres salen sin dueño."""
    try:
        r = mcp.llamar("ghl_api_get", {"client": loc, "path": "/users/",
                                       "params": {"locationId": loc}})
    except Exception as ex:
        log.warning("no se pudieron leer los usuarios (%s): el Call Report saldrá con "
                    "ids en vez de nombres", ex)
        return {}
    lista = (r or {}).get("users") if isinstance(r, dict) else r
    fuera = {}
    for u in lista or []:
        uid = u.get("id")
        if not uid:
            continue
        nombre = (u.get("name")
                  or " ".join(x for x in (u.get("firstName"), u.get("lastName")) if x)
                  or u.get("email") or "")
        fuera[uid] = {"n": nombre.strip()}
    return fuera


def oportunidades(mcp: ClienteMCP, loc: str, desde: str, hasta: str) -> list[dict]:
    return _paginar_cursor(mcp, "ghl_export_opportunities_compact",
                           {"client": loc, "startDate": desde, "endDate": hasta,
                            "limit": 500},
                           f"oportunidades {desde}→{hasta}")


def vendedores(mcp: ClienteMCP, loc: str, desde: str, hasta: str) -> list[dict]:
    return _paginar_cursor(mcp, "ghl_export_seller_performance",
                           {"client": loc, "startDate": desde, "endDate": hasta,
                            "limit": 200},
                           f"conversaciones {desde}→{hasta}")


def _tiene_llamadas(fila: dict) -> bool:
    return any(int(fila.get(k) or 0) > 0
               for k in ("coCon", "coSin", "ciAtt", "ciPerd"))


def llamadas(mcp: ClienteMCP, loc: str, convs: list[str]) -> list[dict]:
    """
    Una fila por llamada, con el userId de QUIEN MARCÓ.

    Es el criterio del Call Report de GoHighLevel, y no el dueño del lead: por eso
    los totales por agente cuadran con el CRM. `dur` puede venir a null y se deja a
    null a propósito — contarlo como cero bajaría la media un 25% y el dashboard ya
    explica que la media se calcula solo sobre las que traen duración.
    """
    fuera, fallos = [], 0
    for i, conv in enumerate(convs, 1):
        try:
            r = mcp.llamar("ghl_api_get",
                           {"client": loc, "path": f"/conversations/{conv}/messages",
                            "params": {"type": "TYPE_CALL", "limit": 100}})
        except Exception as ex:
            fallos += 1
            if fallos <= 5:
                log.warning("conversación %s sin llamadas legibles: %s", conv, ex)
            continue
        bloque = ((r or {}).get("messages") or {})
        mensajes = bloque.get("messages") if isinstance(bloque, dict) else None
        for m in mensajes or []:
            llam = (m.get("meta") or {}).get("call") or {}
            fuera.append({
                "userId": m.get("userId") or "",
                "contactId": m.get("contactId"),
                "conv": conv,
                "dir": m.get("direction") or "",
                "status": llam.get("status") or m.get("status") or "sin estado",
                "dur": llam.get("duration"),
                "ts": m.get("dateAdded"),
            })
        if PAUSA:
            time.sleep(PAUSA)          # no machacar la cuota de GHL
        if i % 50 == 0:
            log.info("  llamadas · %d/%d conversaciones · %d llamadas",
                     i, len(convs), len(fuera))
    if fallos:
        log.warning("%d conversaciones no se pudieron leer", fallos)
    return fuera


def extraer_cliente(objetivo: dict, mcp: ClienteMCP) -> dict:
    loc = (objetivo["config"].get("ghlLocationId") or "").strip()
    if not loc:
        raise RuntimeError("la configuración del cliente no trae 'ghlLocationId'")
    tz = objetivo["tz"]
    desde, hasta = ventana(tz)
    vdesde, vhasta = ventana(tz, dias=DIAS_VENDEDORES)

    pipes = pipelines(mcp, loc)
    log.info("%d pipeline(s) · %d etapas", len(pipes),
             sum(len(p["stages"]) for p in pipes))
    opps = oportunidades(mcp, loc, desde, hasta)
    users = usuarios(mcp, loc)
    filas = vendedores(mcp, loc, vdesde, vhasta)

    # El importe de la oportunidad no lo trae el export de vendedores: se cruza.
    valor_de = {o.get("oid"): o.get("val") for o in opps if o.get("oid")}
    sin_valor = 0
    vend = []
    for f in filas:
        opp = f.get("opp") or {}
        oid = opp.get("id")
        estado = opp.get("status") or ""
        valor = valor_de.get(oid)
        if estado == "won" and oid and valor is None:
            # Pasa cuando la oportunidad se creó fuera de la ventana del reporte:
            # la conversación entra pero su oportunidad no. No es un fallo.
            sin_valor += 1
        vend.append({
            "n": f.get("n") or "—",
            "asg": f.get("asg") or "",
            "tipo": f.get("tipo") or "entrante",
            "rtHum": f.get("rtHum"), "rtAut": f.get("rtAut"),
            "ciAtt": f.get("ciAtt", 0), "ciPerd": f.get("ciPerd", 0),
            "coCon": f.get("coCon", 0), "coSin": f.get("coSin", 0),
            "msgIn": f.get("msgIn", 0), "msgOut": f.get("msgOut", 0),
            "humBy": f.get("humBy") or "",
            "t0to": f.get("t0to") or "",
            "oppStatus": estado,
            "oppValue": valor if valor is not None else 0,
        })
    if sin_valor:
        log.info("%d conversaciones ganadas cuya oportunidad cae fuera de la ventana "
                 "del reporte: su ingreso no se atribuye al vendedor", sin_valor)

    convs = [f.get("conv") for f in filas if f.get("conv") and _tiene_llamadas(f)]
    log.info("%d de %d conversaciones traen llamadas: solo a esas se les piden los "
             "mensajes", len(convs), len(filas))
    calls = llamadas(mcp, loc, convs)
    sin_dur = sum(1 for c in calls if c["dur"] is None)
    log.info("%d llamadas · %d salientes · %d sin duración",
             len(calls), sum(1 for c in calls if c["dir"] == "outbound"), sin_dur)

    return {
        "pipelines": pipes,
        "oportunidades": opps,
        "usuarios": users,
        "vendedores": vend,
        "llamadas": calls,
        "ventana": {"desde": desde, "hasta": hasta},
        "ventanaVendedores": {"desde": vdesde, "hasta": vhasta},
        "ventanaLlamadas": {"desde": vdesde, "hasta": vhasta},
        "_meta": {"locationId": loc, "tz": tz,
                  "diasVendedores": DIAS_VENDEDORES,
                  "conversacionesConLlamadas": len(convs)},
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    mcp = ClienteMCP(os.environ.get("GHL_MCP_URL", ""),
                     os.environ.get("GHL_MCP_TOKEN", ""))
    rep = Reportes(os.environ.get("REPORTES_URL", ""),
                   os.environ.get("REPORTES_ADMIN_TOKEN", ""))

    solo = os.environ.get("SOLO_CLIENTE", "").strip()
    # Un cliente vale para este extractor si declara ghlLocationId, no si tiene
    # cuentas de una plataforma de anuncios: el CRM es la fuente obligatoria.
    objetivos = []
    for c in rep.clientes():
        if not c.get("activo", True):
            continue
        cfg = rep.config(c["slug"])
        if not cfg.get("ghlLocationId") or not cfg.get("tz"):
            log.warning("%s no declara ghlLocationId y/o tz: se salta", c["slug"])
            continue
        objetivos.append({"slug": c["slug"], "tz": cfg["tz"], "config": cfg})
    if solo:
        objetivos = [o for o in objetivos if o["slug"] == solo]
    if not objetivos:
        log.warning("Ningún cliente con ghlLocationId configurado. Nada que hacer.")
        return 0

    construir = os.environ.get("CONSTRUIR_AL_TERMINAR", "1") not in ("0", "false", "no")
    fallos = []
    for o in objetivos:
        try:
            crudo = extraer_cliente(o, mcp)
            r = rep.enviar_crudo(o["slug"], "ghl", crudo)
            log.info("%s · enviado · %s", o["slug"], r.get("resumen"))
            if construir:
                # El CRM es la última fuente en llegar (es la más lenta), así que es el
                # momento natural de construir. Si Meta o Google fallaron hoy, se
                # construye con su trozo de ayer en vez de dejar al cliente sin reporte.
                c = rep.construir(o["slug"])
                log.info("%s · publicado · %s", o["slug"],
                         (c.get("resumen") or "").replace("\n", " | "))
                for a in c.get("avisos") or []:
                    log.warning("%s · aviso: %s", o["slug"], a)
        except Exception as ex:
            log.error("%s · FALLÓ: %s", o["slug"], ex)
            fallos.append(o["slug"])

    if fallos:
        log.error("fallaron %d de %d clientes: %s", len(fallos), len(objetivos), fallos)
        return 1
    log.info("listo · %d cliente(s)", len(objetivos))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
