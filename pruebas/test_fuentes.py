"""
Comprobaciones del estado de fuentes que va dentro del snapshot.

    python pruebas/test_fuentes.py

Lo que se protege aquí es una distinción que desde el snapshot NO se puede deducir: "no llegó
el trozo de Google" y "este cliente no anuncia en Google" producen exactamente el mismo cero
de gasto y significan lo contrario. Si esta prueba pasa, el dashboard puede avisar del primero
sin dar un falso positivo con el segundo.
"""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "web"))
from app.rutas_extraccion import estado_fuentes  # noqa: E402

AHORA = dt.datetime.now(dt.timezone.utc)
HOY = AHORA.isoformat()
HACE_3D = (AHORA - dt.timedelta(days=3, hours=1)).isoformat()
# el caso que más importa distinguir de "viejo": el extractor corrió esta mañana y la
# construcción ocurrió un rato después. Son horas, no días: tiene que dar 0.
HACE_2H = (AHORA - dt.timedelta(hours=2)).isoformat()

CFG_AMBAS = {"cuentas": [{"plataforma": "Meta", "id": "1003698104483915"},
                         {"plataforma": "Google", "id": "580-642-2100"},
                         {"plataforma": "Google", "id": "985-244-7343"}]}
CFG_SOLO_META = {"cuentas": [{"plataforma": "Meta", "id": "1003698104483915"}]}

fallos = []


def ok(nombre, cond):
    print(("  ✓ " if cond else "  ✗ ") + nombre)
    if not cond:
        fallos.append(nombre)
    return cond


print("\n== estado de fuentes ==")

# 1 · todo llegó hoy
f = estado_fuentes(CFG_AMBAS, {"ghl": HOY, "meta": HACE_2H, "google": HOY})
ok("con las tres fuentes frescas no hay nada que avisar",
   f["faltan"] == [] and f["viejas"] == [] and f["sinCuenta"] == [])
ok("un trozo de hace 2 horas NO es viejo (extractor y construcción del mismo día)",
   f["detalle"]["meta"]["estado"] == "ok" and f["detalle"]["meta"]["dias"] == 0)
ok("cuenta las cuentas declaradas de cada plataforma",
   f["detalle"]["google"]["cuentas"] == 2 and f["detalle"]["meta"]["cuentas"] == 1)

# 2 · Google declarado y sin trozo → alerta
f = estado_fuentes(CFG_AMBAS, {"ghl": HOY, "meta": HOY})
ok("Google con cuentas declaradas y sin trozo sale en 'faltan'", f["faltan"] == ["google"])
ok("y no se cuela en 'sinCuenta'", f["sinCuenta"] == [])
ok("el detalle dice cuántas cuentas se quedaron sin extraer",
   f["detalle"]["google"] == {"cuentas": 2, "estado": "falta"})

# 3 · cliente que no anuncia en Google → NO es alerta
f = estado_fuentes(CFG_SOLO_META, {"ghl": HOY, "meta": HOY})
ok("un cliente sin cuentas de Google no genera alerta",
   f["faltan"] == [] and f["sinCuenta"] == ["google"])

# 4 · trozo viejo
f = estado_fuentes(CFG_AMBAS, {"ghl": HOY, "meta": HACE_3D, "google": HOY})
ok("un trozo de hace 3 días sale en 'viejas'", f["viejas"] == ["meta"])
ok("y dice cuántos días tiene", f["detalle"]["meta"]["dias"] == 3)

# 5 · una cuenta declarada pero sin id no cuenta como declarada
f = estado_fuentes({"cuentas": [{"plataforma": "Google", "id": "  "}]}, {"ghl": HOY})
ok("una fila de cuenta sin id no cuenta como plataforma declarada",
   f["faltan"] == [] and "google" in f["sinCuenta"])

# 6 · formatos de 'recibido' de los dos almacenes
f = estado_fuentes(CFG_SOLO_META, {"ghl": AHORA, "meta": str(AHORA).replace("T", " ")})
ok("acepta el datetime de Postgres y la cadena con espacio del almacén de ficheros",
   f["detalle"]["ghl"]["estado"] == "ok" and f["detalle"]["meta"]["estado"] == "ok")
ok("normaliza 'recibido' a un ISO corto y en UTC",
   f["detalle"]["ghl"]["recibido"].endswith("Z") and len(f["detalle"]["ghl"]["recibido"]) == 17)

# 7 · sin config de cuentas: no se inventa nada
f = estado_fuentes({}, {"ghl": HOY})
ok("sin cuentas declaradas, ninguna plataforma se reclama",
   f["faltan"] == [] and sorted(f["sinCuenta"]) == ["google", "meta"])

print(f"\n{13 - len(fallos)}/13 comprobaciones")
if fallos:
    print("FALLAN: " + "; ".join(fallos))
    sys.exit(1)
print("todo OK")
