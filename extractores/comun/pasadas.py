# -*- coding: utf-8 -*-
"""
Cómo se recorre la lista de clientes. Un solo sitio para los tres extractores.

Antes cada extractor tenía su propio `for cliente in objetivos: try/except`, y los
tres compartían el mismo defecto: UNA SOLA OPORTUNIDAD. Si un cliente fallaba por algo
que se arregla solo —un 429 porque se agotó la ventana del límite, una conexión
cortada a media respuesta— se quedaba sin reporte hasta el día siguiente. El dashboard
no miente, pero enseña datos de ayer mientras el CRM ya enseña los de hoy: ese es el
descuadre que se ve desde fuera.

Lo que hace esta pieza, y por qué:

  · DOS PASADAS. La segunda repite SOLO los que fallaron, y espera antes. Si la causa
    fue la cuota de la API, reintentar en caliente vuelve a chocar con el mismo límite.

  · CONCURRENCIA ACOTADA. Es la pieza pensada para crecer. Los límites de GoHighLevel
    (~100 peticiones por cada 10 s) y los de Meta son POR SUB-CUENTA / POR CUENTA
    PUBLICITARIA, no por token de agencia: dos clientes distintos NO compiten por la
    misma cuota. Así que recorrerlos de uno en uno no protege de nada — solo alarga.
    Con 8 clientes daba igual; con 100 es la diferencia entre una hora y ocho.
    Cada obrero mantiene su propio ritmo dentro del cliente que le toca, que es donde
    sí hay un límite real que respetar.

  · UN CLIENTE NO TUMBA A LOS DEMÁS. Cada uno tiene su trozo y el servicio construye
    con lo que haya.

  · EL RESUMEN FINAL DICE EL PORQUÉ. Un "fallaron 3 de 8" sin el motivo obliga a bajar
    al log línea por línea. Con 100 clientes eso deja de ser viable: aquí se termina
    nombrando cada fallo con su error y cuánto tardó cada uno.
"""
from __future__ import annotations

import datetime as dt
import logging
import queue
import threading
import time
from typing import Callable

log = logging.getLogger("extractor.pasadas")


class Resultado:
    """Qué pasó con cada cliente. Es lo que se mira cuando hay muchos."""

    def __init__(self):
        self.ok: dict[str, float] = {}          # slug → segundos
        self.fallos: dict[str, str] = {}        # slug → motivo
        self._lock = threading.Lock()

    def anota_ok(self, slug: str, segundos: float) -> None:
        with self._lock:
            self.ok[slug] = segundos
            self.fallos.pop(slug, None)

    def anota_fallo(self, slug: str, motivo: str) -> None:
        with self._lock:
            self.fallos[slug] = motivo

    @property
    def hubo_fallos(self) -> bool:
        return bool(self.fallos)


def ejecutar(objetivos: list[dict], procesar: Callable[[dict], None], *,
             concurrencia: int = 1, pausa_entre: float = 5.0,
             enfriamiento: float = 60.0, pasadas: int = 2,
             dormir: Callable[[float], None] = time.sleep,
             reloj: Callable[[], float] = time.monotonic) -> int:
    """
    Corre `procesar(objetivo)` sobre cada objetivo. Devuelve el código de salida.

    `objetivos` son diccionarios con al menos 'slug'. `procesar` levanta una excepción
    si el cliente falla: aquí se captura, se anota y se sigue con el siguiente.

    OJO con `concurrencia > 1`: `procesar` se llama desde varios hilos a la vez, así
    que lo que use dentro tiene que aguantarlo. Los clientes HTTP de comun/ lo hacen;
    una variable de entorno global como SOLO_CLIENTE, no — por eso se filtra ANTES de
    llegar aquí y nunca dentro de `procesar`.

    `dormir` y `reloj` se sustituyen en las pruebas para no esperar de verdad.
    """
    if not objetivos:
        return 0

    res = Resultado()
    pendientes = list(objetivos)
    total = len(pendientes)
    hechas = 0

    for pasada in range(1, max(1, pasadas) + 1):
        if pasada > 1:
            log.warning("%d cliente(s) fallaron en la pasada %d; se reintentan en "
                        "%.0fs: %s", len(pendientes), pasada - 1, enfriamiento,
                        sorted(o["slug"] for o in pendientes))
            dormir(enfriamiento)

        hechas = pasada
        fallidos = _una_pasada(pendientes, procesar, res, pasada,
                               concurrencia=concurrencia, pausa_entre=pausa_entre,
                               dormir=dormir, reloj=reloj)
        pendientes = fallidos
        if not pendientes:
            break

    return _resumir(res, total, hechas)


