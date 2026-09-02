# -*- coding: utf-8 -*-
"""
Refrescar un reporte a demanda, desde el propio dashboard.

    dashboard ──POST /r/{token}/refrescar──> reportes ──hilo aparte──> los 3 extractores

POR QUÉ CORRE AQUÍ DENTRO Y NO EN UN SERVICIO APARTE
──────────────────────────────────────────────────
El primer diseño era un cuarto servicio de Railway sin cron (`extractor-ahora`) al que
este módulo le disparaba un despliegue por la API. Se descartó por dos razones, y la
segunda es la buena:

  1. El plan de Railway no admite un sexto servicio en este proyecto.
  2. Los extractores NO tienen dependencias: solo librería estándar. Así que meterlos en
     esta imagen no cuesta ni un paquete, y con eso desaparece TODA la maquinaria de
     coordinación: el token de API de Railway, la mutación GraphQL, la cola compartida
     entre dos procesos y una trampa que ya nos habría mordido — un despliegue SKIPPED
     (el que Railway crea cuando un push no toca los ficheros vigilados de un servicio)
     pasa a ser el "último despliegue" y bloquea el `redeploy` siguiente con
     "Cannot redeploy without a snapshot". El botón habría dejado de funcionar sin que
     nadie tocara nada.

El precio, dicho claro: la extracción corre en el contenedor que además sirve las
páginas. Es trabajo de ESPERA (peticiones a GoHighLevel, Meta y Google), así que gasta
poca CPU, y va en un hilo aparte para no bloquear el servidor. Pero si este servicio se
despliega a mitad de una extracción, esa extracción se pierde: de eso se encarga el
plazo de caducidad de más abajo, y el usuario vuelve a pulsar.

UN SOLO OBRERO, EN COLA
─────────────────────
Las extracciones se hacen de una en una, no en paralelo. No es por prudencia: los
extractores se configuran por variable de entorno (SOLO_CLIENTE), que es global al
proceso, así que dos a la vez se pisarían y un cliente se quedaría sin refrescar
creyendo que sí. Con la cola, el segundo espera su turno y se le dice cuántos tiene
delante.

EL LÍMITE DE TIEMPO
─────────────────
No se cuenta cuántas veces se ha pulsado el botón: se mira la EDAD DEL SNAPSHOT, que es
el dato que de verdad importa. Si los datos son de hace cinco minutos, volver a extraer
no cambiaría nada — y da igual si esa frescura la puso el cron de la madrugada o un
botón. Además así no hay estado que persistir para que el límite funcione: sale de lo
que ya está guardado.
"""
from __future__ import annotations

import logging
import os
import pathlib
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

log = logging.getLogger("reportes.refrescar")

router = APIRouter()

# Cuánto tiene que tener el snapshot para que valga la pena volver a extraer.
ESPERA_MIN = int(os.environ.get("REFRESCO_ESPERA_MIN", "30"))
# Cuánto se tarda, más o menos. No es un límite: es lo que se le dice al usuario para
# que no se quede mirando la pantalla. La extracción de GoHighLevel de un cliente con
# dos mil conversaciones tarda entre quince y veinticinco minutos de verdad.
DURACION_MIN = int(os.environ.get("REFRESCO_DURACION_MIN", "20"))
# Si el contenedor se reinicia a mitad, su cliente se quedaría "en curso" para siempre y
# el botón no volvería a funcionar nunca. Pasado este plazo se da por perdido.
CADUCA_MIN = int(os.environ.get("REFRESCO_CADUCA_MIN", "45"))

# slug -> {"pedido": datetime, "por": str, "curso": bool}
_cola: dict[str, dict] = {}
_cerrojo = threading.Lock()
_obrero: Optional[threading.Thread] = None


# ═════════════════════════════════════════════════════════════════════════════
#  El trabajo
# ═════════════════════════════════════════════════════════════════════════════
def _refrescar_cliente(slug: str) -> tuple[bool, str]:
    """
    Corre los tres extractores para un cliente y reconstruye su snapshot.

    La función vive en `extractores/refrescar.py`, con los extractores, y no aquí: es
    ahí donde tiene sentido leerla y ahí donde está el resto de la extracción. Se
    importa tarde porque `extractores/` no es un paquete instalado, solo una carpeta
    hermana dentro de la imagen.
    """
    raiz = pathlib.Path(__file__).resolve().parents[2] / "extractores"
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))
    from refrescar import refrescar_cliente     # noqa: E402
    return refrescar_cliente(slug)


