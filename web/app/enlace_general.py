# -*- coding: utf-8 -*-
"""
El enlace general de un cliente: uno solo, el mismo para siempre, y empotrable.

    reporte publicado  →  asegura(almacen, slug)  →  /r/{token}/embed en GoHighLevel

QUÉ PROBLEMA RESUELVE
────────────────────
Los enlaces se creaban a mano, uno por vez que alguien lo necesitaba: eligiendo modo,
escribiendo una nota y pegando los dominios de GoHighLevel. Eso deja dos cosas mal:

  · Cada reporte nuevo invitaba a crear OTRO enlace, y el iframe que ya estaba pegado
    en la web del cliente quedaba compitiendo con uno más nuevo. Nadie sabía cuál era
    el bueno, y revocar el equivocado apagaba la web del cliente.
  · Pegar los dominios a mano es un paso que se olvida, y sin dominios el enlace NO se
    puede empotrar. El síntoma es un iframe en blanco en GoHighLevel, que no se parece
    en nada a la causa.

Ahora cada cliente tiene UN enlace general que nace solo con su primer reporte, ya
empotrable en GoHighLevel, y que no se rehace nunca.

POR QUÉ NO HACE FALTA REGENERARLO
─────────────────────────────────
Porque el enlace no lleva datos dentro: lleva un token que apunta al cliente. Lo que
sirve `/r/{token}/` se resuelve en cada visita —

  · el visor, de la última versión publicada: un cambio de diseño del dashboard llega a
    todos los enlaces que ya existen sin tocarlos;
  · el snapshot, el último del cliente: añadir una cuenta de Meta o de Google cambia lo
    que extraen los extractores, y el mismo enlace enseña el reporte nuevo.

Lo único que el token fija es DE QUIÉN es el reporte y CON QUÉ MODO se ve. Eso no
cambia, así que el enlace tampoco tiene por qué.

MULTIUSO, Y POR QUÉ PUEDE SER EL MISMO PARA EL EQUIPO Y PARA EL CLIENTE
─────────────────────────────────────────────────────────────────
Va en modo `cliente`, y hoy `interno` y `cliente` ven exactamente lo mismo: el único
modo que cambia los datos es `demo`, que enmascara los nombres de los pacientes. Así
que un solo enlace sirve para mirarlo con el cliente y para mirarlo el equipo.

Si algún día el modo interno enseñara algo que el cliente no debe ver, el enlace general
tendría que quedarse en `cliente` y lo interno pasaría a un enlace aparte. Está escrito
aquí porque es la clase de decisión que se olvida y luego se filtra sola.

SE PUEDE ROTAR, PERO A MANO
───────────────────────────
Un enlace permanente y empotrado es justo lo que no quieres tener que revocar por
accidente, así que `revocar` lo rechaza. Pero también es un enlace que sigue funcionando
el día que un cliente se va, y para eso está `rotar`: revoca el actual y crea otro. Pide
el identificador del cliente escrito, y avisa de lo que rompe — el iframe que esté
pegado en su web deja de funcionar y hay que pegar el nuevo.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query

from . import seguridad as seg

log = logging.getLogger("reportes")

# Dónde se empota por defecto. Son los orígenes de GoHighLevel porque es donde vive el
# panel del cliente; `DOMINIOS_EMPOTRADO` los cambia sin desplegar, para el día que un
# cliente estrene dominio propio o se deje de usar GoHighLevel.
GHL = ("https://app.gohighlevel.com https://*.gohighlevel.com "
       "https://*.msgsndr.com https://*.leadconnectorhq.com")

NOTA = "general · se comparte y se empotra · no hace falta rehacerlo"


def dominios_por_defecto() -> list[str]:
    return seg.normaliza_dominios(os.environ.get("DOMINIOS_EMPOTRADO") or GHL)


def buscar(almacen, slug: str):
    """El enlace general vivo de este cliente, o None."""
    for e in almacen.enlaces(slug):
        if e.get("general") and not e.get("revocado"):
            return e
    return None


def _ficha(e: dict, nuevo: bool = False) -> dict:
    doms = e.get("dominios") or []
    return {"nuevo": nuevo,
            "token": e["token"],
            "ruta": f"/r/{e['token']}/",
            "rutaEmbed": f"/r/{e['token']}/embed" if doms else None,
            "dominios": doms,
            "modo": e.get("modo") or "cliente",
            "accesos": e.get("accesos") or 0,
            "creado": str(e.get("creado") or "")}


def asegura(almacen, slug: str) -> dict:
    """
    Deja al cliente con su enlace general, y devuelve cuál es. Idempotente.

    Se llama después de CADA publicación, así que tiene que ser barato y no sorprender:
    si ya hay uno, no se toca su token ni su nota. Lo único que se repara es la falta de
    dominios, porque un enlace general que no se puede empotrar no cumple su función; y
    solo si está vacío, para no pisar unos dominios puestos a mano.
    """
    e = buscar(almacen, slug)
    if e:
        if not (e.get("dominios") or []):
            doms = dominios_por_defecto()
            almacen.dominios_enlace(e["token"], doms)
            log.info("enlace general de %s sin dominios · puestos los de por defecto", slug)
            e = dict(e, dominios=doms)
        return _ficha(e)

    doms = dominios_por_defecto()
    e = almacen.crear_enlace(slug, "cliente", NOTA, None, doms, general=True)
    log.info("ENLACE GENERAL creado · %s · empotrable en %s", slug, ", ".join(doms) or "ningún sitio")
    return _ficha(e, nuevo=True)


def tras_publicar(almacen, slug: str) -> None:
    """
    El gancho que se cuelga de cada publicación de snapshot.

    No puede tumbar una publicación que ya salió bien: el reporte está publicado y los
    enlaces que ya existían funcionan. Si esto falla, se queda en el log y el panel lo
    crea al abrir la ficha.
    """
    try:
        asegura(almacen, slug)
    except Exception as ex:                        # noqa: BLE001
        log.warning("no se pudo asegurar el enlace general de %s: %s", slug, ex)


def montar(app, *, almacen, exige_admin):
    router = APIRouter()

    @router.get("/admin/enlaces/general/{slug}", dependencies=[Depends(exige_admin)])
    def ver(slug: str):
        """Qué enlace general tiene este cliente. No crea nada: lo mira y ya."""
        if not almacen.cliente(slug):
            raise HTTPException(404, f"El cliente '{slug}' no existe.")
        e = buscar(almacen, slug)
        return {"cliente": slug, "hayReporte": almacen.snapshot(slug) is not None,
                **(_ficha(e) if e else {"token": None})}

    @router.post("/admin/enlaces/general/{slug}", dependencies=[Depends(exige_admin)])
    def crear(slug: str):
        """
        Idempotente A PROPÓSITO: se puede llamar mil veces y siempre sale el mismo.

        La llama el gancho de la publicación y también el panel al abrir una ficha, para
        que los clientes dados de alta antes de que esto existiera tengan el suyo sin
        esperar a la extracción de la noche.
        """
        if not almacen.cliente(slug):
            raise HTTPException(404, f"El cliente '{slug}' no existe.")
        # El mismo motivo que en el resto: un enlace sin datos detrás es un enlace que
        # se abre y no enseña nada, y eso parece un enlace roto.
        if almacen.snapshot(slug) is None:
            raise HTTPException(409,
                f"El cliente '{slug}' todavía no tiene datos publicados. Pulsa «Preparar "
                "el reporte» en su ficha: el enlace general se crea solo en cuanto el "
                "reporte exista.")
        return {"ok": True, **asegura(almacen, slug)}

    @router.post("/admin/enlaces/general/{slug}/rotar", dependencies=[Depends(exige_admin)])
    def rotar(slug: str, confirmar: str = Query(default="")):
        """
        Revoca el enlace general y crea otro. Para cuando un cliente se va.

        Pide el identificador escrito porque rompe el iframe que esté pegado en la web
        del cliente, y eso no se ve desde aquí: se ve cuando alguien entra en su web.
        """
        if not almacen.cliente(slug):
            raise HTTPException(404, f"El cliente '{slug}' no existe.")
        if confirmar != slug:
            raise HTTPException(400,
                f"Para rotar el enlace general hay que repetir el identificador exacto "
                f"del cliente ('{slug}'). El enlace de ahora deja de funcionar, y con él "
                "el iframe que esté puesto en su web.")
        viejo = buscar(almacen, slug)
        if viejo:
            almacen.revocar_enlace(viejo["token"])
            log.warning("ENLACE GENERAL ROTADO · %s · revocado el de %s visitas · "
                        "hay que volver a pegar el iframe",
                        slug, viejo.get("accesos") or 0)
        nuevo = asegura(almacen, slug)
        return {"ok": True, "revocado": (viejo or {}).get("token"), **nuevo}

    app.include_router(router)
    return router
