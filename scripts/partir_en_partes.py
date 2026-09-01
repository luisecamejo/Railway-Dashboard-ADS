#!/usr/bin/env python3
"""
Parte el dashboard en trozos versionables y los escribe en web/visor/partes/.

    python scripts/partir_en_partes.py /tmp/dashboard.html

¿Por qué? El dashboard es un archivo de ~220 KB. Como fuente de verdad en el repositorio
funciona, pero cualquier cambio obliga a mover el archivo entero. Partido en trozos de
~25 KB por fronteras con sentido, mejorar una parte del dashboard toca un solo archivo
pequeño y el diff se lee.

La garantía es mecánica: la concatenación de los trozos, en orden de nombre, tiene que ser
BYTE A BYTE el original. El script lo comprueba y falla si no lo es.

Flujo de trabajo:
    python scripts/unir_partes.py --salida /tmp/dashboard.html   # une
    (editar /tmp/dashboard.html)
    python scripts/partir_en_partes.py /tmp/dashboard.html       # vuelve a partir
"""
import argparse
import hashlib
import pathlib
import re
import sys

OBJETIVO = 25 * 1024   # tamaño al que se apunta por trozo

# Un corte NUNCA rompe nada: los trozos se concatenan antes de interpretar el archivo, así
# que ninguno tiene que ser válido por su cuenta. Aun así se corta en sitios con sentido
# (una función, un rótulo de bloque, el final del CSS) para que un cambio caiga en un solo
# trozo y el diff se lea bien. Si no aparece ninguna frontera buena, se corta en el
# siguiente salto de línea: nunca a mitad de línea, que haría los diffs ilegibles.
FRONTERAS = re.compile(
    r"^(?:function |const |let |var |async function |/\* ═|// ═|</style>|<style>|"
    r"\s{0,2}<(?:div|header|footer|nav|main|section|table)\b)"
)


def partir(s: str, objetivo: int = OBJETIVO) -> list[str]:
    lineas = s.splitlines(keepends=True)
    trozos, actual, tam = [], [], 0
    for ln in lineas:
        # Corta ANTES de esta línea si ya hay bastante y la línea abre algo nuevo,
        # o si el trozo se ha ido de tamaño y hay que cerrarlo de todas formas.
        if actual and (tam >= objetivo and FRONTERAS.match(ln) or tam >= objetivo * 2):
            trozos.append("".join(actual))
            actual, tam = [], 0
        actual.append(ln)
        tam += len(ln)
    if actual:
        trozos.append("".join(actual))
    return trozos


def main():
    p = argparse.ArgumentParser()
    p.add_argument("dashboard")
    p.add_argument("--destino", default=None)
    p.add_argument("--objetivo-kb", type=int, default=25)
    a = p.parse_args()

    origen = pathlib.Path(a.dashboard)
    s = origen.read_text(encoding="utf-8")
    destino = pathlib.Path(a.destino or (pathlib.Path(__file__).resolve().parent.parent
                                         / "web" / "visor" / "partes"))

    trozos = partir(s, a.objetivo_kb * 1024)

    # La comprobación que hace esto seguro
    if "".join(trozos) != s:
        sys.exit("✗ la concatenación NO reproduce el original. No se escribe nada.")

    if destino.exists():
        for viejo in destino.glob("*.part"):
            viejo.unlink()
    destino.mkdir(parents=True, exist_ok=True)

    for i, t in enumerate(trozos):
        (destino / f"{i:02d}.part").write_text(t, encoding="utf-8")

    rehecho = "".join((destino / f"{i:02d}.part").read_text(encoding="utf-8")
                      for i in range(len(trozos)))
    if rehecho != s:
        sys.exit("✗ lo escrito en disco no reproduce el original.")

    print(f"✓ {len(trozos)} trozos en {destino}  ·  "
          f"{min(len(t) for t in trozos)/1024:.0f}–{max(len(t) for t in trozos)/1024:.0f} KB")
    print(f"  sha256 del original: {hashlib.sha256(s.encode()).hexdigest()[:16]}")


if __name__ == "__main__":
    main()
