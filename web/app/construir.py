# -*- coding: utf-8 -*-
"""
Construye el snapshot del dashboard a partir de datos crudos y una configuración de cliente.

Antes esto era un script con el pipeline, las etapas, los productos, los roles y las
fechas de Aesthetics by Cliff escritos a mano. Aquí nada de eso está en el código:

  · pipelines y etapas          → salen de GoHighLevel (crudo["pipelines"])
  · etapa de cierre, estados    → los deduce el dashboard, no este script
  · productos                   → cfg["productos"], opcional; sin él todo va "Sin clasificar"
  · SOP por etapa               → cfg["sop"], opcional; se resuelve nombre→id y se avisa
  · roles de los vendedores     → cfg["roles"], opcional
  · zona horaria, fechas, ids   → cfg

Reglas que NO se pueden tocar, porque son las que hacen que el reporte cuadre con el CRM:
  · el Source se copia tal cual está en GoHighLevel, sin reinterpretar
  · lead↔campaña solo por utm_campaign_id, y si GHL solo guardó el nombre, por nombre exacto
  · 'fo' (creación de la oportunidad) es el criterio de la ventana; 'f' (alta del contacto)
    puede ser anterior y entonces el lead se marca rec=1
  · las llamadas se atribuyen a quien MARCÓ (userId), no al dueño del lead
"""
from __future__ import annotations

import datetime as dt
from collections import Counter
from zoneinfo import ZoneInfo


# ═════════════════════════════════════════════════════════════════════════════
#  Utilidades
# ═════════════════════════════════════════════════════════════════════════════
def _norm(v) -> str:
    """Minúsculas, sin puntuación ni emojis, espacios colapsados."""
    return " ".join("".join(ch if (ch.isalnum() or ch.isspace()) else " "
                            for ch in str(v).lower()).split())


def _clasificador_producto(reglas):
    """
    reglas: lista de {"palabra": "...", "producto": "..."} en orden de prioridad, o None.

    Se comprueban en orden, así que las más específicas van primero ("total face
    restoration" antes que "face"). Sin reglas, todo cae en "Sin clasificar" — que es el
    comportamiento correcto para un cliente cuya nomenclatura de campañas no controlamos.
    """
    pares = [(_norm(r["palabra"]), r["producto"]) for r in (reglas or [])
             if r.get("palabra") and r.get("producto")]

    def clasificar(nombre):
        t = _norm(nombre)
        if not t:
            return "Sin clasificar"
        for palabra, producto in pares:
            if palabra in t:
                return producto
        return "Sin clasificar"

    return clasificar