def _siguiente() -> Optional[str]:
    with _cerrojo:
        for s, p in _cola.items():
            if not p["curso"]:
                p["curso"] = True
                return s
    return None


def _trabajar() -> None:
    """Vacía la cola, un cliente detrás de otro, y se muere cuando no queda nada."""
    global _obrero
    while True:
        slug = _siguiente()
        if slug is None:
            with _cerrojo:
                # Se comprueba otra vez DENTRO del cerrojo antes de morir: si no, una
                # petición que llegase en este hueco vería un obrero vivo que ya se está
                # apagando y su cliente se quedaría en la cola para siempre.
                if not any(not p["curso"] for p in _cola.values()):
                    _obrero = None
                    return
            continue
        t0 = time.monotonic()
        try:
            ok, detalle = _refrescar_cliente(slug)
        except Exception as ex:                  # noqa: BLE001
            ok, detalle = False, f"error inesperado: {ex}"
        with _cerrojo:
            _cola.pop(slug, None)
        (log.info if ok else log.error)(
            "refresco terminado · %s · %s · %s · %.0fs",
            slug, "OK" if ok else "FALLÓ", detalle, time.monotonic() - t0)


def _arrancar_obrero() -> None:
    global _obrero
    with _cerrojo:
        if _obrero and _obrero.is_alive():
            return
        _obrero = threading.Thread(target=_trabajar, name="refresco", daemon=True)
        _obrero.start()


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


def _limpia_caducados() -> None:
    """Un reinicio del contenedor deja peticiones huérfanas: se descartan por edad."""
    ahora = datetime.now(timezone.utc)
    with _cerrojo:
        viejos = [s for s, p in _cola.items()
                  if ahora - p["pedido"] > timedelta(minutes=CADUCA_MIN)]
        for s in viejos:
            _cola.pop(s, None)
    for s in viejos:
        log.warning("refresco de %s dado por perdido: %d min sin terminar", s, CADUCA_MIN)


def _pendiente(slug: str) -> Optional[dict]:
    _limpia_caducados()
    with _cerrojo:
        p = _cola.get(slug)
        if not p:
            return None
        # Cuántos tiene delante: los que entraron antes y todavía no han terminado. Los
        # dict de Python conservan el orden de inserción, que aquí es el orden de llegada.
        delante = 0
        for s in _cola:
            if s == slug:
                break
            delante += 1
        return {**p, "delante": delante}


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
        p = _pendiente(slug)
        out = {"cliente": slug, "generado": g, "minutos": mins,
               "enCurso": bool(p), "duracionMin": DURACION_MIN,
               "esperaMin": ESPERA_MIN}
        if p:
            out["pedido"] = p["pedido"].strftime("%Y-%m-%dT%H:%MZ")
            out["puede"] = False
            out["motivo"] = (
                "Ya hay una extracción en marcha para este cliente. "
                if p["curso"] else
                f"Este cliente está en la cola, con {p['delante']} por delante. ")
            out["motivo"] += (f"Suele tardar unos {DURACION_MIN} minutos; cuando acabe, "
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
                           "curso": False}
        _arrancar_obrero()
        log.info("refresco pedido · %s · por %s · %d en cola", slug, por, len(_cola))
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

        Lo único que se sigue respetando es no encolar dos veces el mismo cliente, que
        no aceleraría nada y duplicaría el gasto de cuota.
        """
        if not almacen.cliente(slug):
            raise HTTPException(404, f"El cliente '{slug}' no existe.")
        if _pendiente(slug):
            raise HTTPException(409, _estado(slug)["motivo"])
        return _encolar(slug, "admin")

    @router.get("/admin/refrescos", dependencies=[Depends(exige_admin)])
    def refrescos():
        """La cola entera, para el panel. Solo lectura."""
        _limpia_caducados()
        with _cerrojo:
            return {"cola": [{"cliente": s,
                              "pedido": p["pedido"].strftime("%Y-%m-%dT%H:%MZ"),
                              "por": p["por"], "curso": p["curso"]}
                             for s, p in _cola.items()]}

    app.include_router(router)
    return router
