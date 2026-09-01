# -*- coding: utf-8 -*-
"""
extractor-meta · gasto diario, campañas, anuncios y miniaturas desde la Marketing API.

Sustituye a Windsor. Habla directamente con graph.facebook.com.

Credenciales: UN token de usuario del sistema (system user) del Business Manager, con
permiso `ads_read` sobre las cuentas de los clientes. Ese token NO caduca y NO necesita
revisión de app: Meta da "Standard Access" automático a quien tiene un rol en la app.
Se lee de META_TOKEN y nunca se escribe en un log.

Qué NO hace, a propósito:
  · No decide qué clientes hay: se los pregunta al servicio `reportes`, que ya guarda la
    configuración de cada uno (incluidos los ids de cuenta publicitaria).
  · No construye el snapshot. Deja su trozo crudo y se va. Juntar y publicar es del
    servicio, que es el único que tiene todos los trozos.

Sobre 'conversiones': la Marketing API devuelve `actions`, una lista de tipos de acción.
Cuál de ellos es "un lead" depende de cómo tenga el cliente montado el píxel y los
formularios. No hay una respuesta universal, así que:
  · se cuenta el PRIMER tipo de TIPOS_LEAD que aparezca (configurable con
    META_TIPOS_LEAD) — nunca la suma de varios, porque Meta reporta el mismo evento
    con dos nombres distintos y sumarlos lo contaría doble,
  · y se registra en el log el desglose COMPLETO de tipos encontrados, para poder
    ajustarlo con datos reales en vez de a ojo.
Aunque el número salga distinto del de la plataforma, no afecta al CPL del reporte: el
dashboard calcula CPL con los leads del CRM, y esta columna se muestra aparte y
etiquetada como "Conv. red" precisamente porque mide otra cosa.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from collections import Counter

# En Railway el directorio raíz del servicio es /extractores, así que el paquete
# "extractores" NO existe dentro del contenedor: lo que hay en la raíz es comun/, ghl/,
# meta/. Se añade esa carpeta al path y se importa "comun.x", que funciona igual
# corriendo desde la raíz del repositorio que desde /extractores.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comun.fechas import ventana
from comun.http import json_get
from comun.reportes import Reportes

log = logging.getLogger("extractor.meta")

VERSION_API = os.environ.get("META_API_VERSION", "v26.0")
BASE = f"https://graph.facebook.com/{VERSION_API}"

# Los tipos de acción que se cuentan como lead. El primero que exista en la fila manda,
# por orden: si una cuenta reporta 'lead' Y 'offsite_conversion.fb_pixel_lead' para el
# mismo evento, sumar los dos lo contaría doble.
TIPOS_LEAD = [t.strip() for t in os.environ.get(
    "META_TIPOS_LEAD",
    "lead,offsite_conversion.fb_pixel_lead,onsite_conversion.lead_grouped"
).split(",") if t.strip()]

TOPE_PAGINAS = int(os.environ.get("META_TOPE_PAGINAS", "200"))


def _paginar(url: str, params: dict, token: str, etiqueta: str) -> list[dict]:
    """
    Recorre `paging.next` hasta el final.

    El tope de páginas no es paranoia: un bucle de paginación mal terminado contra una
    API que cobra por llamada es una factura sorpresa. Si se alcanza, se levanta en vez
    de devolver datos a medias que parecerían buenos.
    """
    filas, siguiente, n = [], None, 0
    while True:
        n += 1
        if n > TOPE_PAGINAS:
            raise RuntimeError(f"{etiqueta}: más de {TOPE_PAGINAS} páginas. Algo va mal "
                               f"en la paginación; no devuelvo datos incompletos.")
        if siguiente:
            r = json_get(siguiente)
        else:
            r = json_get(url, {**params, "access_token": token})
        filas.extend(r.get("data") or [])
        siguiente = ((r.get("paging") or {}).get("next")) or None
        if not siguiente:
            break
    log.info("%s · %d filas en %d página(s)", etiqueta, len(filas), n)
    return filas


def _conversiones(fila: dict, contador: Counter) -> float:
    acciones = fila.get("actions") or []
    for a in acciones:
        contador[a.get("action_type")] += 1
    for tipo in TIPOS_LEAD:
        for a in acciones:
            if a.get("action_type") == tipo:
                try:
                    return float(a.get("value") or 0)
                except (TypeError, ValueError):
                    return 0.0
    return 0.0


def cuenta_info(act: str, token: str) -> dict:
    """Nombre, zona horaria y moneda. La zona es lo que hace que el gasto y el lead
    caigan en el mismo día; si no coincide con la del CRM hay que declararlo."""
    return json_get(f"{BASE}/act_{act}",
                    {"fields": "name,timezone_name,currency,account_status",
                     "access_token": token})


def gasto_diario(act: str, token: str, desde: str, hasta: str) -> tuple[list[dict], Counter]:
    tipos = Counter()
    filas = _paginar(
        f"{BASE}/act_{act}/insights",
        {"level": "campaign", "time_increment": 1,
         "time_range": json.dumps({"since": desde, "until": hasta}),
         "fields": "campaign_id,campaign_name,spend,impressions,clicks,actions",
         "limit": 500},
        token, f"gasto diario de act_{act}")
    fuera = []
    for r in filas:
        fuera.append({
            "fecha": r.get("date_start"),
            # 'hasta' viaja para que el servicio pueda comprobar que la fila cubre UN día.
            # Con time_increment=1 date_start == date_stop; si algún día dejara de ser
            # así, el servicio lo rechaza en vez de aceptar gasto agregado en silencio.
            "hasta": r.get("date_stop"),
            "campana_id": str(r.get("campaign_id") or ""),
            "campana": r.get("campaign_name") or "",
            "red": "Meta",
            "spend": round(float(r.get("spend") or 0), 2),
            "impressions": int(float(r.get("impressions") or 0)),
            "clicks": int(float(r.get("clicks") or 0)),
            "conversiones": _conversiones(r, tipos),
        })
    return fuera, tipos


def anuncios_diario(act: str, token: str, desde: str, hasta: str) -> list[dict]:
    tipos = Counter()
    filas = _paginar(
        f"{BASE}/act_{act}/insights",
        {"level": "ad", "time_increment": 1,
         "time_range": json.dumps({"since": desde, "until": hasta}),
         "fields": ("ad_id,ad_name,campaign_id,campaign_name,spend,impressions,"
                    "clicks,actions"),
         "limit": 500},
        token, f"anuncios diarios de act_{act}")
    return [{
        "fecha": r.get("date_start"), "hasta": r.get("date_stop"),
        "anuncio_id": str(r.get("ad_id") or ""), "anuncio": r.get("ad_name") or "",
        "campana_id": str(r.get("campaign_id") or ""),
        "campana": r.get("campaign_name") or "", "red": "Meta",
        "spend": round(float(r.get("spend") or 0), 2),
        "impressions": int(float(r.get("impressions") or 0)),
        "clicks": int(float(r.get("clicks") or 0)),
        "conversiones": _conversiones(r, tipos),
    } for r in filas]


def miniaturas(act: str, token: str) -> dict:
    """
    thumbnail_url por anuncio.

    OJO: son URLs FIRMADAS del CDN de Meta y caducan a las pocas semanas. El dashboard
    ya muestra "vencida" cuando una no carga; refrescarlas es volver a extraer, que es
    justo lo que hace este servicio cada noche.
    """
    filas = _paginar(f"{BASE}/act_{act}/ads",
                     {"fields": "id,name,creative{thumbnail_url}", "limit": 200},
                     token, f"miniaturas de act_{act}")
    fuera = {}
    for a in filas:
        url = ((a.get("creative") or {}).get("thumbnail_url") or "").strip()
        if url:
            fuera[str(a.get("id"))] = url
    return fuera


def extraer_cliente(objetivo: dict, token: str) -> dict:
    tz = objetivo["tz"]
    desde, hasta = ventana(tz)
    gasto, anuncios, thumbs, cuentas = [], [], {}, []
    tipos_total = Counter()

    for c in objetivo["cuentas"]:
        act = str(c.get("id") or "").replace("act_", "").strip()
        if not act:
            continue
        info = cuenta_info(act, token)
        tz_meta = info.get("timezone_name") or ""
        cuentas.append({"plataforma": "Meta", "id": act, "tz": tz_meta,
                        "nombre": info.get("name") or ""})
        # H-10 de la auditoría: si la cuenta publicitaria y el negocio no comparten huso,
        # el gasto y los leads se cortan en momentos distintos y el CPL diario miente.
        # Se avisa alto y claro en vez de dejarlo pasar.
        if tz_meta and tz_meta != tz:
            log.warning("¡ZONAS DISTINTAS! act_%s está en %s y el negocio en %s. El CPL "
                        "diario no es comparable hasta que coincidan.", act, tz_meta, tz)

        g, tipos = gasto_diario(act, token, desde, hasta)
        gasto.extend(g)
        tipos_total.update(tipos)
        anuncios.extend(anuncios_diario(act, token, desde, hasta))
        thumbs.update(miniaturas(act, token))

    if tipos_total:
        # El desglose completo, para poder decidir con datos qué es "un lead" en esta
        # cuenta en vez de adivinarlo.
        log.info("tipos de acción vistos en %s: %s", objetivo["slug"],
                 dict(tipos_total.most_common(12)))
        log.info("se están contando como lead, por orden: %s", TIPOS_LEAD)

    return {"gastoDiario": gasto, "anunciosDiario": anuncios, "miniaturas": thumbs,
            "cuentas": cuentas,
            "_meta": {"ventana": {"desde": desde, "hasta": hasta},
                      "apiVersion": VERSION_API,
                      "tiposLead": TIPOS_LEAD,
                      "tiposVistos": dict(tipos_total)}}


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("META_TOKEN", "").strip()
    if not token:
        log.error("Falta META_TOKEN. Es el token del usuario del sistema del Business "
                  "Manager, con ads_read sobre las cuentas de los clientes.")
        return 2
    rep = Reportes(os.environ.get("REPORTES_URL", ""),
                   os.environ.get("REPORTES_ADMIN_TOKEN", ""))

    solo = os.environ.get("SOLO_CLIENTE", "").strip()
    objetivos = rep.objetivos("Meta")
    if solo:
        objetivos = [o for o in objetivos if o["slug"] == solo]
    if not objetivos:
        log.warning("Ningún cliente declara cuentas de Meta en su configuración. "
                    "Nada que hacer.")
        return 0

    fallos = []
    for o in objetivos:
        try:
            crudo = extraer_cliente(o, token)
            r = rep.enviar_crudo(o["slug"], "meta", crudo)
            log.info("%s · enviado · %s", o["slug"], r.get("resumen"))
        except Exception as ex:
            # Un cliente que falla no debe impedir los demás: cada uno tiene su propio
            # trozo y el servicio construye con lo que haya.
            log.error("%s · FALLÓ: %s", o["slug"], ex)
            fallos.append(o["slug"])

    if fallos:
        log.error("fallaron %d de %d clientes: %s", len(fallos), len(objetivos), fallos)
        return 1
    log.info("listo · %d cliente(s) de Meta", len(objetivos))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
