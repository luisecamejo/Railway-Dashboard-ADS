"""
Comprobaciones del refresco a demanda.

    python pruebas/test_refrescar.py

Se monta el router sobre un FastAPI de mentira con un almacén y un resolutor de enlaces
falsos, y se sustituye el disparo a Railway por un contador. Así se prueban las reglas
—que son lo delicado— sin tocar Railway ni las APIs de nadie.

Lo que se protege:
  · que un enlace solo pueda refrescar SU cliente
  · que no se lancen dos extracciones del mismo cliente a la vez
  · que un snapshot reciente bloquee el botón, y que el mensaje diga cuánto falta
  · que el admin pueda saltarse la espera pero NO la extracción en curso
  · que un contenedor muerto no deje al cliente bloqueado para siempre
  · que la cola se entregue UNA vez (dos contenedores no repiten el trabajo)
"""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "web"))

from fastapi import FastAPI, HTTPException, Request        # noqa: E402
from fastapi.testclient import TestClient                  # noqa: E402

from app import rutas_refrescar as R                       # noqa: E402

fallos = []


def ok(nombre, cond):
    print(("  ✓ " if cond else "  ✗ ") + nombre)
    if not cond:
        fallos.append(nombre)
    return cond


def iso(minutos_atras):
    return (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(minutes=minutos_atras)).strftime("%Y-%m-%dT%H:%MZ")


class AlmacenFalso:
    def __init__(self):
        self.gen = {"cliff": iso(600), "majo": iso(5)}

    def snapshot(self, slug):
        return {"generado": self.gen[slug]} if slug in self.gen else None

    def cliente(self, slug):
        return {"slug": slug} if slug in self.gen else None


ENLACES = {"tok-cliff": {"cliente": "cliff", "modo": "cliente"},
           "tok-majo": {"cliente": "majo", "modo": "cliente"}}


def resolver(token, peticion):
    e = ENLACES.get(token)
    if not e:
        raise HTTPException(404, "Ese enlace no existe.")
    return e


disparos = []
R._disparar = lambda: disparos.append(1)          # nada de tocar Railway
R.ESPERA_MIN = 30
R.CADUCA_MIN = 45
R.DURACION_MIN = 20

alm = AlmacenFalso()
app = FastAPI()
R.montar(app, almacen=alm, exige_admin=lambda: None, resolver_enlace=resolver)
c = TestClient(app)

print("\n== refresco a demanda ==")

# 1 · un snapshot viejo se puede refrescar
r = c.get("/r/tok-cliff/refrescar").json()
ok("un snapshot de hace 10 h se puede refrescar", r["puede"] is True and not r["enCurso"])

# 2 · un snapshot recén hecho, no; y dice cuánto falta
r = c.get("/r/tok-majo/refrescar").json()
ok("un snapshot de hace 5 min NO se puede refrescar", r["puede"] is False)
ok("y el motivo dice cuántos minutos faltan", "25 minutos" in r["motivo"])

# 3 · el POST respeta la regla
r = c.post("/r/tok-majo/refrescar")
ok("pedirlo igualmente devuelve 429, no 500", r.status_code == 429)
ok("y no dispara nada", len(disparos) == 0)

# 4 · el caso bueno encola y dispara
r = c.post("/r/tok-cliff/refrescar")
ok("el cliente con datos viejos sí encola", r.status_code == 200)
ok("y dispara el extractor exactamente una vez", len(disparos) == 1)
ok("el estado pasa a 'en curso'", r.json()["enCurso"] is True)

# 5 · no se lanzan dos a la vez
r2 = c.post("/r/tok-cliff/refrescar")
ok("un segundo intento del mismo cliente da 429", r2.status_code == 429)
ok("y no vuelve a disparar", len(disparos) == 1)

# 6 · un enlace solo ve su cliente
ok("el enlace de majo no puede refrescar a cliff",
   c.get("/r/tok-majo/refrescar").json()["cliente"] == "majo")
ok("un token inventado da 404", c.get("/r/no-existe/refrescar").status_code == 404)

# 7 · la cola se entrega una sola vez
q1 = c.get("/admin/cola-refresco").json()["pendientes"]
q2 = c.get("/admin/cola-refresco").json()["pendientes"]
ok("la cola entrega el cliente pedido", q1 == ["cliff"])
ok("y no lo entrega dos veces: un segundo contenedor no repite el trabajo", q2 == [])

# 8 · el admin no puede saltarse una extracción en curso
ok("el admin tampoco lanza dos a la vez",
   c.post("/admin/refrescar/cliff").status_code == 409)

# 9 · al cerrarla, el cliente vuelve a estar libre
c.post("/admin/cola-refresco/cliff", json={"ok": True, "detalle": "prueba"})
r = c.get("/r/tok-cliff/refrescar").json()
ok("cerrado el refresco, ya no está en curso", r["enCurso"] is False)

# 10 · el admin SÍ se salta la espera por antigüedad
ok("el admin puede refrescar un snapshot recén hecho",
   c.post("/admin/refrescar/majo").status_code == 200)
c.post("/admin/cola-refresco/majo", json={"ok": True})
ok("un cliente que no existe da 404", c.post("/admin/refrescar/nadie").status_code == 404)

# 11 · un contenedor muerto no bloquea para siempre
c.post("/r/tok-cliff/refrescar")
R._cola["cliff"]["pedido"] -= dt.timedelta(minutes=R.CADUCA_MIN + 1)
r = c.get("/r/tok-cliff/refrescar").json()
ok("una extracción caducada deja de contar como en curso", r["enCurso"] is False)
ok("y el cliente vuelve a poder refrescar", r["puede"] is True)

# 12 · sin configuración de Railway, el botón lo dice en vez de callarse
def sin_config():
    raise RuntimeError("El refresco a demanda no está configurado: falta RAILWAY_API_TOKEN")
R._disparar = sin_config
r = c.post("/r/tok-cliff/refrescar")
ok("sin configurar, responde 503 y lo explica",
   r.status_code == 503 and "no está configurado" in r.json()["detail"])
ok("y no deja al cliente encolado a medias", "cliff" not in R._cola)

print(f"\n{21 - len(fallos)}/21 comprobaciones")
if fallos:
    print("FALLAN: " + "; ".join(fallos))
    sys.exit(1)
print("todo OK")
