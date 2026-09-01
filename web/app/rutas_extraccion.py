# -*- coding: utf-8 -*-
"""
Rutas que usan los extractores. Es la espina dorsal de la Fase 1.

La idea: los extractores son TONTOS y no comparten nada.

    extractor-ghl    ──POST /admin/crudo/{cliente}/ghl──┐
    extractor-meta   ──POST /admin/crudo/{cliente}/meta─┼──> el servicio junta,
    extractor-google ──POST /admin/crudo/{cliente}/google┘    valida y publica

Por qué así y no un extractor que lo haga todo:
  · Un volumen de Railway se monta en UN servicio, así que no hay disco compartido.
    El sitio natural donde juntar los trozos es el servicio que ya tiene el almacén.
  · Cada extractor puede fallar, reintentarse o desplegarse solo, sin tocar los otros.
    Si Meta cae, el trozo de Meta se queda con los datos de ayer y el reporte sigue
    en pie con el resto — en vez de quedarse sin snapshot entero.
  · La construcción del snapshot (construir.py) está ya probada contra el snapshot
    real byte a byte. Corre aquí, una sola vez, no tres veces distintas.

El precio: los trozos crudos ocupan sitio. Se guarda solo el ÚLTIMO de cada fuente.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request

log = logging.getLogger("reportes.extraccion")

router = APIRouter()

FUENTES = ("ghl", "meta", "google")


def montar(app, *, almacen, exige_admin, validar, leer_cuerpo, tope_mb: float):
    """
    Se monta desde main.py pasándole lo que ya existe allí.

    Se hace por inyección y no importando `main` para no crear un import circular
    (main importa este módulo).
    """

    @router.post("/admin/config/{slug}", dependencies=[Depends(exige_admin)])
    def guardar_config(slug: str, cuerpo: dict = Body(...)):
        """
        La configuración de construcción del cliente: productos, SOP, roles, cuentas.

        No vive en el repositorio a propósito: es dato de negocio, cambia sin
        desplegar, y cada cliente tiene la suya.
        """
        if not almacen.cliente(slug):
            raise HTTPException(404, f"El cliente '{slug}' no existe. Créalo primero.")
        for clave in ("nombre", "tz"):
            if not (cuerpo.get(clave) or "").strip():
                raise HTTPException(400, f"La configuración necesita '{clave}'.")
        try:
            almacen.guardar_config(slug, cuerpo)
        except KeyError:
            raise HTTPException(404, f"El cliente '{slug}' no existe.")
        log.info("configuración guardada · %s · %d claves", slug, len(cuerpo))
        return {"ok": True, "cliente": slug, "claves": sorted(cuerpo.keys())}

    @router.get("/admin/config/{slug}", dependencies=[Depends(exige_admin)])
    def leer_config(slug: str):
        c = almacen.cliente(slug)
        if not c:
            raise HTTPException(404, f"El cliente '{slug}' no existe.")
        return {"cliente": slug, "config": c.get("config") or {}}

    @router.post("/admin/crudo/{slug}/{fuente}", dependencies=[Depends(exige_admin)])
    async def recibir_crudo(slug: str, fuente: str, peticion: Request):
        """Un extractor deja su trozo. Sobreescribe el anterior de esa misma fuente."""
        if fuente not in FUENTES:
            raise HTTPException(400, f"Fuente desconocida '{fuente}'. "
                                     f"Se esperan: {', '.join(FUENTES)}")
        if not almacen.cliente(slug):
            raise HTTPException(404, f"El cliente '{slug}' no existe. Créalo primero.")
        try:
            crudo = await leer_cuerpo(peticion, tope_mb, f"crudo de {fuente}")
        except ValueError as ex:
            raise HTTPException(413, str(ex))
        if not crudo:
            raise HTTPException(400, "El cuerpo está vacío.")
        try:
            datos = json.loads(crudo)
        except Exception as ex:
            raise HTTPException(400, f"El cuerpo no es JSON válido: {ex}")
        if not isinstance(datos, dict):
            raise HTTPException(400, "El trozo crudo tiene que ser un objeto JSON.")

        problemas = comprobar_crudo(fuente, datos)
        if problemas:
            # Se rechaza aquí y no al construir: así el extractor se entera en su
            # propio log, con su propio código de salida, en vez de dejar un trozo
            # inservible que rompe la construcción horas después.
            raise HTTPException(422, {"error": f"El trozo de {fuente} no sirve",
                                      "problemas": problemas})

        reg = almacen.guardar_crudo(slug, fuente, datos)
        log.info("crudo recibido · %s/%s · %.0f KB · %s",
                 slug, fuente, reg["bytes"] / 1024, _resumen_crudo(fuente, datos))
        return {"ok": True, "cliente": slug, "fuente": fuente,
                "kb": round(reg["bytes"] / 1024, 1),
                "resumen": _resumen_crudo(fuente, datos)}

    @router.get("/admin/crudo/{slug}", dependencies=[Depends(exige_admin)])
    def estado_crudos(slug: str):
        """Qué trozos hay, de cuándo y qué traen. Sin devolver los datos."""
        if not almacen.cliente(slug):
            raise HTTPException(404, f"El cliente '{slug}' no existe.")
        out = {}
        for fuente, m in (almacen.crudos(slug) or {}).items():
            out[fuente] = {"recibido": str(m.get("recibido")),
                           "kb": round((m.get("bytes") or 0) / 1024, 1),
                           "resumen": _resumen_crudo(fuente, m.get("datos") or {})}
        return {"cliente": slug, "fuentes": out,
                "faltan": [f for f in FUENTES if f not in out]}

    @router.post("/admin/construir/{slug}", dependencies=[Depends(exige_admin)])
    def construir_y_publicar(slug: str, cuerpo: dict = Body(default={})):
        """
        Junta los trozos, construye el snapshot, lo valida y lo publica.

        Con `"publicar": false` hace todo menos publicar y devuelve el resumen: sirve
        para ver qué saldría antes de que lo vea un cliente.
        """
        from .construir import construir, resumen as resumen_de

        cli = almacen.cliente(slug)
        if not cli:
            raise HTTPException(404, f"El cliente '{slug}' no existe.")
        cfg = dict(cli.get("config") or {})
        if not cfg:
            raise HTTPException(409, f"El cliente '{slug}' no tiene configuración. "
                                     f"Mándala a POST /admin/config/{slug} primero.")

        trozos = almacen.crudos(slug) or {}
        if "ghl" not in trozos:
            # Sin CRM no hay leads, y sin leads el snapshot no pasa el validador.
            # Mejor decirlo claro que construir algo vacío.
            raise HTTPException(409, "Falta el trozo de 'ghl': sin datos del CRM no hay "
                                     "reporte. Fuentes presentes: "
                                     + (", ".join(sorted(trozos)) or "ninguna"))

        crudo, procedencia = {}, {}
        for fuente in FUENTES:
            t = trozos.get(fuente)
            if not t:
                continue
            for clave, valor in (t["datos"] or {}).items():
                if clave in ("_meta",):
                    continue
                if clave in crudo and isinstance(valor, list) and isinstance(crudo[clave], list):
                    # gastoDiario y anunciosDiario los rellenan Meta Y Google: se suman
                    # en vez de pisarse. Cada fila ya trae su propia 'red'.
                    crudo[clave] = crudo[clave] + valor
                elif clave in crudo and isinstance(valor, dict) and isinstance(crudo[clave], dict):
                    crudo[clave] = {**crudo[clave], **valor}
                else:
                    crudo[clave] = valor
            procedencia[fuente] = str(t.get("recibido"))

        # La ventana la manda quien construye, no el extractor: así los tres trozos
        # se recortan a la MISMA ventana aunque se hayan extraído en momentos distintos.
        for clave in ("desde", "hasta"):
            if cuerpo.get(clave):
                cfg[clave] = cuerpo[clave]
        if not cfg.get("desde") or not cfg.get("hasta"):
            v = (trozos.get("ghl", {}).get("datos") or {}).get("ventana") or {}
            cfg.setdefault("desde", v.get("desde"))
            cfg.setdefault("hasta", v.get("hasta"))
        if not cfg.get("desde") or not cfg.get("hasta"):
            raise HTTPException(409, "No sé qué ventana de fechas usar: ni la config ni el "
                                     "trozo de ghl traen 'desde'/'hasta'.")

        avisos: list[str] = []
        try:
            datos = construir(cfg, crudo, avisos)
        except ValueError as ex:
            raise HTTPException(422, {"error": "No se pudo construir el snapshot",
                                      "problemas": [str(ex)], "avisos": avisos})

        problemas = validar(datos)
        if problemas:
            raise HTTPException(422, {"error": "El snapshot construido no cuadra",
                                      "problemas": problemas, "avisos": avisos,
                                      "resumen": resumen_de(datos)})

        if cuerpo.get("publicar") is False:
            return {"ok": True, "publicado": False, "avisos": avisos,
                    "procedencia": procedencia, "resumen": resumen_de(datos)}

        reg = almacen.publicar_snapshot(slug, datos)
        almacen.purgar_snapshots(slug, conservar=30)
        log.info("snapshot construido y publicado · %s · %s leads · fuentes %s",
                 slug, reg.get("n_leads"), ", ".join(sorted(procedencia)))
        for a in avisos:
            log.warning("aviso de construcción · %s · %s", slug, a)
        return {"ok": True, "publicado": True, "avisos": avisos,
                "procedencia": procedencia, "resumen": resumen_de(datos),
                **{k: str(v) for k, v in reg.items()}}

    app.include_router(router)
    return router


# ═════════════════════════════════════════════════════════════════════════════
#  Comprobaciones por fuente — un trozo malo se rechaza donde se recibe
# ═════════════════════════════════════════════════════════════════════════════
def comprobar_crudo(fuente: str, d: dict) -> list[str]:
    p: list[str] = []
    if fuente == "ghl":
        if not isinstance(d.get("oportunidades"), list) or not d["oportunidades"]:
            p.append("'oportunidades' tiene que ser una lista con al menos un elemento.")
        if not isinstance(d.get("pipelines"), list) or not d["pipelines"]:
            p.append("'pipelines' tiene que ser una lista con al menos un elemento.")
        else:
            sin_etapas = [x.get("n") for x in d["pipelines"] if not x.get("stages")]
            if len(sin_etapas) == len(d["pipelines"]):
                p.append("Ningún pipeline trae etapas: el embudo saldría vacío.")
        ids = [o.get("oid") for o in (d.get("oportunidades") or []) if o.get("oid")]
        if len(ids) != len(set(ids)):
            p.append(f"Oportunidades duplicadas: {len(ids)} filas y {len(set(ids))} ids "
                     f"únicos. Es el cursor de paginación de GHL saltándose registros.")
        sin_fecha = sum(1 for o in (d.get("oportunidades") or []) if not o.get("created"))
        if sin_fecha:
            p.append(f"{sin_fecha} oportunidades sin 'created': no se pueden fechar.")

    elif fuente in ("meta", "google"):
        filas = d.get("gastoDiario")
        if not isinstance(filas, list):
            p.append("'gastoDiario' tiene que ser una lista (vacía si no hubo gasto).")
        else:
            esperada = "Meta" if fuente == "meta" else "Google"
            malas = [f.get("red") for f in filas if f.get("red") != esperada]
            if malas:
                p.append(f"{len(malas)} filas de gasto no declaran red '{esperada}' "
                         f"(ej. {malas[0]!r}). Se mezclarían las plataformas.")
            sin_fecha = [f for f in filas if not f.get("fecha")]
            if sin_fecha:
                p.append(f"{len(sin_fecha)} filas de gasto sin 'fecha'.")
            # El dashboard promete granularidad diaria; una fila que agrega varios días
            # rompe el CPL por día sin avisar.
            largas = [f for f in filas if f.get("hasta") and f.get("hasta") != f.get("fecha")]
            if largas:
                p.append(f"{len(largas)} filas cubren más de un día: el gasto tiene que "
                         f"venir día a día.")
    return p


def _resumen_crudo(fuente: str, d: dict) -> str:
    if fuente == "ghl":
        return (f"{len(d.get('oportunidades') or [])} oportunidades · "
                f"{len(d.get('pipelines') or [])} pipelines · "
                f"{len(d.get('llamadas') or [])} llamadas · "
                f"{len(d.get('vendedores') or [])} conversaciones")
    filas = d.get("gastoDiario") or []
    gasto = round(sum(float(f.get("spend") or 0) for f in filas), 2)
    dias = len({f.get("fecha") for f in filas})
    camps = len({f.get("campana_id") for f in filas})
    return (f"{gasto} de gasto · {camps} campañas · {dias} días · "
            f"{len(d.get('anunciosDiario') or [])} filas de anuncios · "
            f"{len(d.get('miniaturas') or {})} miniaturas")
