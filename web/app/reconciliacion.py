# -*- coding: utf-8 -*-
"""
Reconciliación dentro de ROASLytics (nov-2026).

Antes cada reconciliación acababa en un Google Sheet: Claude cruzaba los pagos (Fresha,
Stripe, HouseCall Pro…) o el Call details de Google Ads contra el CRM, montaba un libro
con una fila por decisión (APRUEBAS: SI/NO/DUDA) y alguien lo revisaba a mano. Cada mes
un libro nuevo, y ningún sitio donde ver qué se decidió sobre un contacto hace tres meses.

Ahora el registro vive aquí, por cliente:

  · un LOTE es una reconciliación (un periodo, un criterio: ventas o pagos, una
    plataforma), con su resumen;
  · un ITEM es una fila a decidir: la propuesta de Claude (subir el importe, cambiar el
    source, marcar ganada…), la evidencia, la decisión de la persona y el cierre cuando
    se aplicó en el CRM.

Estados de un item: `abierto` (falta decidir) → `decidido` (SI/NO/DUDA) → `cerrado`
(aplicado en el CRM, o descartado, con el resultado anotado). Los bloques informativos
(cobros sin oportunidad, bloqueados por falta de identificador, fuera del periodo…) no
llevan decisión: nacen `cerrados` salvo que traigan una propuesta.

Este módulo es solo el modelo y las reglas (sin HTTP): lo usan las rutas, las pruebas y
el export a Excel. La Ficha de cliente (datos de atribución del cliente) también se
define aquí porque la reconciliación la lee: criterio, cohorte, app de pagos, pipeline y
etapa de cierre.
"""
from __future__ import annotations

import io
import re
import secrets
from datetime import datetime, timezone
from typing import Iterable, Optional

TIPOS = ("valor", "source", "cobro_sin_oportunidad", "bloqueado", "factura_cero",
         "llamada_sin_contacto", "fuera_periodo", "otro")
# Los que no piden decisión salvo que traigan propuesta: se registran para que el cuadre
# contra el total del export salga exacto y para que quede constancia.
TIPOS_INFORMATIVOS = ("cobro_sin_oportunidad", "bloqueado", "factura_cero",
                      "llamada_sin_contacto", "fuera_periodo")
DECISIONES = ("SI", "NO", "DUDA")
ESTADOS = ("abierto", "decidido", "cerrado")
# Rol → qué puede hacer. `vendedor` es el rol nuevo de ROASLytics en la suite: solo ve la
# reconciliación de sus clientes y solo decide. Cerrar (= se aplicó en el CRM) es del equipo.
PERMISOS = {
    "admin": {"ver", "crear", "decidir", "cerrar", "borrar", "ficha", "exportar", "invitar"},
    "equipo": {"ver", "crear", "decidir", "cerrar", "ficha", "exportar"},
    "vendedor": {"ver", "decidir", "exportar"},
}
UMBRAL_PCT = 15.0   # la regla del 15 %: por encima se propone subir el Opp Value (SI)

TITULOS_TIPO = {
    "valor": "Reconciliar valor",
    "source": "Llamadas de Google · source",
    "cobro_sin_oportunidad": "Cobros sin oportunidad",
    "bloqueado": "Bloqueados por falta de identificador",
    "factura_cero": "Facturas en 0",
    "llamada_sin_contacto": "Llamadas sin contacto en el CRM",
    "fuera_periodo": "Fuera del periodo",
    "otro": "Otros",
}


def ahora() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def nuevo_id(prefijo: str) -> str:
    return f"{prefijo}_{secrets.token_urlsafe(6)}"


def puede(rol: Optional[str], accion: str) -> bool:
    return accion in PERMISOS.get(rol or "", set())


# ── normalización de identificadores (para detectar lo ya reconciliado) ─────────────
def tel_norm(v) -> str:
    d = re.sub(r"\D", "", str(v or ""))
    if len(d) == 11 and d.startswith("1"):   # +1 de EE. UU. y Canadá
        d = d[1:]
    return d


