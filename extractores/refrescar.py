# -*- coding: utf-8 -*-
"""
extractor-ahora · una pasada completa para los clientes que la pidieron.

Es el mismo trabajo que hacen los tres crones, pero en un solo contenedor y solo para
los clientes que están en la cola de `reportes`.

Por qué existe como servicio aparte: en Railway, un `redeploy` de un servicio CON cron
no lo ejecuta — solo espera al siguiente tick. Este servicio NO tiene cron, así que un
deploy suyo sí arranca el contenedor, y eso es lo único que el botón de "refrescar
ahora" del dashboard necesita poder provocar.

Y por qué no reimplementa nada: se limita a llamar al `main()` de cada extractor con
SOLO_CLIENTE puesto. Esos main() ya saben pedir la configuración al servicio, filtrar
por cliente, extraer y enviar el trozo. Duplicar esa lógica aquí serían dos sitios
donde arreglar el mismo fallo, que es justo lo que se evitó en el resto de la Fase 1.

El orden importa: Meta y Google primero, GoHighLevel al final. El CRM es la fuente más
lenta y la que dispara la construcción del snapshot, así que cuando llega ya están los
otros dos trozos del día. Es el mismo orden que los crones (09:00, 09:15, 09:30).
"""
from __future__ import annotations

import importlib
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from comun.reportes import Reportes          # noqa: E402
from comun.http import ErrorHTTP             # noqa: E402

log = logging.getLogger("extractor.ahora")

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


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    rep = Reportes(os.environ.get("REPORTES_URL", ""),
                   os.environ.get("REPORTES_ADMIN_TOKEN", ""))

    try:
        cola = rep.cola_refresco()
    except ErrorHTTP as ex:
        log.error("no se pudo leer la cola de refresco: %s", ex)
        return 1

    if not cola:
        # No es un error y no debe salir en rojo: pasa siempre que el servicio se
        # despliega por un cambio de código en vez de por el botón. Y también si
        # `reportes` se reinició justo después de encolar, porque la cola vive en su
        # memoria; en ese caso el usuario vuelve a pulsar y ya está.
        log.info("La cola de refresco está vacía. Nada que hacer.")
        return 0

    log.info("refrescando %d cliente(s): %s", len(cola), ", ".join(cola))
    mal = []
    for slug in cola:
        t0 = time.monotonic()
        ok, detalle = refrescar_cliente(slug)
        detalle = f"{detalle} · total {time.monotonic() - t0:.0f}s"
        if not ok:
            mal.append(slug)
        log.info("%s · %s · %s", slug, "listo" if ok else "CON FALLOS", detalle)
        try:
            # Se avisa SIEMPRE, incluso si falló: si no, el cliente se queda "en curso"
            # en el panel hasta que caduca y el botón no vuelve a funcionar hasta
            # entonces.
            rep.cerrar_refresco(slug, ok=ok, detalle=detalle)
        except ErrorHTTP as ex:
            log.error("%s · no se pudo cerrar el refresco: %s", slug, ex)

    if mal:
        log.error("con fallos: %s", ", ".join(mal))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
