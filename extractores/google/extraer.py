# -*- coding: utf-8 -*-
"""
extractor-google · gasto diario por campaña desde la API de Google Ads.

Sustituye a Windsor para Google, igual que meta/extraer.py lo sustituyó para Meta.

Produce EXACTAMENTE el mismo trozo que ya consume construir.py:

    {"gastoDiario": [{"fecha","campana_id","campana","red":"Google",
                      "spend","impressions","clicks","conversiones"}]}

Una fila por día y campaña, nunca un bloque de varios días: el servicio rechaza con
422 una fila cuyo rango cubra más de una jornada.

Cosas de esta API que hay que tener presentes, y que son la razón de la mitad del
código de abajo:

  · LOS ENTEROS LLEGAN COMO TEXTO. El mapeo JSON de protobuf serializa int64 como
    cadena, así que `costMicros`, `impressions`, `clicks` e `id` vienen entre comillas.
    Sin convertirlos, el gasto se concatena en vez de sumarse — el mismo error que ya
    apareció con Meta.

  · EL GASTO VIENE EN MICROS. 11.257.400 micros son 11,2574. Sin dividir, el CPL sale
    un millón de veces peor y el reporte es absurdo a simple vista; pero dividir por
    100 (confundiendo micros con céntimos) daría cifras verosímiles y equivocadas.

  · LAS CONVERSIONES SON DECIMALES. Google reparte una conversión entre varios
    clics, así que un día puede tener 0,9964 conversiones. No se redondea a entero:
    la suma del periodo sí es la buena y redondear cada día la desvía.

  · EL customer_id VA SIN GUIONES en la URL. En el panel se escribe como lo muestra
    Google (580-642-2100) porque así es como lo lee una persona; aquí se limpian.

  · developer-token es UNO para toda la agencia y no depende del cliente. Lo que
    cambia por cliente es el customer_id, que sale de su configuración.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comun.fechas import ventana
from comun.http import pedir
from comun.reportes import Reportes

log = logging.getLogger("extractor.google")

VERSION_API = os.environ.get("GOOGLE_API_VERSION", "v25").strip()
BASE = f"https://googleads.googleapis.com/{VERSION_API}"
OAUTH = "https://oauth2.googleapis.com/token"


def solo_digitos(cid) -> str:
    """580-642-2100 → 5806422100. La API no acepta los guiones."""
    return "".join(ch for ch in str(cid or "") if ch.isdigit())


def _entero(v) -> int:
    """int64 llega como texto. Y un campo ausente es 0, no un fallo."""
    if v is None or v == "":
        return 0
    return int(float(v))


def _decimal(v) -> float:
    if v is None or v == "":
        return 0.0
    return float(v)


# ═════════════════════════════════════════════════════════════════════════════
#  Credenciales
# ═════════════════════════════════════════════════════════════════════════════
class Credenciales:
    """
    Cambia el refresh token por un access token y lo reutiliza.

    El refresh token no caduca (salvo que se revoque); el access token dura una hora.
    Una extracción entera cabe de sobra en esa hora, así que se pide uno y basta.
    """

    def __init__(self):
        self.client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
        self.client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
        self.refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN", "").strip()
        self.developer_token = os.environ.get("GOOGLE_DEVELOPER_TOKEN", "").strip()
        self.login_customer_id = solo_digitos(os.environ.get("GOOGLE_LOGIN_CUSTOMER_ID", ""))
        self._access = ""

        # Se comprueba al arrancar y no a mitad de la extracción: así el log dice qué
        # falta antes de gastar una sola llamada, y el contenedor termina en rojo.
        faltan = [n for n, v in (("GOOGLE_CLIENT_ID", self.client_id),
                                 ("GOOGLE_CLIENT_SECRET", self.client_secret),
                                 ("GOOGLE_REFRESH_TOKEN", self.refresh_token),
                                 ("GOOGLE_DEVELOPER_TOKEN", self.developer_token)) if not v]
        if faltan:
            raise ValueError("Faltan credenciales de Google Ads: " + ", ".join(faltan) +
                             ". El developer token sale del API Center de una cuenta MCC; "
                             "el resto, del consentimiento OAuth. Ver extractores/google/README.md.")

    def access_token(self) -> str:
        if self._access:
            return self._access
        cuerpo = urllib.parse.urlencode({
            "client_id": self.client_id, "client_secret": self.client_secret,
            "refresh_token": self.refresh_token, "grant_type": "refresh_token",
        }).encode()
        _c, datos, _h = pedir(OAUTH, metodo="POST", cuerpo=cuerpo,
                              cabeceras={"Content-Type": "application/x-www-form-urlencoded"})
        r = json.loads(datos or b"{}")
        self._access = r.get("access_token") or ""
        if not self._access:
            raise RuntimeError(f"Google no devolvió access_token: {str(r)[:200]}")
        return self._access

    def cabeceras(self) -> dict:
        h = {"Authorization": "Bearer " + self.access_token(),
             "developer-token": self.developer_token,
             "Content-Type": "application/json"}
        # Solo cuando se entra a través de la MCC. Mandarlo cuando no toca da error.
        if self.login_customer_id:
            h["login-customer-id"] = self.login_customer_id
        return h


# ═════════════════════════════════════════════════════════════════════════════
#  Consulta
# ═════════════════════════════════════════════════════════════════════════════
def consultar(cred: Credenciales, cid: str, gaql: str) -> list[dict]:
    """
    Ejecuta un GAQL y devuelve las filas.

    Se acepta la respuesta en las DOS formas que puede llegar, en vez de dar una por
    supuesta: `searchStream` responde una LISTA de trozos, cada uno con sus `results`;
    `search` responde un OBJETO con `results` y `nextPageToken`. Si Google cambia de
    una a otra en una versión futura, esto no se rompe.
    """
    url = f"{BASE}/customers/{cid}/googleAds:searchStream"
    filas, token, vueltas = [], None, 0
    while True:
        vueltas += 1
        if vueltas > 200:
            raise RuntimeError("más de 200 páginas de Google Ads: no devuelvo datos a "
                               "medias que parecerían completos")
        peticion = {"query": gaql}
        if token:
            peticion["pageToken"] = token
        _c, datos, _h = pedir(url, metodo="POST",
                              cuerpo=json.dumps(peticion).encode("utf-8"),
                              cabeceras=cred.cabeceras())
        r = json.loads(datos or b"[]")
        if isinstance(r, list):
            for trozo in r:
                filas.extend((trozo or {}).get("results") or [])
            break                      # el stream no pagina: viene entero
        filas.extend(r.get("results") or [])
        token = r.get("nextPageToken")
        if not token:
            break
    return filas


GAQL_GASTO = """
SELECT segments.date, campaign.id, campaign.name,
       metrics.cost_micros, metrics.impressions, metrics.clicks, metrics.conversions