def email_norm(v) -> str:
    return str(v or "").strip().lower()


def nombre_norm(v) -> str:
    return re.sub(r"\s+", " ", str(v or "").strip().lower())


def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("$", "").replace("€", "").replace(" ", "")
    # 1.234,56 (es) o 1,234.56 (en): se decide por el último separador
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".") if s.count(",") == 1 and len(s.split(",")[1]) <= 2 else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def claves_de(it: dict) -> list[str]:
    """Las claves por las que un item identifica a su contacto/oportunidad."""
    ks = []
    if it.get("opp_id"):
        ks.append("opp:" + str(it["opp_id"]).strip())
    if it.get("contact_id"):
        ks.append("contacto:" + str(it["contact_id"]).strip())
    t = tel_norm(it.get("telefono"))
    if len(t) >= 7:
        ks.append("tel:" + t)
    e = email_norm(it.get("email"))
    if "@" in e:
        ks.append("email:" + e)
    return ks


def claves_consulta(opps=(), contactos=(), telefonos=(), emails=()) -> list[str]:
    ks = [f"opp:{str(o).strip()}" for o in opps if str(o).strip()]
    ks += [f"contacto:{str(c).strip()}" for c in contactos if str(c).strip()]
    ks += [f"tel:{tel_norm(t)}" for t in telefonos if len(tel_norm(t)) >= 7]
    ks += [f"email:{email_norm(e)}" for e in emails if "@" in email_norm(e)]
    return ks


# ── el item ──────────────────────────────────────────────────────────────────────────
def normalizar_item(crudo: dict, lote: dict, por: str) -> dict:
    """
    Convierte lo que manda Claude (o el formulario) en un item completo y coherente.
    Calcula la diferencia en porcentaje y pre-rellena la decisión propuesta con la regla
    del 15 % cuando es una reconciliación de valor y no viene ya.
    """
    if not isinstance(crudo, dict):
        raise ValueError("Cada item tiene que ser un objeto.")
    tipo = str(crudo.get("tipo") or "valor").strip().lower()
    if tipo not in TIPOS:
        raise ValueError(f"Tipo de item desconocido: {tipo!r}. Válidos: {', '.join(TIPOS)}.")
    vp, vg = _num(crudo.get("valor_plataforma")), _num(crudo.get("valor_ghl"))
    dif = None
    if tipo == "valor" and vp is not None:
        if vp > 0:
            dif = round((vp - (vg or 0.0)) / vp * 100.0, 1)
        elif vg:
            dif = -100.0
    prop_dec = crudo.get("propuesta_decision")
    if prop_dec is not None:
        prop_dec = str(prop_dec).strip().upper() or None
        if prop_dec not in DECISIONES:
            raise ValueError(f"propuesta_decision tiene que ser SI, NO o DUDA (llegó {prop_dec!r}).")
    elif tipo == "valor" and dif is not None:
        prop_dec = "SI" if dif > UMBRAL_PCT else "NO"
    propuesta = str(crudo.get("propuesta") or "").strip()
    if tipo == "valor" and not propuesta and vp is not None:
        if prop_dec == "SI":
            propuesta = f"Subir el Opp Value a {vp:,.2f}"
            if str(crudo.get("estado_opp") or "").lower() not in ("won", "ganada", "ganado"):
                propuesta += " y marcar ganada"
        else:
            propuesta = "Dejar el valor como está"
    cambios = crudo.get("cambios")
    if cambios is not None and not isinstance(cambios, dict):
        raise ValueError("`cambios` tiene que ser un objeto (p. ej. {\"monetaryValue\": 520}).")
    informativo = bool(crudo.get("informativo", tipo in TIPOS_INFORMATIVOS and not propuesta))
    it = {
        "id": nuevo_id("it"),
        "lote": lote["id"],
        "tipo": tipo,
        "informativo": informativo,
        "contacto": str(crudo.get("contacto") or crudo.get("nombre") or "").strip(),
        "contact_id": (str(crudo.get("contact_id") or "").strip() or None),
        "opp_id": (str(crudo.get("opp_id") or "").strip() or None),
        "cliente_plataforma": str(crudo.get("cliente_plataforma") or "").strip() or None,
        "telefono": str(crudo.get("telefono") or "").strip() or None,
        "email": email_norm(crudo.get("email")) or None,
        "opp_creada": str(crudo.get("opp_creada") or "").strip() or None,
        "contacto_alta": str(crudo.get("contacto_alta") or "").strip() or None,
        "estado_opp": str(crudo.get("estado_opp") or "").strip() or None,
        "etapa": str(crudo.get("etapa") or "").strip() or None,
        "valor_plataforma": vp,
        "valor_ghl": vg,
        "diferencia_pct": dif,
        "source_actual": str(crudo.get("source_actual") or "").strip() or None,
        "source_propuesto": str(crudo.get("source_propuesto") or "").strip() or None,
        "campana": str(crudo.get("campana") or "").strip() or None,
        "fecha_evento": str(crudo.get("fecha_evento") or "").strip() or None,
        "propuesta": propuesta or None,
        "cambios": cambios or None,
        "evidencia": str(crudo.get("evidencia") or "").strip() or None,
        "cruce_por": str(crudo.get("cruce_por") or "").strip() or None,
        "propuesta_decision": prop_dec,
        "decision": None, "nota": None, "decidido_por": None, "decidido_en": None,
        "estado": "cerrado" if informativo else "abierto",
        "cerrado_por": por if informativo else None,
        "cerrado_en": ahora() if informativo else None,
        "resultado": "Informativo: no requiere decisión." if informativo else None,
        "creado_por": por, "creado_en": ahora(),
    }
    it["claves"] = claves_de(it)
    if not it["contacto"] and not it["cliente_plataforma"] and not it["telefono"] and not it["email"]:
        raise ValueError("Un item necesita al menos el nombre del contacto, el del cliente en "
                         "la plataforma, un teléfono o un email.")
    return it


