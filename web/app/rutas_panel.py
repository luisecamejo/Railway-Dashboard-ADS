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

HTML = (pathlib.Path(__file__).resolve().parent / "plantillas" / "admin.html") \
    .read_text(encoding="utf-8")

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
