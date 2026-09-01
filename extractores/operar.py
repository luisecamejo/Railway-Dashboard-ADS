# -*- coding: utf-8 -*-
"""
Herramienta de operación del servicio `reportes`, para lo que NO es la extracción diaria.

    export REPORTES_URL=https://reportes-production-a40d.up.railway.app
    export REPORTES_ADMIN_TOKEN=...            # nunca en la línea de comandos

    python operar.py estado                                  # todos los clientes
    python operar.py config SLUG                             # ver la guardada
    python operar.py config SLUG clientes/SLUG.json           # guardarla
    python operar.py construir SLUG --ensayo                 # construir sin publicar
    python operar.py construir SLUG                          # construir y publicar

Nada de esto es de un cliente concreto: hay un fichero por cliente en `clientes/` y el
mismo comando sirve para todos. Los extractores tampoco: recorren TODOS los clientes
que declaren una cuenta de su plataforma.

Para qué existe:

  · La configuración de construcción (productos, roles, SOP, cuentas de anuncios) es
    dato de NEGOCIO: vive en el servicio, no en el repositorio, y se edita sin
    desplegar. Pero la primera vez hay que dejarla ahí, y esto lo hace de forma corta
    y auditable: un POST con el fichero y el resumen de lo que quedó guardado.

  · `construir --ensayo` construye el snapshot y NO lo publica. Es la red de seguridad
    del primer arranque de un cliente: se mira el resumen antes de tapar el reporte
    que el cliente está viendo.

Toma REPORTES_URL y REPORTES_ADMIN_TOKEN del entorno, igual que los extractores, para
que el token no aparezca nunca en una línea de comandos ni en un log.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from comun.http import ErrorHTTP  # noqa: E402
from comun.reportes import Reportes  # noqa: E402

log = logging.getLogger("operar")


def resumen_config(cfg: dict) -> str:
    """Lo justo para reconocer de un vistazo que se guardó lo que se quería."""
    cuentas = ", ".join(f"{c.get('plataforma')} {c.get('id')}"
                        for c in (cfg.get("cuentas") or [])) or "ninguna"
    return (f"tz {cfg.get('tz') or 'SIN ZONA'} · ghlLocationId "
            f"{cfg.get('ghlLocationId') or 'NINGUNO'} · cuentas: {cuentas} · "
            f"{len(cfg.get('productos') or [])} palabras de producto · "
            f"{len(cfg.get('roles') or {})} roles")


def cmd_estado(rep: Reportes, a) -> int:
    clientes = rep.clientes()
    if not clientes:
        log.warning("el servicio no tiene ningún cliente dado de alta")
        return 1
    for c in clientes:
        cfg = rep.config(c["slug"])
        estado = resumen_config(cfg) if cfg else "SIN CONFIGURACIÓN"
        log.info("%s (%s) · %s", c["slug"], "activo" if c.get("activo", True) else "inactivo",
                 estado)
    return 0


def cmd_config(rep: Reportes, a) -> int:
    if not a.fichero:
        actual = rep.config(a.slug)
        if not actual:
            log.warning("%s no tiene configuración guardada", a.slug)
            return 1
        log.info("%s · %s", a.slug, resumen_config(actual))
        return 0

    ruta = pathlib.Path(a.fichero)
    if not ruta.exists():
        ruta = pathlib.Path(__file__).resolve().parent / a.fichero
    cfg = json.loads(ruta.read_text(encoding="utf-8"))

    # Se comprueba aquí, antes de mandarlo: el servicio lo rechaza igual, pero este
    # mensaje dice QUÉ FICHERO está mal.
    if not cfg.get("tz"):
        log.error('%s no declara "tz". Sin zona horaria las fechas no cuadran con el '
                  "CRM y el servicio lo rechaza.", ruta)
        return 2

    antes = rep.config(a.slug)
    if antes:
        log.warning("%s YA tenía configuración; se reemplaza", a.slug)
        log.warning("  antes: %s", resumen_config(antes))

    rep.guardar_config(a.slug, cfg)
    despues = rep.config(a.slug)
    log.info("%s · guardado · %s", a.slug, resumen_config(despues))
    if despues.get("tz") != cfg.get("tz"):
        log.error("lo guardado no coincide con lo enviado: revisa el servicio")
        return 3
    return 0


def cmd_construir(rep: Reportes, a) -> int:
    r = rep.construir(a.slug, publicar=not a.ensayo)
    log.info("%s · %s", a.slug,
             "publicado" if r.get("publicado") else "ENSAYO (no se publicó nada)")
    for linea in (r.get("resumen") or "").splitlines():
        if linea.strip():
            log.info("  %s", linea.strip())
    for av in r.get("avisos") or []:
        log.warning("  aviso: %s", av)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="orden", required=True)

    sub.add_parser("estado", help="qué clientes hay y si tienen configuración")

    pc = sub.add_parser("config", help="ver o guardar la configuración de un cliente")
    pc.add_argument("slug")
    pc.add_argument("fichero", nargs="?", help="JSON a guardar; sin él, solo muestra")

    pb = sub.add_parser("construir", help="juntar los trozos y construir el snapshot")
    pb.add_argument("slug")
    pb.add_argument("--ensayo", action="store_true", help="construir SIN publicar")

    a = p.parse_args()
    rep = Reportes(os.environ.get("REPORTES_URL", ""),
                   os.environ.get("REPORTES_ADMIN_TOKEN", ""))
    try:
        return {"estado": cmd_estado, "config": cmd_config,
                "construir": cmd_construir}[a.orden](rep, a)
    except ErrorHTTP as ex:
        # El servicio ya explica el motivo en el cuerpo (por ejemplo: "Falta el trozo
        # de 'ghl'"). Un traceback de Python encima solo esconde ese mensaje.
        log.error("%s", ex)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
