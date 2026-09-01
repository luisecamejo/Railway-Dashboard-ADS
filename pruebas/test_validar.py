"""
Comprobaciones del validador de snapshots.

    python pruebas/test_validar.py

Cada prueba sabotea un snapshot válido de una forma que ya nos ha pasado de verdad y
verifica que el validador lo rechaza. Si estas pruebas pasan, un snapshot roto no llega
a un cliente.
"""
import copy
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "web"))
from app.main import validar  # noqa: E402

BASE = {
    "desde": "2026-05-03",
    "hasta": "2026-08-30",
    "cliente": {"nombre": "Cliente", "tz": "America/Denver"},
    "granularidadGasto": "dia",
    "stages": [{"id": "e1", "n": "Uno", "p": 1, "pipe": "p1"}],
    "camps": [{"id": "c1", "n": "Camp", "w": [{"s": "2026-08-01", "e": "2026-08-01", "sp": 10}]}],
    "leads": [
        {"id": "a", "n": "Ana Pérez", "f": "2026-06-01", "fo": "2026-06-01", "ei": "e1", "rec": 0},
        {"id": "b", "n": "Beto Ruiz", "f": "2026-01-04", "fo": "2026-07-02", "ei": "e1", "rec": 1},
    ],
}


def ok(nombre, cond):
    print(("  ✓ " if cond else "  ✗ ") + nombre)
    return cond


def main():
    bien = [ok("un snapshot correcto pasa", validar(BASE) == [])]

    d = copy.deepcopy(BASE); d["leads"].append(dict(d["leads"][0]))
    bien.append(ok("detecta oportunidades duplicadas",
                   any("duplicad" in x for x in validar(d))))

    d = copy.deepcopy(BASE); d["cliente"]["tz"] = ""
    bien.append(ok("exige zona horaria",
                   any("zona horaria" in x for x in validar(d))))

    d = copy.deepcopy(BASE); d["leads"][1]["rec"] = 0
    bien.append(ok("exige la marca de recurrente si el contacto es anterior",
                   any("recurrentes" in x for x in validar(d))))

    d = copy.deepcopy(BASE); d["leads"][0]["fo"] = "2026-01-01"
    bien.append(ok("detecta oportunidades fuera de la ventana",
                   any("fuera de la ventana" in x for x in validar(d))))

    d = copy.deepcopy(BASE); d["hasta"] = "2026-01-01"
    bien.append(ok("detecta la ventana invertida",
                   any("invertida" in x for x in validar(d))))

    d = copy.deepcopy(BASE); d["camps"][0]["w"][0]["e"] = "2026-08-07"
    bien.append(ok("detecta gasto semanal declarado como diario",
                   any("granularidad" in x for x in validar(d))))

    d = copy.deepcopy(BASE); del d["leads"]
    bien.append(ok("detecta claves obligatorias que faltan",
                   any("leads" in x for x in validar(d))))

    d = copy.deepcopy(BASE)
    d["leads"] = [dict(l, ei="zzz") for l in d["leads"]]
    bien.append(ok("detecta etapas de otro pipeline",
                   any("no está en 'stages'" in x for x in validar(d))))

    print(f"\n{sum(bien)}/{len(bien)} comprobaciones")
    sys.exit(0 if all(bien) else 1)


if __name__ == "__main__":
    main()