def normalizar_lote(crudo: dict, por: str) -> dict:
    if not isinstance(crudo, dict):
        raise ValueError("El lote tiene que ser un objeto.")
    titulo = str(crudo.get("titulo") or "").strip()
    desde, hasta = str(crudo.get("periodo_desde") or "").strip(), str(crudo.get("periodo_hasta") or "").strip()
    if not titulo:
        titulo = f"Reconciliación {desde} a {hasta}".strip() if desde or hasta else "Reconciliación"
    criterio = str(crudo.get("criterio") or "").strip() or None
    if criterio and criterio.lower() not in ("ventas", "pagos"):
        raise ValueError("El criterio es «Ventas» o «Pagos».")
    return {
        "id": nuevo_id("lote"),
        "titulo": titulo,
        "tipo": str(crudo.get("tipo") or "mixto").strip().lower(),
        "periodo_desde": desde or None, "periodo_hasta": hasta or None,
        "criterio": criterio.capitalize() if criterio else None,
        "plataforma": str(crudo.get("plataforma") or "").strip() or None,
        "cohorte": str(crudo.get("cohorte") or "").strip() or None,
        "resumen": str(crudo.get("resumen") or "").strip() or None,
        "total_export": _num(crudo.get("total_export")),
        "numero_control": crudo.get("numero_control"),
        "estado": "abierto",
        "creado_por": por, "creado_en": ahora(),
        "cerrado_en": None,
    }


# ── lo ya reconciliado ───────────────────────────────────────────────────────────────
def ya_reconciliados(items: Iterable[dict], claves: Iterable[str]) -> dict:
    """
    Para cada clave consultada, los items que ya la llevan (cualquier lote, cualquier
    estado). Es lo que Claude pregunta antes de proponer: «¿este contacto ya se
    reconcilió?». Devuelve {clave: [resumen de item…]}; solo las claves con algo.
    """
    pedidas = set(claves)
    fuera: dict[str, list] = {}
    for it in items:
        for k in it.get("claves") or []:
            if k in pedidas:
                fuera.setdefault(k, []).append(resumen_item(it))
    return fuera


