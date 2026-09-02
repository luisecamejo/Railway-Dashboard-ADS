"""
Comprobaciones del refresco a demanda.

    python pruebas/test_refrescar.py

Se monta el router sobre un FastAPI de mentira, con un almacén y un resolutor de enlaces
falsos, y se sustituye la extracción de verdad por una función que solo tarda un rato.
Así se prueban las reglas —que son lo delicado— sin tocar ninguna API.

Lo que se protege:
  · que un enlace solo pueda refrescar SU cliente
  · que un snapshot reciente bloquee el botón, y que el mensaje diga cuánto falta
  · que el admin pueda saltarse la espera pero NO una petición ya en cola
  · que las extracciones se hagan DE UNA EN UNA: los extractores se configuran por
    variable de entorno, que es global al proceso, así que dos a la vez se pisarían
  · que el segundo de la cola sepa cuántos tiene delante
  · que un fallo de la extracción no deje al cliente bloqueado
  · que el hilo obrero no se muera dejando a alguien en la cola para siempre
"""
import datetime as dt
import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "web"))

from fastapi import FastAPI, HTTPException                 # noqa: E402
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
        self.gen = {"cliff": iso(600), "majo": iso(5), "garage": iso(600)}

    def snapshot(self, slug):
        return {"generado": self.gen[slug]} if slug in self.gen else None

    def cliente(self, slug):
        return {"slug": slug} if slug in self.gen else None


ENLACES = {"tok-cliff": {"cliente": "cliff"}, "tok-majo": {"cliente": "majo"},
           "tok-garage": {"cliente": "garage"}}


def resolver(token, peticion):
    e = ENLACES.get(token)
    if not e:
        raise HTTPException(404, "Ese enlace no existe.")
    return e


# ── la extracción de mentira ───────────────────────────────────
# Registra CUÁNDO entra y sale cada cliente, que es lo que permite comprobar que no se
# solapan. `suelta` la mantiene dentro hasta que la prueba quiera.
entradas, salidas = [], []
suelta = threading.Event()
falla_para = set()
simultaneos, max_simultaneos = 0, 0
guardia = threading.Lock()


def extraccion_falsa(slug):
    global simultaneos, max_simultaneos
    with guardia:
        simultaneos += 1
        max_simultaneos = max(max_simultaneos, simultaneos)
    entradas.append(slug)
    suelta.wait(timeout=10)
    with guardia:
        simultaneos -= 1
    salidas.append(slug)
    if slug in falla_para:
        raise RuntimeError("la API dijo no")
    return True, f"{slug} refrescado"


R._refrescar_cliente = extraccion_falsa
R.ESPERA_MIN = 30
R.CADUCA_MIN = 45
R.DURACION_MIN = 20

alm = AlmacenFalso()
app = FastAPI()
R.montar(app, almacen=alm, exige_admin=lambda: None, resolver_enlace=resolver)
c = TestClient(app)


def espera_hasta(cond, seg=5):
    fin = time.monotonic() + seg
    while time.monotonic() < fin:
        if cond():
            return True
        time.sleep(0.02)
    return False


print("\n== refresco a demanda ==")

# 1 · las reglas de si se puede
r = c.get("/r/tok-cliff/refrescar").json()
ok("un snapshot de hace 10 h se puede refrescar", r["puede"] is True and not r["enCurso"])
r = c.get("/r/tok-majo/refrescar").json()
ok("un snapshot de hace 5 min NO se puede refrescar", r["puede"] is False)
ok("y el motivo dice cuántos minutos faltan", "25 minutos" in r["motivo"])
ok("pedirlo igualmente devuelve 429, no 500",
   c.post("/r/tok-majo/refrescar").status_code == 429)
ok("un enlace solo ve su cliente",
   c.get("/r/tok-majo/refrescar").json()["cliente"] == "majo")
ok("un token inventado da 404", c.get("/r/no-existe/refrescar").status_code == 404)

# 2 · encolar dos y comprobar que NO se solapan
suelta.clear()
r1 = c.post("/r/tok-cliff/refrescar")
ok("el primer cliente encola", r1.status_code == 200 and r1.json()["enCurso"] is True)
ok("el obrero arranca y entra en el primero", espera_hasta(lambda: entradas == ["cliff"]))

r2 = c.post("/r/tok-garage/refrescar")
ok("un segundo cliente distinto también encola", r2.status_code == 200)
ok("pero NO empieza: las extracciones van de una en una", entradas == ["cliff"])
est = c.get("/r/tok-garage/refrescar").json()
ok("y se le dice que está en la cola, con uno delante",
   "en la cola" in est["motivo"] and "1 por delante" in est["motivo"])

ok("repetir el mismo cliente da 429", c.post("/r/tok-cliff/refrescar").status_code == 429)
ok("y el admin tampoco lo encola dos veces",
   c.post("/admin/refrescar/cliff").status_code == 409)

vista = c.get("/admin/refrescos").json()["cola"]
ok("el panel ve la cola entera, en orden de llegada",
   [x["cliente"] for x in vista] == ["cliff", "garage"])
ok("y sabe cuál se está ejecutando",
   vista[0]["curso"] is True and vista[1]["curso"] is False)

# 3 · se suelta: los dos terminan, uno detrás de otro
suelta.set()
ok("los dos acaban", espera_hasta(lambda: sorted(salidas) == ["cliff", "garage"], 8))
ok("nunca hubo dos a la vez", max_simultaneos == 1)
ok("la cola queda vacía", espera_hasta(lambda: c.get("/admin/refrescos").json()["cola"] == []))
ok("y el obrero se muere cuando no queda trabajo",
   espera_hasta(lambda: R._obrero is None or not R._obrero.is_alive()))

# 4 · el admin SÍ se salta la espera por antigüedad
ok("el admin puede refrescar un snapshot recién hecho",
   c.post("/admin/refrescar/majo").status_code == 200)
ok("un cliente que no existe da 404", c.post("/admin/refrescar/nadie").status_code == 404)
ok("majo termina", espera_hasta(lambda: "majo" in salidas, 8))

# 5 · una extracción que falla no deja al cliente bloqueado
falla_para.add("cliff")
c.post("/admin/refrescar/cliff")
ok("tras un fallo, el cliente sale de la cola",
   espera_hasta(lambda: not c.get("/admin/refrescos").json()["cola"], 8))
falla_para.clear()

# 6 · una petición huérfana (contenedor reiniciado a mitad) se descarta por edad
R._cola["garage"] = {"pedido": dt.datetime.now(dt.timezone.utc)
                     - dt.timedelta(minutes=R.CADUCA_MIN + 1),
                     "por": "enlace", "curso": True}
r = c.get("/r/tok-garage/refrescar").json()
ok("una petición caducada deja de contar como en curso", r["enCurso"] is False)
ok("y el cliente vuelve a poder refrescar", r["puede"] is True)

print(f"\n{24 - len(fallos)}/24 comprobaciones")
if fallos:
    print("FALLAN: " + "; ".join(fallos))
    sys.exit(1)
print("todo OK")