def _una_pasada(objetivos: list[dict], procesar, res: Resultado, pasada: int, *,
                concurrencia: int, pausa_entre: float, dormir, reloj) -> list[dict]:
    cola: queue.Queue = queue.Queue()
    for o in objetivos:
        cola.put(o)
    fallidos: list[dict] = []
    candado = threading.Lock()
    obreros = max(1, min(int(concurrencia), len(objetivos)))

    def trabajar(n: int) -> None:
        # Arranque escalonado: si los obreros salen todos en el mismo instante, el
        # primer golpe de peticiones llega junto y es justo el que dispara un 429.
        if n and pausa_entre:
            dormir(pausa_entre * n / obreros)
        primero = True
        while True:
            try:
                o = cola.get_nowait()
            except queue.Empty:
                return
            if not primero and pausa_entre:
                dormir(pausa_entre)
            primero = False
            t0 = reloj()
            try:
                procesar(o)
                res.anota_ok(o["slug"], reloj() - t0)
            except Exception as ex:
                sufijo = "" if pasada == 1 else f" (pasada {pasada})"
                log.error("%s · FALLÓ%s: %s", o["slug"], sufijo, ex)
                res.anota_fallo(o["slug"], f"{type(ex).__name__}: {ex}")
                with candado:
                    fallidos.append(o)
            finally:
                cola.task_done()

    if obreros == 1:
        trabajar(0)
        return fallidos

    hilos = [threading.Thread(target=trabajar, args=(i,), daemon=True,
                              name=f"extractor-{i}") for i in range(obreros)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    # Se conserva el orden original: un resumen que cambia de orden cada noche es
    # más difícil de comparar con el de ayer.
    orden = {o["slug"]: i for i, o in enumerate(objetivos)}
    return sorted(fallidos, key=lambda o: orden.get(o["slug"], 0))


def _resumir(res: Resultado, total: int, pasadas_hechas: int) -> int:
    if res.ok:
        tiempos = sorted(res.ok.values(), reverse=True)
        lentos = sorted(res.ok.items(), key=lambda kv: -kv[1])[:3]
        log.info("%d de %d cliente(s) OK · total %.0fs · el más lento %s (%.0fs)",
                 len(res.ok), total, sum(tiempos), lentos[0][0], lentos[0][1])
        if len(lentos) > 1:
            log.info("  los que más tardan: %s",
                     ", ".join(f"{s} {t:.0f}s" for s, t in lentos))

    if not res.hubo_fallos:
        log.info("listo · %d cliente(s)", total)
        return 0

    log.error("fallaron %d de %d clientes tras %d pasada(s):",
              len(res.fallos), total, pasadas_hechas)
    for slug in sorted(res.fallos):
        # El motivo, recortado: el error de una API puede traer un cuerpo enorme y el
        # resumen final tiene que caber de un vistazo.
        log.error("  · %s — %s", slug, res.fallos[slug][:300].replace("\n", " "))
    return 1


# ═════════════════════════════════════════════════════════════════════════════
#  Partir una ventana de fechas
# ═════════════════════════════════════════════════════════════════════════════
# Vive aquí y no en un extractor porque el problema no es de una API concreta: pedir
# 120 días de golpe es pedirle a un servidor ajeno que haga en una sola petición un
# trabajo que no tiene por qué caberle. Ver meta/extraer.py, donde esto es la
# diferencia entre extraer y no extraer.

def tramos(desde: str, hasta: str, dias: int) -> list[tuple[str, str]]:
    """Parte un rango en trozos de como mucho `dias` días, sin huecos ni solapes."""
    a = dt.date.fromisoformat(desde)
    b = dt.date.fromisoformat(hasta)
    if b < a:
        return []
    paso = max(1, int(dias))
    fuera = []
    while a <= b:
        fin = min(a + dt.timedelta(days=paso - 1), b)
        fuera.append((a.isoformat(), fin.isoformat()))
        a = fin + dt.timedelta(days=1)
    return fuera


def partir_por_la_mitad(desde: str, hasta: str) -> list[tuple[str, str]]:
    """Divide un rango en dos. Devuelve [] si ya es de un solo día."""
    a = dt.date.fromisoformat(desde)
    b = dt.date.fromisoformat(hasta)
    if b <= a:
        return []
    medio = a + dt.timedelta(days=(b - a).days // 2)
    return [(a.isoformat(), medio.isoformat()),
            ((medio + dt.timedelta(days=1)).isoformat(), b.isoformat())]