def resumen_item(it: dict) -> dict:
    return {k: it.get(k) for k in ("id", "lote", "tipo", "contacto", "opp_id", "contact_id",
                                    "opp_creada", "valor_plataforma", "valor_ghl", "propuesta",
                                    "decision", "nota", "estado", "resultado", "cerrado_en",
                                    "decidido_en", "creado_en")}


def duplicado_de(nuevo: dict, existentes: Iterable[dict]) -> Optional[dict]:
    """
    Un item del mismo tipo sobre la misma oportunidad (o, sin oportunidad, el mismo
    contacto) que ya existe. Se usa al crear un lote: lo que ya se decidió no se vuelve a
    proponer salvo que se pida con `forzar`.
    """
    fuertes = {k for k in nuevo.get("claves") or [] if k.startswith(("opp:", "contacto:"))}
    debiles = {k for k in nuevo.get("claves") or [] if k.startswith(("tel:", "email:"))}
    for it in existentes:
        if it.get("tipo") != nuevo.get("tipo"):
            continue
        ks = set(it.get("claves") or [])
        if fuertes & ks:
            return it
        # Sin opp ni contacto en ninguno de los dos: vale teléfono o email
        if not fuertes and not any(k.startswith(("opp:", "contacto:")) for k in ks) and debiles & ks:
            return it
    return None


# ── transiciones ─────────────────────────────────────────────────────────────────────
def decidir(it: dict, decision, nota, por: str) -> dict:
    if it.get("estado") == "cerrado":
        raise ValueError("Este registro ya está cerrado: reábrelo antes de cambiar la decisión.")
    d = str(decision or "").strip().upper()
    if d not in DECISIONES:
        raise ValueError("La decisión es SI, NO o DUDA.")
    it.update({"decision": d, "nota": (str(nota).strip() if nota else None) or it.get("nota"),
               "decidido_por": por, "decidido_en": ahora(), "estado": "decidido"})
    return it


def cerrar(it: dict, resultado, por: str) -> dict:
    if it.get("estado") == "cerrado":
        raise ValueError("Ya estaba cerrado.")
    if it.get("estado") != "decidido" and not it.get("informativo"):
        raise ValueError("Falta la decisión: se decide (SI/NO/DUDA) y luego se cierra.")
    it.update({"estado": "cerrado", "cerrado_por": por, "cerrado_en": ahora(),
               "resultado": (str(resultado).strip() if resultado else None)
               or ("Aplicado en el CRM." if it.get("decision") == "SI" else "Cerrado sin cambios.")})
    return it


def reabrir(it: dict, por: str) -> dict:
    it.update({"estado": "decidido" if it.get("decision") else "abierto",
               "cerrado_por": None, "cerrado_en": None, "resultado": None, "informativo": False})
    return it


def resumen_lote(lote: dict, items: list[dict]) -> dict:
    its = [i for i in items if i.get("lote") == lote["id"]]
    n = {"abiertos": 0, "decididos": 0, "cerrados": 0, "informativos": 0}
    subir = 0.0
    for i in its:
        if i.get("informativo"):
            n["informativos"] += 1
        else:
            n[i.get("estado", "abierto") + "s"] += 1
        if i.get("tipo") == "valor" and i.get("decision") == "SI" and i.get("valor_plataforma") is not None:
            subir += max(0.0, (i["valor_plataforma"] or 0) - (i.get("valor_ghl") or 0))
    pendientes = n["abiertos"] + n["decididos"]
    return dict(lote, n=len(its), **n, pendientes=pendientes, efecto_si=round(subir, 2),
                estado="cerrado" if its and pendientes == 0 else lote.get("estado", "abierto"))


