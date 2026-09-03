"""
La página del panel de administración.

Se sirve tal cual, sin datos dentro: todo lo pide con el token que se escribe en la
propia página, así que la página en sí no es un secreto. Va en su propio módulo para no
mezclar HTML con las rutas de datos.
"""
from __future__ import annotations

import pathlib

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

RAIZ = pathlib.Path(__file__).resolve().parent / "plantillas"


def _html() -> str:
    """
    El panel va troceado en `plantillas/panel/*.part`, y se concatena por orden.

    Mismo motivo que el dashboard: escribir en el repositorio cuesta el archivo COMPLETO,
    así que con el panel en una sola pieza de 58 KB cambiar una línea movía 58 KB. Los
    cortes están en frontera de línea, así que la concatenación es el archivo de siempre
    byte a byte — aquí no hay nada que parsear, a diferencia del visor.

    Si no hay partes se sirve `admin.html` de una pieza, que es como estuvo hasta ahora.
    """
    d = RAIZ / "panel"
    partes = sorted(d.glob("*.part")) if d.exists() else []
    if partes:
        return "".join(x.read_text(encoding="utf-8") for x in partes)
    return (RAIZ / "admin.html").read_text(encoding="utf-8")


HTML = _html()

CABECERAS = {
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

router = APIRouter()


@router.get("/admin", response_class=HTMLResponse)
def panel():
    return HTMLResponse(HTML, headers=CABECERAS)
