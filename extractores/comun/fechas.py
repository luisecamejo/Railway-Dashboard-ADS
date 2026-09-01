# -*- coding: utf-8 -*-
"""
La ventana del reporte. Un solo sitio, porque de aquí salen todos los descuadres.

Reglas, las mismas que ya aplica el dashboard:
  · La ventana termina en el ÚLTIMO DÍA COMPLETO en la zona horaria DEL NEGOCIO.
    El día en curso se excluye: en el CRM sigue acumulando y nunca cuadraría.
  · Son 120 días exactos contando ese último día.
  · Todo se calcula en la zona del negocio, no en UTC ni en la del servidor. Un
    contenedor de Railway va en UTC; si se usara su fecha, un lead de las 22:00 en
    Denver contaría como del día siguiente y los bordes del rango bailarían.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

DIAS = 120


def ventana(tz: str, dias: int = DIAS, hoy: dt.date | None = None) -> tuple[str, str]:
    """Devuelve (desde, hasta) en ISO. `hoy` solo se usa en las pruebas."""
    zona = ZoneInfo(tz)
    local = hoy or dt.datetime.now(zona).date()
    hasta = local - dt.timedelta(days=1)          # el último día COMPLETO
    desde = hasta - dt.timedelta(days=dias - 1)
    return desde.isoformat(), hasta.isoformat()


def dias_entre(desde: str, hasta: str) -> list[str]:
    a = dt.date.fromisoformat(desde)
    b = dt.date.fromisoformat(hasta)
    return [(a + dt.timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]