# ── la Ficha de cliente ──────────────────────────────────────────────────────────────
# Misma estructura que el template oficial (4 pestañas). Los datos de identidad (nombre,
# locationId, zona horaria, cuentas de anuncios) NO se guardan aquí: vienen de la suite y
# se enseñan en solo lectura. Lo que sí vive aquí es lo que solo la atribución sabe.
FICHA_CAMPOS = ("moneda", "pipeline_nombre", "pipeline_id", "etapa_cierre", "prefijo_campanas",
                "apps_reconciliacion", "criterio_reconciliacion", "cohorte", "ventana_dias",
                "numero_control", "notas")


def normalizar_ficha(crudo: dict) -> dict:
    if not isinstance(crudo, dict):
        raise ValueError("La ficha tiene que ser un objeto.")
    f = {k: (str(crudo.get(k)).strip() if crudo.get(k) not in (None, "") else None) for k in FICHA_CAMPOS}
    if f["criterio_reconciliacion"] and f["criterio_reconciliacion"].lower() not in ("ventas", "pagos"):
        raise ValueError("El criterio de reconciliación es «Ventas» o «Pagos».")
    if f["criterio_reconciliacion"]:
        f["criterio_reconciliacion"] = f["criterio_reconciliacion"].capitalize()
    if f["ventana_dias"]:
        try:
            f["ventana_dias"] = int(float(f["ventana_dias"]))
        except ValueError:
            raise ValueError("La ventana de atribución son días (un número).")

    def tabla(nombre, columnas):
        filas = crudo.get(nombre) or []
        if not isinstance(filas, list):
            raise ValueError(f"`{nombre}` tiene que ser una lista.")
        out = []
        for fila in filas:
            if not isinstance(fila, dict):
                continue
            r = {c: (str(fila.get(c)).strip() if fila.get(c) not in (None, "") else None) for c in columnas}
            if any(r.values()):
                out.append(r)
        return out

    f["numeros"] = tabla("numeros", ("numero", "nombre", "source", "atribuye", "notas"))
    for r in f["numeros"]:
        r["atribuye"] = "SÍ" if str(r.get("atribuye") or "").upper() in ("SÍ", "SI", "TRUE", "1") else "NO"
    f["sources"] = tabla("sources", ("source", "prioridad", "clase", "deteccion", "propuesta", "decision", "si_no"))
    f["pipelines"] = tabla("pipelines", ("pipeline", "oportunidades", "destino", "mapeo"))
    f["actualizado"] = ahora()
    return f