# ═════════════════════════════════════════════════════════════════════════════
#  Construcción
# ═════════════════════════════════════════════════════════════════════════════
def construir(cfg: dict, crudo: dict, avisos: list | None = None) -> dict:
    """
    cfg   — configuración del cliente (ver módulo). Obligatorio: nombre, tz, desde, hasta.
    crudo — lo que devolvió la extracción, sin transformar.
    avisos— si se pasa una lista, se rellena con lo que el operador debería saber.
    """
    av = avisos if avisos is not None else []

    for clave in ("nombre", "tz", "desde", "hasta"):
        if not cfg.get(clave):
            raise ValueError(f"La configuración del cliente no trae '{clave}'.")

    TZ = ZoneInfo(cfg["tz"])
    DESDE, HASTA = cfg["desde"], cfg["hasta"]
    AHORA = dt.datetime.now(dt.timezone.utc)

    def d_of(ms):
        return dt.datetime.fromtimestamp(ms / 1000, TZ).date().isoformat()

    # ── pipelines y etapas, con el nombre LITERAL del CRM (emojis incluidos) ──
    pipelines, stages, smap = [], [], {}
    for pi in crudo.get("pipelines") or []:
        pipelines.append({"id": pi["id"], "n": pi["n"]})
        for pos, et in enumerate(pi.get("stages") or []):
            sid, sn = et["id"], et["n"]
            stages.append({"id": sid, "n": sn, "p": pos, "pipe": pi["id"]})
            smap[sid] = (sn, pos)
    if not stages:
        av.append("El CRM no devolvió ninguna etapa: el embudo saldrá vacío.")

    prod_de = _clasificador_producto(cfg.get("productos"))
    tag2prod = {_norm(k): v for k, v in (cfg.get("productosPorTag") or {}).items()}

    # ── campañas con gasto DIARIO tal cual lo reporta cada plataforma ──
    camps = {}

    def addc(cid, nombre, red):
        if cid not in camps:
            camps[cid] = {"id": cid, "nombre": nombre, "red": red, "prod": prod_de(nombre),
                          "spend": 0, "impr": 0, "clicks": 0, "metaLeads": 0, "w": []}
        return camps[cid]

    for fila in crudo.get("gastoDiario") or []:
        c = addc(str(fila["campana_id"]), fila["campana"], fila["red"])
        sp = round(float(fila.get("spend") or 0), 2)
        im = int(fila.get("impressions") or 0)
        cl = int(fila.get("clicks") or 0)
        ld = round(float(fila.get("conversiones") or 0), 2)
        c["spend"] += sp; c["impr"] += im; c["clicks"] += cl; c["metaLeads"] += ld
        # s == e siempre: es la garantía de granularidad diaria que valida el servicio
        c["w"].append({"s": fila["fecha"], "e": fila["fecha"],
                       "sp": sp, "im": im, "cl": cl, "ld": ld})
    for c in camps.values():
        c["spend"] = round(c["spend"], 2)
        c["metaLeads"] = round(c["metaLeads"], 2)
        c["w"].sort(key=lambda w: w["s"])
    cname = {c["nombre"].strip().lower(): cid for cid, c in camps.items()}

    # ── anuncios (totales del rango) + miniatura del creativo ──
    adg = {}
    for r in crudo.get("anunciosDiario") or []:
        a = adg.setdefault(str(r["anuncio_id"]), {
            "id": str(r["anuncio_id"]), "c": str(r["campana_id"]), "n": r["anuncio"],
            "sp": 0, "im": 0, "cl": 0, "ld": 0, "camp": r["campana"],
            "red": r.get("red") or "Meta", "prod": prod_de(r["campana"])})
        a["sp"] += float(r.get("spend") or 0)
        a["im"] += int(r.get("impressions") or 0)
        a["cl"] += int(r.get("clicks") or 0)
        a["ld"] += int(r.get("conversiones") or 0)
    thumbs = crudo.get("miniaturas") or {}
    ads = []
    for a in adg.values():
        a["sp"] = round(a["sp"], 2)
        nl = _norm(a["n"])
        a["tipo"] = "Video" if "video" in nl else ("Imagen" if "imag" in nl else "Otro")
        # URL firmada del CDN de Meta: se puede empotrar pero CADUCA. El dashboard ya
        # muestra un aviso cuando no carga; regenerar el snapshot las refresca.
        a["pv"] = thumbs.get(str(a["id"]), "")
        ads.append(a)
    ads.sort(key=lambda a: -a["sp"])

    # ── leads: una oportunidad de GHL por fila, el Source tal cual ──
    leads = []
    for r in crudo.get("oportunidades") or []:
        created = r["created"]
        cc = r.get("cc") or created
        fo, f = d_of(created), d_of(cc)
        stg = smap.get(r.get("stage"), ("—", 99))

        lEq = bool(r.get("lEq"))
        fCid = str(r.get("fCid") or "")
        lCid = fCid if lEq else str(r.get("lCid") or "")
        fNam = r.get("fCname") or ""
        lNam = fNam if lEq else (r.get("lCname") or "")
        fCon = r.get("fCon") or ""
        lCon = fCon if lEq else (r.get("lCon") or "")
        sess = r.get("fSess") or ""
        # sesión del ÚLTIMO contacto: si lEq, es la misma que la primera. Si no hay campos
        # l*, GHL no guardó atribución de último contacto — que no es lo mismo que "es igual".
        sessL = sess if lEq else (r.get("lSess") or "")

        c = cn = m = crit = ""
        plat = "Otros"
        hit = fCid if fCid in camps else (lCid if lCid in camps else "")
        nameHit = cname.get(fNam.strip().lower()) or cname.get(lNam.strip().lower()) or ""
        # El código del criterio lleva la inicial de la plataforma (M de Meta, G de Google,
        # T de TikTok…). Sale del nombre de la red, no de una lista escrita a mano.
        if hit:
            c = hit; cn = camps[c]["nombre"]; plat = camps[c]["red"]; m = "id"
            crit = f"{plat[:1].upper()}1 · utm_campaign_id de {plat}"
        elif nameHit:
            c = nameHit; cn = camps[c]["nombre"]; plat = camps[c]["red"]; m = "nombre"
            crit = (f"{plat[:1].upper()}2 · nombre de campaña ({plat}), "
                    f"la atribución no guardó el id")
        elif fCid or lCid:
            cn = fNam or lNam
            crit = ("X1 · trae utm_campaign_id pero no coincide con ninguna campaña "
                    "de las cuentas conectadas")
        elif sess:
            crit = f"O3 · sesión {sess}"
        else:
            crit = "O4 · sin señal de atribución"

        prod = prod_de(cn) if cn else "Sin clasificar"
        if prod == "Sin clasificar" and tag2prod:
            for t in r.get("tags") or []:
                if _norm(t) in tag2prod:
                    prod = tag2prod[_norm(t)]
                    break

        stageAt = r.get("stageAt") or created
        statusAt = r.get("statusAt") or created
        st = r.get("st", "open")
        leads.append({
            "id": r["oid"], "n": r.get("n") or "—", "f": f, "fo": fo,
            "src": (r.get("src") or "").strip(), "plat": plat, "st": st,
            "v": round(float(r.get("val") or 0), 2),
            "e": stg[0], "ep": stg[1], "ei": r.get("stage") or "",
            "c": c, "cn": cn, "p": prod, "m": m, "crit": crit, "con": fCon or lCon,
            # rec=1 = contacto anterior a la ventana con oportunidad dentro. El validador
            # del servicio exige esta marca, y el dashboard los excluye por defecto del CPL.
            "rec": 1 if f < DESDE else 0,
            "cl": d_of(statusAt) if st == "won" else "",
            "d": round((stageAt - created) / 86400000, 1),
            "de": round((AHORA.timestamp() * 1000 - stageAt) / 86400000, 1),
            "sess": sess, "sl": sessL, "pipe": r.get("pipe") or "",
        })
    leads.sort(key=lambda x: x["fo"], reverse=True)

    fuera = [x["fo"] for x in leads if not (DESDE <= x["fo"] <= HASTA)]
    if fuera:
        av.append(f"{len(fuera)} oportunidades quedaron fuera de la ventana declarada "
                  f"(ej. {fuera[0]}). El servicio rechazará el snapshot.")

    # ── vendedores ──
    users = crudo.get("usuarios") or {}
    def uname(i):
        return (users.get(i) or {}).get("n", "") if i else ""

    roles = {_norm(k): v for k, v in (cfg.get("roles") or {}).items()}
    sprows = []
    for r in crudo.get("vendedores") or []:
        num = r.get("t0to") or ""
        if not num.startswith("+"):
            num = ""
        sprows.append({
            "n": r["n"], "asg": uname(r.get("asg")) or "—",
            "tipo": "Formulario" if r.get("tipo") == "formulario" else "Entrante",
            "rtH": r.get("rtHum"), "rtA": r.get("rtAut"),
            "ciA": r.get("ciAtt", 0), "ciP": r.get("ciPerd", 0),
            "coC": r.get("coCon", 0), "coS": r.get("coSin", 0),
            "mIn": r.get("msgIn", 0), "mOut": r.get("msgOut", 0),
            "by": uname(r.get("humBy")), "num": num,
            "st": r.get("oppStatus") or "",
            "val": round(float(r.get("oppValue") or 0), 2) if r.get("oppStatus") == "won" else 0,
        })
    usados = sorted({x["asg"] for x in sprows if x["asg"] != "—"} |
                    {x["by"] for x in sprows if x["by"]})
    data_users = [{"n": u, "r": roles.get(_norm(u), "Usuario")} for u in usados]

    # ── SOP: nombre de etapa → id, resuelto AQUÍ para que el dashboard solo use ids ──
    sop = cfg.get("sop") or {}
    sop_id, sin_casar = {}, []
    for clave, valor in sop.items():
        nk = _norm(clave)
        casa = [sid for sid, (sn, _p) in smap.items() if _norm(sn) == nk]
        if not casa:
            casa = [sid for sid, (sn, _p) in smap.items() if _norm(sn).startswith(nk)]
        if len(casa) == 1:
            sop_id[casa[0]] = valor
        else:
            sin_casar.append(clave)
    if sin_casar:
        av.append(f"{len(sin_casar)} claves del SOP no casan con ninguna etapa de este "
                  f"pipeline: {', '.join(sin_casar[:5])}. Esas etapas van sin semáforo.")

    # ── llamadas, atribuidas a QUIEN MARCÓ ──
    cw = crudo.get("ventanaLlamadas") or {}
    calls = []
    for r in crudo.get("llamadas") or []:
        t = dt.datetime.fromisoformat(str(r["ts"]).replace("Z", "+00:00")).astimezone(TZ)
        fecha = t.date().isoformat()
        if cw.get("desde") and not (cw["desde"] <= fecha <= cw["hasta"]):
            continue
        calls.append({"u": r.get("userId") or "", "c": r.get("contactId"),
                      "d": 1 if r.get("dir") == "outbound" else 0,
                      "s": r.get("status") or "sin estado",
                      "dur": r.get("dur"), "t": int(t.timestamp() * 1000)})
    # Orden determinista: el snapshot no debe depender de en qué orden respondió la
    # API. Sin esto, dos extracciones de los mismos datos producen ficheros distintos
    # y comparar dos snapshots muestra ruido en vez de cambios reales.
    calls.sort(key=lambda c: (c["t"], str(c["c"]), c["d"], str(c["dur"])))
    if calls and cw.get("desde") and (cw["desde"] > DESDE or cw["hasta"] < HASTA):
        av.append(f"La ventana de llamadas ({cw['desde']} → {cw['hasta']}) es más corta que "
                  f"la del reporte ({DESDE} → {HASTA}). Fuera de ella el Call Report sale vacío.")

    data = {
        "generado": AHORA.strftime("%Y-%m-%dT%H:%MZ"),
        "desde": DESDE, "hasta": HASTA,
        "cliente": {"nombre": cfg["nombre"], "ghlLocationId": cfg.get("ghlLocationId", ""),
                    "tz": cfg["tz"], "tzFuente": cfg.get("tzFuente", "")},
        "cuentas": cfg.get("cuentas") or [],
        "granularidadGasto": "dia",
        "pipelines": pipelines,
        "leads": leads,
        "camps": sorted(camps.values(), key=lambda c: -c["spend"]),
        "stages": stages,
        "ads": ads,
        "sp": {"desde": (crudo.get("ventanaVendedores") or {}).get("desde", DESDE),
               "hasta": (crudo.get("ventanaVendedores") or {}).get("hasta", HASTA),
               "rows": sprows},
        "sop": sop,
        "sopById": sop_id,
        "users": data_users,
        "usuarios": {k: v.get("n", "") for k, v in users.items()},
        "calls": calls,
        "callWin": {"desde": cw.get("desde"), "hasta": cw.get("hasta"),
                    "nota": "Llamada por llamada desde la API, atribuidas a quien marcó "
                            "(userId). Ventana en la zona del negocio."},
    }
    return data


def resumen(data: dict) -> str:
    """Una línea por cosa que conviene mirar antes de publicar."""
    L = data["leads"]
    camps = data["camps"]
    lineas = [
        f"leads {len(L)} · recurrentes {sum(1 for x in L if x.get('rec'))}",
        f"gasto {round(sum(c['spend'] for c in camps), 2)} en {len(camps)} campañas",
        f"match {dict(Counter(x['m'] or 'sin' for x in L))}",
        f"plataformas {dict(Counter(x['plat'] for x in L))}",
        f"ganadas {sum(1 for x in L if x['st'] == 'won')} · "
        f"ingreso {round(sum(x['v'] for x in L if x['st'] == 'won'), 2)}",
        f"anuncios {len(data['ads'])} · con miniatura {sum(1 for a in data['ads'] if a['pv'])}",
        f"pipelines {dict(Counter(x['pipe'] for x in L))}",
        f"llamadas {len(data['calls'])} · salientes {sum(1 for c in data['calls'] if c['d'] == 1)}"
        f" · sin duración {sum(1 for c in data['calls'] if c['dur'] is None)}",
        f"etapas {len(data['stages'])} · SOP casado {len(data['sopById'])}",
    ]
    return "\n".join("  " + x for x in lineas)