FROM campaign
WHERE segments.date BETWEEN '{desde}' AND '{hasta}'
  AND metrics.impressions > 0
ORDER BY segments.date
""".strip()


def gasto_diario(cred: Credenciales, cid: str, desde: str, hasta: str) -> list[dict]:
    """
    Una fila por día y campaña. `segments.date` es lo que parte el gasto por día, y
    Google lo calcula en la zona horaria DE LA CUENTA, no en UTC: por eso la ventana
    se pide en la zona del negocio y ambas deben coincidir (hallazgo H-10).

    Se piden solo las campañas con impresiones: un día sin actividad no aporta nada al
    reporte y multiplicaría las filas por el número de campañas pausadas.
    """
    crudas = consultar(cred, cid, GAQL_GASTO.format(desde=desde, hasta=hasta))
    fuera = []
    for f in crudas:
        camp = f.get("campaign") or {}
        m = f.get("metrics") or {}
        seg = f.get("segments") or {}
        fecha = seg.get("date")
        if not fecha:
            continue
        fuera.append({
            "fecha": fecha,
            "campana_id": str(camp.get("id") or ""),
            "campana": camp.get("name") or "",
            "red": "Google",
            # micros → unidades monetarias. Se redondea a 4 decimales porque es lo que
            # trae el dato bueno ya verificado, y así no aparece ruido de coma flotante.
            "spend": round(_entero(m.get("costMicros")) / 1_000_000, 4),
            "impressions": _entero(m.get("impressions")),
            "clicks": _entero(m.get("clicks")),
            "conversiones": round(_decimal(m.get("conversions")), 4),
        })
    log.info("gasto diario de %s · %d filas · %.2f de gasto · %d campañas",
             cid, len(fuera), sum(f["spend"] for f in fuera),
             len({f["campana_id"] for f in fuera}))
    return fuera


GAQL_CUENTA = ("SELECT customer.id, customer.descriptive_name, customer.time_zone, "
               "customer.currency_code FROM customer LIMIT 1")


def cuenta_info(cred: Credenciales, cid: str) -> dict:
    try:
        filas = consultar(cred, cid, GAQL_CUENTA)
    except Exception as ex:
        log.warning("no se pudo leer la ficha de %s (%s): se sigue sin ella", cid, ex)
        return {}
    c = (filas[0].get("customer") if filas else {}) or {}
    return {"nombre": c.get("descriptiveName") or "",
            "tz": c.get("timeZone") or "",
            "moneda": c.get("currencyCode") or ""}


# ═════════════════════════════════════════════════════════════════════════════
def extraer_cliente(objetivo: dict, cred: Credenciales) -> dict:
    tz = objetivo["tz"]
    desde, hasta = ventana(tz)
    gasto, cuentas = [], []

    for c in objetivo["cuentas"]:
        cid = solo_digitos(c.get("id"))
        if not cid:
            log.warning("cuenta de Google sin id utilizable: %r", c.get("id"))
            continue
        info = cuenta_info(cred, cid)
        cuentas.append({"plataforma": "Google", "id": c.get("id"),
                        "tz": info.get("tz") or "", "nombre": info.get("nombre") or ""})
        # H-10: si la cuenta y el negocio no comparten huso, el gasto y los leads se
        # cortan en momentos distintos y el CPL diario no es comparable.
        if info.get("tz") and info["tz"] != tz:
            log.warning("¡ZONAS DISTINTAS! la cuenta de Google %s está en %s y el "
                        "negocio en %s. El CPL diario no es comparable hasta que "
                        "coincidan.", c.get("id"), info["tz"], tz)
        gasto.extend(gasto_diario(cred, cid, desde, hasta))

    return {"gastoDiario": gasto, "cuentas": cuentas,
            "_meta": {"ventana": {"desde": desde, "hasta": hasta},
                      "apiVersion": VERSION_API,
                      "cuentas": [c["id"] for c in cuentas]}}


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    rep = Reportes(os.environ.get("REPORTES_URL", ""),
                   os.environ.get("REPORTES_ADMIN_TOKEN", ""))

    solo = os.environ.get("SOLO_CLIENTE", "").strip()
    objetivos = rep.objetivos("Google")
    if solo:
        objetivos = [o for o in objetivos if o["slug"] == solo]
    if not objetivos:
        # No es un error: puede que ningún cliente declare cuenta de Google todavía.
        log.warning("Ningún cliente con cuenta de Google configurada. Nada que hacer.")
        return 0

    cred = Credenciales()          # falla aquí si falta alguna credencial
    fallos = []
    for o in objetivos:
        try:
            crudo = extraer_cliente(o, cred)
            r = rep.enviar_crudo(o["slug"], "google", crudo)
            log.info("%s · enviado · %s", o["slug"], r.get("resumen"))
        except Exception as ex:
            # Un cliente que falla no debe dejar sin datos a los demás.
            log.error("%s · FALLÓ: %s", o["slug"], ex)
            fallos.append(o["slug"])

    if fallos:
        log.error("fallaron %d de %d clientes: %s", len(fallos), len(objetivos), fallos)
        return 1
    log.info("listo · %d cliente(s) de Google", len(objetivos))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
