"""
Prueba de no-regresión del constructor de snapshots.

    python pruebas/test_construir.py --crudos /ruta/con/los/ficheros --esperado DATA.json

Sin `--crudos` la prueba se salta (no hay datos de cliente en el repositorio, a propósito:
llevan datos personales de pacientes).

Lo que comprueba: que el constructor GENÉRICO, alimentado solo con configuración y datos
crudos, produce exactamente el mismo snapshot que el script hecho a mano para el primer
cliente. Es lo que impide que "hacerlo genérico" cambie los números en silencio.
"""
import argparse
import copy
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "extractor"))
from construir import construir, resumen  # noqa: E402


def sin_reloj(d):
    """'generado' y 'de' (días estancado) dependen del reloj: no se comparan."""
    d = copy.deepcopy(d)
    d.pop("generado", None)
    for l in d.get("leads", []):
        l.pop("de", None)
    return d


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--crudos", help="carpeta con crudo.json y config.json")
    p.add_argument("--esperado", help="snapshot bueno con el que comparar")
    a = p.parse_args()

    if not a.crudos:
        print("  · saltada: no se pasó --crudos (el repositorio no guarda datos de clientes)")
        return

    raiz = pathlib.Path(a.crudos)
    cfg = json.loads((raiz / "config.json").read_text(encoding="utf-8"))
    crudo = json.loads((raiz / "crudo.json").read_text(encoding="utf-8"))

    avisos = []
    nuevo = construir(cfg, crudo, avisos)
    print(resumen(nuevo))
    print("  avisos:", avisos or "ninguno")

    if not a.esperado:
        print("  · sin --esperado: solo se comprobó que construye")
        return

    viejo = json.loads(pathlib.Path(a.esperado).read_text(encoding="utf-8"))
    va, vb = sin_reloj(viejo), sin_reloj(nuevo)

    solo_viejo = sorted(set(va) - set(vb))
    solo_nuevo = sorted(set(vb) - set(va))
    difs = [k for k in sorted(set(va) & set(vb)) if va[k] != vb[k]]

    if not (solo_viejo or solo_nuevo or difs):
        print("  ✓ idéntico al snapshot de referencia")
        return

    print("  ✗ hay diferencias")
    if solo_viejo:
        print("    claves que faltan:", solo_viejo)
    if solo_nuevo:
        print("    claves nuevas:", solo_nuevo)
    for k in difs:
        x, y = va[k], vb[k]
        if isinstance(x, list) and isinstance(y, list) and len(x) == len(y):
            for i, (fa, fb) in enumerate(zip(x, y)):
                if fa != fb:
                    campos = ([q for q in set(fa) | set(fb) if fa.get(q) != fb.get(q)]
                              if isinstance(fa, dict) else None)
                    print(f"    {k}: primera fila distinta #{i}"
                          + (f", campos {campos}" if campos else f": {fa!r} → {fb!r}"))
                    break
        else:
            print(f"    {k}: {str(x)[:80]!r} → {str(y)[:80]!r}")
    sys.exit(1)


if __name__ == "__main__":
    main()