# ── Excel ────────────────────────────────────────────────────────────────────────────
def excel(cliente: dict, lotes: list[dict], items: list[dict], moneda: str = "USD") -> bytes:
    """
    El libro con el formato de la skill de reconciliación: `1 · Resumen`, `2 · Reconciliar
    valor` (columnas obligatorias, APRUEBAS con desplegable SI/NO/DUDA pre-rellenado) y una
    pestaña por bloque. Sirve para mandárselo a los vendedores que no entran en la suite.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.formatting.rule import FormulaRule

    simbolo = "€" if str(moneda).upper() == "EUR" else "$"
    fmt_dinero = f'"{simbolo}"#,##0.00;("{simbolo}"#,##0.00);-'
    F_TIT, F_CAB, F_ALT = "1F2A37", "374151", "EEF2F6"
    C_OK, C_REV, C_NO, C_INFO = "D1FAE5", "FEF3C7", "FEE2E2", "DBEAFE"
    fino = Side(style="thin", color="D4D4D8")
    borde = Border(left=fino, right=fino, top=fino, bottom=fino)

    def relleno(c):
        return PatternFill("solid", fgColor=c)

    def titulo(ws, texto, sub, ncols):
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
        ws["A1"] = texto
        ws["A1"].font = Font(name="Arial", size=12, bold=True, color="FFFFFF")
        ws["A1"].fill = relleno(F_TIT)
        ws["A1"].alignment = Alignment(vertical="center")
        ws.row_dimensions[1].height = 24
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncols)
        ws["A2"] = sub
        ws["A2"].font = Font(name="Arial", size=9, color="374151")
        ws["A2"].alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[2].height = 42

    def cabecera(ws, fila, cols):
        for j, (h, ancho) in enumerate(cols, 1):
            c = ws.cell(row=fila, column=j, value=h)
            c.font = Font(name="Arial", size=9, bold=True, color="FFFFFF")
            c.fill = relleno(F_CAB)
            c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
            c.border = borde
            ws.column_dimensions[get_column_letter(j)].width = ancho
        ws.freeze_panes = ws.cell(row=fila + 1, column=1)

    def celda(ws, fila, col, valor, *, num=None, fill=None, bold=False, wrap=False):
        c = ws.cell(row=fila, column=col, value=valor)
        c.font = Font(name="Arial", size=9, bold=bold)
        c.border = borde
        c.alignment = Alignment(wrap_text=wrap, vertical="top")
        if num:
            c.number_format = num
        if fill:
            c.fill = relleno(fill)
        return c

    def desplegable(ws, col_letra, fila1, fila2):
        dv = DataValidation(type="list", formula1='"SI,NO,DUDA"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{col_letra}{fila1}:{col_letra}{fila2}")
        rango = f"{col_letra}{fila1}:{col_letra}{fila2}"
        ws.conditional_formatting.add(rango, FormulaRule(formula=[f'{col_letra}{fila1}="SI"'], fill=relleno(C_OK)))
        ws.conditional_formatting.add(rango, FormulaRule(formula=[f'{col_letra}{fila1}="NO"'], fill=relleno(C_NO)))
        ws.conditional_formatting.add(rango, FormulaRule(formula=[f'{col_letra}{fila1}="DUDA"'], fill=relleno(C_REV)))

    lotes_por_id = {l["id"]: l for l in lotes}
    nombre = cliente.get("nombre") or cliente.get("slug") or "Cliente"
    wb = Workbook()
    res = wb.active
    res.title = "1 · Resumen"
    for lt in ("A", "B", "C", "D", "E", "F"):
        res.column_dimensions[lt].width = {"A": 34, "B": 16, "C": 14, "D": 14, "E": 14, "F": 18}[lt]
    periodos = sorted({(l.get("periodo_desde") or "", l.get("periodo_hasta") or "") for l in lotes})
    sub = (f"Lotes: {len(lotes)} · Periodos: "
           + (", ".join(f"{a} a {b}".strip(" a") for a, b in periodos) or "—")
           + f" · Criterio: {', '.join(sorted({l.get('criterio') for l in lotes if l.get('criterio')})) or '—'}"
           + ". SI = apruebo la propuesta. Solo se escribe en el CRM lo que vuelva marcado SI. "
           "Decide en la pestaña 2 (y en las de llamadas si las hay); las demás son informativas.")
    titulo(res, f"{nombre} · Reconciliación", sub, 6)

    por_tipo: dict[str, list] = {}
    for it in items:
        por_tipo.setdefault(it.get("tipo") or "otro", []).append(it)

    fila = 4
    cabecera(res, fila, [("Bloque", 34), ("Registros", 16), ("Dinero real", 14), ("Valor en CRM", 14),
                         ("Efecto si SI", 14), ("Pestaña", 18)])
    fila += 1
    primera_resumen = fila
    hojas_creadas = []
    orden = [t for t in TIPOS if t in por_tipo]
    n_hoja = 2
    for t in orden:
        its = por_tipo[t]
        nombre_hoja = f"{n_hoja} · {TITULOS_TIPO[t]}"[:31]
        n_hoja += 1
        ws = wb.create_sheet(nombre_hoja)
        if t == "valor":
            cols = [("Opp creada", 12), ("Cliente (2nda plataforma de Reconciliación)", 30),
                    ("Contacto GHL", 30), ("Valor a Reconciliar Total (2nda plataforma de Reconciliación)", 20),
                    ("Valor a Reconciliar GHL", 16), ("Diferencia en porcentaje", 14), ("APRUEBAS", 11),
                    ("TU NOTA", 36), ("Propuesta", 36), ("Evidencia", 48), ("Estado", 11), ("Decidido por", 22), ("Lote", 22)]
        elif t == "source":
            cols = [("Hora de la llamada", 16), ("Teléfono", 16), ("Campaña", 24), ("Contacto", 28),
                    ("Opp creada", 12), ("Source actual", 16), ("Source propuesto", 16), ("APRUEBAS", 11),
                    ("TU NOTA", 36), ("Propuesta", 36), ("Evidencia", 48), ("Estado", 11), ("Decidido por", 22), ("Lote", 22)]
        elif t == "fuera_periodo":
            cols = [("Cliente", 30), ("Contacto GHL", 30), ("Fecha de alta del contacto", 16), ("Importe", 14),
                    ("Evidencia", 48), ("Lote", 22)]
        else:
            cols = [("Cliente / contacto", 30), ("Teléfono", 16), ("Email", 26), ("Fecha", 12), ("Importe", 14),
                    ("Propuesta", 36), ("Evidencia", 48), ("APRUEBAS", 11), ("TU NOTA", 36), ("Estado", 11), ("Lote", 22)]
        titulo(ws, f"{nombre} · {TITULOS_TIPO[t]}",
               "Cada fila lleva su evidencia al lado. " + ("SI = subir el importe a la suma de la plataforma "
               "(y marcar ganada si no lo está). Marcas pre-rellenadas con la regla del 15 %."
               if t == "valor" else "Informativo salvo que haya propuesta." if t in TIPOS_INFORMATIVOS
               else "SI = apruebo la propuesta de esta fila."), len(cols))
        cabecera(ws, 4, cols)
        r = 5
        col_apr = None
        for i, it in enumerate(its):
            alt = F_ALT if i % 2 else None
            lote_t = (lotes_por_id.get(it.get("lote")) or {}).get("titulo") or it.get("lote")
            estado = it.get("estado")
            dec = it.get("decision") or it.get("propuesta_decision") or ""
            if t == "valor":
                contacto = (it.get("contacto") or "") + (" · open → won" if str(it.get("estado_opp") or "").lower() in ("open", "abierta", "abierto") else "")
                vals = [it.get("opp_creada"), it.get("cliente_plataforma"), contacto, it.get("valor_plataforma"),
                        it.get("valor_ghl"), None, dec, it.get("nota"), it.get("propuesta"), it.get("evidencia"),
                        estado, it.get("decidido_por"), lote_t]
                for j, v in enumerate(vals, 1):
                    celda(ws, r, j, v, fill=alt, wrap=j in (3, 8, 9, 10))
                ws.cell(row=r, column=4).number_format = fmt_dinero
                ws.cell(row=r, column=5).number_format = fmt_dinero
                ws.cell(row=r, column=6).value = f"=IF(D{r}=0,0,(D{r}-E{r})/D{r})"
                ws.cell(row=r, column=6).number_format = "0.0%"
                col_apr = "G"
            elif t == "source":
                vals = [it.get("fecha_evento"), it.get("telefono"), it.get("campana"), it.get("contacto"),
                        it.get("opp_creada"), it.get("source_actual"), it.get("source_propuesto"), dec,
                        it.get("nota"), it.get("propuesta"), it.get("evidencia"), estado, it.get("decidido_por"), lote_t]
                for j, v in enumerate(vals, 1):
                    celda(ws, r, j, v, fill=alt, wrap=j in (9, 10, 11))
                col_apr = "H"
            elif t == "fuera_periodo":
                vals = [it.get("cliente_plataforma") or it.get("contacto"), it.get("contacto"), it.get("contacto_alta"),
                        it.get("valor_plataforma"), it.get("evidencia"), lote_t]
                for j, v in enumerate(vals, 1):
                    celda(ws, r, j, v, fill=C_INFO if not alt else alt, wrap=j == 5)
                ws.cell(row=r, column=4).number_format = fmt_dinero
            else:
                vals = [it.get("cliente_plataforma") or it.get("contacto"), it.get("telefono"), it.get("email"),
                        it.get("fecha_evento") or it.get("opp_creada"), it.get("valor_plataforma"),
                        it.get("propuesta"), it.get("evidencia"), "" if it.get("informativo") else dec,
                        it.get("nota"), estado, lote_t]
                for j, v in enumerate(vals, 1):
                    celda(ws, r, j, v, fill=(C_INFO if it.get("informativo") and not alt else alt), wrap=j in (6, 7, 9))
                ws.cell(row=r, column=5).number_format = fmt_dinero
                if not it.get("informativo"):
                    col_apr = "H"
            r += 1
        ultima = r - 1
        if col_apr and ultima >= 5:
            desplegable(ws, col_apr, 5, ultima)
        # Totales al pie
        if t == "valor" and ultima >= 5:
            celda(ws, r + 1, 3, "Total plataforma", bold=True)
            celda(ws, r + 1, 4, f"=SUM(D5:D{ultima})", num=fmt_dinero, bold=True)
            celda(ws, r + 1, 5, f"=SUM(E5:E{ultima})", num=fmt_dinero, bold=True)
            celda(ws, r + 2, 3, "Aprobado (SI)", bold=True)
            celda(ws, r + 2, 4, f'=SUMIF(G5:G{ultima},"SI",D5:D{ultima})', num=fmt_dinero, bold=True)
        hojas_creadas.append((t, nombre_hoja, ultima))
        # Fila del resumen, por fórmula
        q = nombre_hoja.replace("'", "''")
        celda(res, fila, 1, TITULOS_TIPO[t], bold=True,
              fill=C_OK if t == "valor" else C_REV if t == "source" else C_INFO)
        if ultima >= 5:
            celda(res, fila, 2, f"=COUNTA('{q}'!A5:A{ultima})")
            if t == "valor":
                celda(res, fila, 3, f"=SUM('{q}'!D5:D{ultima})", num=fmt_dinero)
                celda(res, fila, 4, f"=SUM('{q}'!E5:E{ultima})", num=fmt_dinero)
                celda(res, fila, 5, f"=SUMPRODUCT(('{q}'!G5:G{ultima}=\"SI\")*(('{q}'!D5:D{ultima})-('{q}'!E5:E{ultima})))", num=fmt_dinero)
            elif t in ("fuera_periodo",):
                celda(res, fila, 3, f"=SUM('{q}'!D5:D{ultima})", num=fmt_dinero)
            elif t != "source":
                celda(res, fila, 3, f"=SUM('{q}'!E5:E{ultima})", num=fmt_dinero)
        else:
            celda(res, fila, 2, 0)
        celda(res, fila, 6, nombre_hoja)
        fila += 1
    if fila > primera_resumen:
        celda(res, fila, 1, "CUADRE (suma de bloques)", bold=True)
        celda(res, fila, 2, f"=SUM(B{primera_resumen}:B{fila - 1})", bold=True)
        celda(res, fila, 3, f"=SUM(C{primera_resumen}:C{fila - 1})", num=fmt_dinero, bold=True)
        total = sum((l.get("total_export") or 0) for l in lotes if l.get("total_export") is not None)
        if total:
            celda(res, fila + 1, 1, "Total esperado (export)", bold=True)
            celda(res, fila + 1, 3, total, num=fmt_dinero, bold=True)
    fila += 3
    celda(res, fila, 1, "Lotes", bold=True)
    fila += 1
    cabecera(res, fila, [("Lote", 34), ("Periodo", 16), ("Criterio", 14), ("Plataforma", 14), ("Registros", 14), ("Creado", 18)])
    fila += 1
    for l in lotes:
        n = sum(1 for i in items if i.get("lote") == l["id"])
        for j, v in enumerate([l.get("titulo"), f"{l.get('periodo_desde') or ''} a {l.get('periodo_hasta') or ''}".strip(" a"),
                               l.get("criterio"), l.get("plataforma"), n, (l.get("creado_en") or "")[:10]], 1):
            celda(res, fila, j, v)
        fila += 1
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
