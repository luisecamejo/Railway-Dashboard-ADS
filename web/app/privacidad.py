"""
Enmascarado de datos personales para los enlaces en modo `demo`.

El modo lo decide el enlace, no el navegador: el enmascarado se hace **en el servidor**,
antes de enviar nada. Un enlace demo nunca transporta el nombre real de un paciente,
así que da igual quién abra las herramientas de desarrollo.

Modos:
  interno  — todo tal cual. Para Luis y el equipo.
  cliente  — todo tal cual. El cliente es el dueño de sus propios datos.
  demo     — nombres de leads enmascarados y nombre del negocio oculto.
             Para casos de éxito, capturas y enseñar el reporte a un tercero.
"""
from __future__ import annotations

import copy
import re

MODOS = ("interno", "cliente", "demo")

_DIGITOS = re.compile(r"\d")


def _enmascarar_nombre(n: str) -> str:
    if not n or not isinstance(n, str):
        return n
    digitos = _DIGITOS.findall(n)
    # Si el "nombre" es en realidad un teléfono, dejamos los 4 últimos dígitos.
    if len(digitos) >= 7:
        return "•••• " + "".join(digitos[-4:])
    partes = [p for p in re.split(r"\s+", n.strip()) if p]
    if not partes:
        return "—"
    return " ".join(p[0].upper() + "." for p in partes[:3])


def aplicar(datos: dict, modo: str) -> dict:
    """Devuelve una copia del snapshot ajustada al modo. No muta el original."""
    if modo not in MODOS:
        modo = "cliente"
    if modo != "demo":
        return datos

    d = copy.deepcopy(datos)
    for lead in d.get("leads") or []:
        if "n" in lead:
            lead["n"] = _enmascarar_nombre(lead.get("n"))
    cli = d.get("cliente")
    if isinstance(cli, dict):
        cli["nombre"] = "Cliente (demo)"
    # Los nombres de vendedores sí se conservan: son del equipo, no pacientes.
    d["_modo"] = "demo"
    return d
