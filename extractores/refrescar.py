# -*- coding: utf-8 -*-
"""
Una pasada completa de extracción para UN cliente, a demanda.

Es el mismo trabajo que hacen los tres crones de la madrugada, pero para un solo
cliente y cuando alguien lo pide con el botón "Actualizar ahora" del dashboard. Lo
llama un hilo del servicio `reportes` (ver el comentario largo de
web/app/rutas_refrescar.py, que explica por qué corre ahí dentro y no en un servicio
aparte).

Y por qué no reimplementa nada: se limita a llamar al `main()` de cada extractor con
SOLO_CLIENTE puesto. Esos main() ya saben pedir la configuración al servicio, filtrar
por cliente, extraer y enviar el trozo. Duplicar esa lógica aquí serían dos sitios
donde arreglar el mismo fallo, que es justo lo que se evitó en el resto de la Fase 1.

El orden importa: Meta y Google primero, GoHighLevel al final. El CRM es la fuente más
lenta y la que dispara la construcción del snapshot, así que cuando llega ya están los
otros dos trozos. Es el mismo orden que los crones (09:00, 09:15, 09:30 UTC).

OJO al llamarlo: configura los extractores por VARIABLE DE ENTORNO, que es global al
proceso. Dos llamadas a la vez se pisarían SOLO_CLIENTE y un cliente se quedaría sin
refrescar creyendo que sí. Quien lo use tiene que serializarlo — `rutas_refrescar` lo
hace con un único hilo obrero y una cola.
"""
from __future__ import annotations

import importlib
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

log = logging.getLogger("extractor.refresco")

# Meta y Google no construyen; GoHighLevel sí, y por eso va último.
PASOS = (("meta", "meta.extraer", "0"),
         ("google", "google.extraer", "0"),
         ("ghl", "ghl.extraer", "1"))


def refrescar_cliente(slug: str) -> tuple[bool, str]:
    """
    Corre los tres extractores para un solo cliente. Devuelve (ok, detalle).

    Un fallo de Meta o de Google NO aborta la pasada: se sigue con el resto y el
    snapshot se construye con el trozo de ayer de esa plataforma, que es mejor que
    dejar al cliente sin reporte. Y ahora el propio dashboard avisa de que ese trozo
    viene viejo, así que el hueco no pasa desapercibido.

    Un fallo de GoHighLevel sí es un fallo de la pasada: sin CRM no hay reporte.
    """
    partes, fallos = [], []
    for nombre, modulo, construir in PASOS:
        os.environ["SOLO_CLIENTE"] = slug
        os.environ["CONSTRUIR_AL_TERMINAR"] = construir
        t0 = time.monotonic()
        try:
            # Se importa dentro del bucle y no arriba a propósito: varios de estos
            # módulos leen variables de entorno al importarse, y así el fallo de uno
            # que le falte una credencial no impide correr los otros dos.
            mod = importlib.import_module(modulo)
            codigo = mod.main()
            seg = time.monotonic() - t0
            if codigo == 0:
                partes.append(f"{nombre} ok ({seg:.0f}s)")
            else:
                partes.append(f"{nombre} salió con {codigo} ({seg:.0f}s)")
                fallos.append(nombre)
        except Exception as ex:
            partes.append(f"{nombre} FALLÓ: {ex}")
            fallos.append(nombre)
            log.error("%s · %s · FALLÓ: %s", slug, nombre, ex)
    detalle = " · ".join(partes)
    return ("ghl" not in fallos), detalle


# Antes esto tenía un main() que leía una cola en `reportes` y refrescaba a los clientes
# que estuvieran en ella: era el punto de entrada de un cuarto servicio de Railway sin
# cron. Se quitó cuando esa pasada se movió DENTRO de `reportes` (ver el comentario largo
# de web/app/rutas_refrescar.py). Lo que queda —refrescar_cliente()— es lo único que hacía
# falta, y ahora lo llama un hilo del propio servicio.
#
# El módulo se deja aquí, con los extractores, porque es donde tiene sentido leerlo: usa
# sus main() y comparte su forma de trabajar.
