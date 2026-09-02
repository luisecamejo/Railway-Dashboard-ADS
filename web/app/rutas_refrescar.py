# -*- coding: utf-8 -*-
"""
Refrescar un reporte a demanda, desde el propio dashboard.

    dashboard ──POST /r/{token}/refrescar──> reportes ──API de Railway──> extractor-ahora
                                                 │                              │
                                                 └──GET /admin/cola-refresco────┘

Por qué hace falta un servicio aparte y no se reutilizan los tres crones: en Railway,
un `redeploy` de un servicio CON cron no lo ejecuta, solo espera al siguiente tick.
Para poder disparar una extracción a demanda hace falta un servicio SIN cron, y ese es
`extractor-ahora`: mismo código, misma imagen, sin horario. Un deploy suyo sí arranca
el contenedor, y eso es lo único que este módulo necesita poder provocar.

Por qué el slug NO viaja en una variable de entorno del servicio: dos clientes
pulsando el botón a la vez se pisarían la variable y uno de los dos se quedaría sin
refrescar creyendo que sí. En vez de eso, aquí se guarda una COLA y el servicio la
consulta al arrancar. Un disparo puede recoger varios clientes de una pasada.

Sobre el límite de tiempo. No se cuenta cuántas veces se ha pulsado el botón: se mira
la EDAD DEL SNAPSHOT, que es el dato que de verdad importa. Si los datos son de hace
cinco minutos, volver a extraer no cambiaría nada — y da igual si esa frescura la puso
el cron de la madrugada o un botón. Además así no hay estado que persistir para que el
límite funcione: sale de lo que ya está guardado.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request

log = logging.getLogger("reportes.refrescar")

router = APIRouter()

# Cuánto tiene que tener el snapshot para que valga la pena volver a extraer.
ESPERA_MIN = int(os.environ.get("REFRESCO_ESPERA_MIN", "30"))
# Cuánto se tarda, más o menos. No es un límite: es lo que se le dice al usuario para
# que no se quede mirando la pantalla. La extracción de GoHighLevel de un cliente con
# dos mil conversaciones tarda entre quince y veinticinco minutos de verdad.
DURACION_MIN = int(os.environ.get("REFRESCO_DURACION_MIN", "20"))
# Si un contenedor se muere a mitad, su cliente se quedaría "en curso" para siempre y
# el botón no volvería a funcionar nunca. Pasado este plazo se da por perdido.
CADUCA_MIN = int(os.environ.get("REFRESCO_CADUCA_MIN", "45"))

API_RAILWAY = os.environ.get("RAILWAY_API_URL",
                             "https://backboard.railway.com/graphql/v2")

# slug -> {"pedido": datetime, "por": str, "curso": bool, "detalle": str|None}
_cola: dict[str, dict] = {}
_cerrojo = threading.Lock()


# ═════════════════════════════════════════════════════════════════════════════
#  Disparar el servicio de extracción a demanda
# ═════════════════════════════════════════════════════════════════════════════
MUTACION = ("mutation($s:String!,$e:String!)"
            "{serviceInstanceRedeploy(serviceId:$s,environmentId:$e)}")


def _disparar() -> None:
    """
    Le pide a Railway que despliegue `extractor-ahora`, lo que lo arranca.

    Lanza RuntimeError con un mensaje que se pueda leer si falta configuración: es
    preferible que el botón diga "no está configurado" que quedarse callado y dejar al
    usuario esperando un refresco que nunca se pidió.
    """
    tok = (os.environ.get("RAILWAY_API_TOKEN") or "").strip()
    srv = (os.environ.get("REFRESCO_SERVICE_ID") or "").strip()
    env = (os.environ.get("RAILWAY_ENVIRONMENT_ID") or "").strip()
    faltan = [n for n, v in (("RAILWAY_API_TOKEN", tok),
                             ("REFRESCO_SERVICE_ID", srv),
                             ("RAILWAY_ENVIRONMENT_ID", env)) if not v]
    if faltan:
        raise RuntimeError("El refresco a demanda no está configurado: falta "
                           + ", ".join(faltan))

    cuerpo = json.dumps({"query": MUTACION,
                         "variables": {"s": srv, "e": env}}).encode("utf-8")
    pet = urllib.request.Request(API_RAILWAY, data=cuerpo, method="POST", headers={
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(pet, timeout=30) as r:
            datos = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as ex:
        raise RuntimeError(f"Railway devolvió {ex.code} al arrancar el extractor.")
    except Exception as ex:
        raise RuntimeError(f"No se pudo hablar con la API de Railway: {ex}")
    # GraphQL contesta 200 aunque haya fallado: el error va en el cuerpo. Sin mirarlo,
    # un token caducado se vería como un refresco lanzado con éxito.
    if datos.get("errors"):
        msg = (datos["errors"][0] or {}).get("message") or "error sin mensaje"
        raise RuntimeError(f"Railway rechazó el arranque: {msg}")


# ═════════════════════════════════════════════════════════════════════════════
#  Estado
# ═════════════════════════════════════════════════════════════════════════════
def _momento(v) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).strip().replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _en_curso(slug: str) -> Optional[dict]:
    """La petición viva de ese cliente, o None. Descarta las caducadas."""
    with _cerrojo:
        p = _cola.get(slug)
        if not p:
            return None
        if datetime.now(timezone.utc) - p["pedido"] > timedelta(minutes=CADUCA_MIN):
            _cola.pop(slug, None)
            log.warning("refresco de %s dado por perdido: %d min sin terminar",
                        slug, CADUCA_MIN)
            return None
        return dict(p)


def montar(app, *, almacen, exige_admin, resolver_enlace):
    """Se monta desde main.py con lo que ya existe allí."""

    def _edad_min(slug: str) -> tuple[Optional[str], Optional[int]]:
        """(generado, minutos de antigüedad) del snapshot publicado."""
        d = almacen.snapshot(slug)
        g = (d or {}).get("generado")
        t = _momento(g)
        if not t:
            return g, None
        return g, int((datetime.now(timezone.utc) - t).total_seconds() // 60)

    def _estado(slug: str) -> dict:
        g, mins = _edad_min(slug)
        vivo = _en_curso(slug)
        out = {"cliente": slug, "generado": g, "minutos": mins,
               "enCurso": bool(vivo), "duracionMin": DURACION_MIN,
               "esperaMin": ESPERA_MIN}
        if vivo:
            out["pedido"] = vivo["pedido"].strftime("%Y-%m-%dT%H:%MZ")
            out["puede"] = False
            out["motivo"] = ("Ya hay una extracción en marcha para este cliente. "
                             f"Suele tardar unos {DURACION_MIN} minutos; cuando acabe, "
                             "este reporte se actualiza solo.")
        elif mins is not None and mins < ESPERA_MIN:
            out["puede"] = False
            out["motivo"] = (f"Los datos son de hace {mins} minuto"
                             f"{'s' if mins != 1 else ''}: volver a extraer ahora no "
                             f"cambiaría nada. Se puede refrescar de nuevo dentro de "
                             f"{ESPERA_MIN - mins} minutos.")
        else:
            out["puede"] = True
            out["motivo"] = ""
        return out

    def _encolar(slug: str, por: str) -> dict:
        with _cerrojo:
            _cola[slug] = {"pedido": datetime.now(timezone.utc), "por": por,
                           "curso": False, "detalle": None}
        try:
            _disparar()
        except RuntimeError as ex:
            with _cerrojo:
                _cola.pop(slug, None)
            raise HTTPException(503, str(ex))
        log.info("refresco pedido · %s · por %s", slug, por)
        return _estado(slug)

    # ── desde el dashboard, con el token del enlace ──────────────────
    @router.get("/r/{token}/refrescar")
    def estado_enlace(token: str, peticion: Request):
        e = resolver_enlace(token, peticion)
        return _estado(e["cliente"])

    @router.post("/r/{token}/refrescar")
    def pedir_enlace(token: str, peticion: Request):
        """
        Quien tiene el enlace puede refrescar sus propios datos.

        El enlace ES la autorización: ya da acceso a todos los datos del cliente, así
        que poder actualizarlos no añade permiso nuevo. Lo que sí hay que evitar es que
        una pestaña olvidada o un bot dispare extracciones sin parar y se coma la cuota
        de las APIs, y de eso se encarga el límite por antigüedad del snapshot.
        """
        e = resolver_enlace(token, peticion)
        slug = e["cliente"]
        est = _estado(slug)
        if not est["puede"]:
            raise HTTPException(429, est["motivo"])
        return _encolar(slug, "enlace")

    # ── desde el panel ───────────────────────────────────────
    @router.get("/admin/refrescar/{slug}", dependencies=[Depends(exige_admin)])
    def estado_admin(slug: str):
        if not almacen.cliente(slug):
            raise HTTPException(404, f"El cliente '{slug}' no existe.")
        return _estado(slug)

    @router.post("/admin/refrescar/{slug}", dependencies=[Depends(exige_admin)])
    def pedir_admin(slug: str):
        """
        Sin límite por antigüedad: si el operador quiere volver a extraer, sabe por qué.

        Lo único que se sigue respetando es no lanzar dos extracciones del mismo cliente
        a la vez, que no aceleraría nada y duplicaría el gasto de cuota.
        """
        if not almacen.cliente(slug):
            raise HTTPException(404, f"El cliente '{slug}' no existe.")
        if _en_curso(slug):
            raise HTTPException(409, _estado(slug)["motivo"])
        return _encolar(slug, "admin")

    # ── lo que usa `extractor-ahora` ─────────────────────────────
    @router.get("/admin/cola-refresco", dependencies=[Depends(exige_admin)])
    def cola():
        """
        Qué clientes hay que refrescar. Las marca en curso al entregarlas.

        Se marcan aquí y no cuando termina cada una para que un segundo contenedor que
        arrancase a la vez no repitiera el mismo trabajo.
        """
        with _cerrojo:
            pendientes = [s for s, p in _cola.items() if not p["curso"]]
            for s in pendientes:
                _cola[s]["curso"] = True
        if pendientes:
            log.info("cola de refresco entregada · %s", ", ".join(pendientes))
        return {"pendientes": pendientes}

    @router.post("/admin/cola-refresco/{slug}", dependencies=[Depends(exige_admin)])
    def cerrar(slug: str, cuerpo: dict = Body(default={})):
        """El extractor dice cómo acabó. Se saca de la cola en cualquier caso."""
        with _cerrojo:
            _cola.pop(slug, None)
        ok = bool(cuerpo.get("ok"))
        detalle = str(cuerpo.get("detalle") or "")[:500]
        (log.info if ok else log.error)("refresco terminado · %s · %s · %s",
                                        slug, "OK" if ok else "FALLÓ", detalle)
        return {"ok": True, "cliente": slug}

    app.include_router(router)
    return router
