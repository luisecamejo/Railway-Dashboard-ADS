#!/usr/bin/env python3
"""
Une los trozos de web/visor/partes/ en un dashboard.html de una pieza.

    python scripts/unir_partes.py --salida /tmp/dashboard.html

Sirve para trabajar cómodo: se une, se edita el archivo grande, y se vuelve a partir con
scripts/partir_en_partes.py. El repositorio solo guarda los trozos, así que no hay dos
copias que puedan quedar desincronizadas.
"""
import argparse
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
PARTES = RAIZ / "web" / "visor" / "partes"

p = argparse.ArgumentParser()
p.add_argument("--salida", default="/tmp/dashboard.html")
a = p.parse_args()

trozos = sorted(PARTES.glob("*.part"))
if not trozos:
    sys.exit(f"No hay trozos en {PARTES}")
html = "".join(t.read_text(encoding="utf-8") for t in trozos)
pathlib.Path(a.salida).write_text(html, encoding="utf-8")
print(f"✓ {len(trozos)} trozos → {a.salida}  ({len(html)/1024:.0f} KB)")
