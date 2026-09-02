# -*- coding: utf-8 -*-
"""
Prueba del extractor de Google Ads SIN credenciales ni red.

    python pruebas/test_google.py                     # se salta: no hay con qué comparar
    python pruebas/test_google.py --crudos CARPETA     # comparación completa

La carpeta debe tener crudo_google.json, que NO está en el repositorio porque lleva
datos de cliente.

Levanta un googleads.googleapis.com de mentira que devuelve las formas de respuesta
REALES y comprueba que el extractor reproduce exactamente el trozo que produjo el
snapshot ya validado. Lo que cada comprobación impide:

  · que los int64 que llegan COMO TEXTO se concatenen en vez de sumarse
  · que los micros se conviertan mal (dividir por 100 en vez de por un millón da
    cifras verosímiles y equivocadas, que es peor que un absurdo evidente)
  · que las conversiones decimales se redondeen por día
  · que el customer_id viaje con guiones, que la API no acepta
  · que un developer token inválido devuelva vacío en vez de fallar: eso publicaría
    el reporte con el gasto de Google a cero sin que nadie se enterara
"""
import argparse, json, logging, os, pathlib, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
# igual que en el contenedor: la raíz de los extractores en el path
sys.path.insert(0, str(RAIZ / "extractores"))
logging.disable(logging.INFO)

fallos = []


def ok(cond, etiqueta, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + etiqueta + (f"  {extra}" if extra else ""))
    if not cond:
        fallos.append(etiqueta)


def servidor(handler, puerto):
    s = HTTPServer(("127.0.0.1", puerto), handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def falso_google(esperado, puerto=8893):
    PAG = 80

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            d = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(d)))
            self.end_headers()
            self.wfile.write(d)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            cuerpo = self.rfile.read(n) or b"{}"

            if self.path == "/token":
                if b"grant_type=refresh_token" not in cuerpo:
                    return self._json({"error": "unsupported_grant_type"}, 400)
                return self._json({"access_token": "acc-123", "expires_in": 3599})

            # La API real exige LAS DOS cabeceras. Se replica para que la prueba lo
            # garantice: sin developer-token responde 401, no datos vacíos.
            if self.headers.get("developer-token") != "dev-tok":
                return self._json({"error": {"code": 401,
                                             "message": "developer token no válido"}}, 401)
            if self.headers.get("Authorization") != "Bearer acc-123":
                return self._json({"error": {"code": 401, "message": "no autorizado"}}, 401)
            # El customer_id de la URL NO puede llevar guiones.
            cid = self.path.split("/customers/")[1].split("/")[0]
            if not cid.isdigit():
                return self._json({"error": {"code": 400,
                                             "message": f"customer id inválido: {cid}"}}, 400)

            q = json.loads(cuerpo).get("query") or ""
            if "FROM customer" in q:
                return self._json([{"results": [{"customer": {
                    "id": cid, "descriptiveName": "Cuenta de prueba",
                    "timeZone": "America/Denver", "currencyCode": "USD"}}]}])

            # searchStream responde una LISTA de trozos, y los int64 van COMO TEXTO.
            filas = [{"segments": {"date": r["fecha"]},
                      "campaign": {"id": str(r["campana_id"]), "name": r["campana"]},
                      "metrics": {"costMicros": str(int(round(r["spend"] * 1_000_000))),
                                  "impressions": str(r["impressions"]),
                                  "clicks": str(r["clicks"]),
                                  "conversions": r["conversiones"]}}
                     for r in esperado]
            trozos = [{"results": filas[i:i + PAG], "fieldMask": "x"}
                      for i in range(0, len(filas), PAG)] or [{"results": []}]
            return self._json(trozos)

    return servidor(H, puerto)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--crudos", help="carpeta con crudo_google.json")
    a = p.parse_args()

    print("\n== extractor de Google Ads ==")
    ruta = (pathlib.Path(a.crudos) / "crudo_google.json") if a.crudos else None
    if not (ruta and ruta.exists()):
        print("  · saltada: no se pasó --crudos con crudo_google.json")
        return 0
    esperado = json.loads(ruta.read_text(encoding="utf-8"))["gastoDiario"]

    s = falso_google(esperado)
    try:
        import google.extraer as G
        G.BASE = "http://127.0.0.1:8893"
        G.OAUTH = "http://127.0.0.1:8893/token"
        os.environ.update({"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "sec",
                           "GOOGLE_REFRESH_TOKEN": "ref",
                           "GOOGLE_DEVELOPER_TOKEN": "dev-tok",
                           "GOOGLE_LOGIN_CUSTOMER_ID": "111-222-3333"})
        real = G.ventana
        G.ventana = lambda tz, dias=120, hoy=None: ("2026-05-03", "2026-08-30")
        try:
            cred = G.Credenciales()
            ok(cred.login_customer_id == "1112223333",
               "el login-customer-id se manda sin guiones")
            got = G.extraer_cliente({"slug": "x", "tz": "America/Denver",
                                     "cuentas": [{"plataforma": "Google",
                                                  "id": "580-642-2100"}]}, cred)
        finally:
            G.ventana = real

        # sin developer token válido la API responde 401: tiene que levantar error
        try:
            malo = G.Credenciales()
            malo.developer_token = "no-vale"
            G.gasto_diario(malo, "5806422100", "2026-05-03", "2026-08-30")
            ok(False, "un developer token inválido debe ser rechazado")
        except Exception:
            ok(True, "un developer token inválido levanta error, no devuelve vacío")

        # y sin ninguna credencial, falla al construir y no a mitad de la extracción
        for clave in ("GOOGLE_CLIENT_ID", "GOOGLE_DEVELOPER_TOKEN"):
            guardado = os.environ.pop(clave)
            try:
                G.Credenciales()
                ok(False, f"sin {clave} tiene que fallar")
            except ValueError as ex:
                ok(clave in str(ex), f"sin {clave} falla al arrancar y lo dice por su nombre")
            finally:
                os.environ[clave] = guardado
    finally:
        s.shutdown()

    k = lambda f: (f["fecha"], f["campana_id"])
    campos = ("fecha", "campana_id", "campana", "red", "spend", "impressions",
              "clicks", "conversiones")
    x = [{c: f.get(c) for c in campos} for f in sorted(esperado, key=k)]
    y = [{c: f.get(c) for c in campos} for f in sorted(got["gastoDiario"], key=k)]
    ok(x == y, "gasto diario idéntico al trozo verificado", f"{len(x)} filas")
    ok(round(sum(f["spend"] for f in y), 2) == round(sum(f["spend"] for f in x), 2),
       "el gasto suma lo mismo (micros convertidos, enteros que venían como texto)",
       f"{round(sum(f['spend'] for f in y), 2)}")
    ok(round(sum(f["conversiones"] for f in y), 4) ==
       round(sum(f["conversiones"] for f in x), 4),
       "las conversiones decimales no se redondean por día",
       f"{round(sum(f['conversiones'] for f in y), 4)}")
    ok(all(f["red"] == "Google" for f in y), "todas las filas declaran red Google")
    ok(all(f.get("hasta") is None for f in got["gastoDiario"]),
       "ninguna fila cubre más de un día (el servicio rechazaría eso con 422)")
    ok(got["cuentas"] and got["cuentas"][0]["id"] == "580-642-2100",
       "la cuenta se guarda como la escribió el operador, con guiones")

    print("\n" + (f"FALLOS: {fallos}" if fallos else "todo OK"))
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
