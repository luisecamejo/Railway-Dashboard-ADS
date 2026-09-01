# -*- coding: utf-8 -*-
"""
Prueba de los extractores SIN credenciales ni red.

Levanta un graph.facebook.com y un ghl-mcp de mentira que devuelven las formas de
respuesta REALES (verificadas contra las APIs de verdad) y comprueba que lo que
produce cada extractor es exactamente el trozo crudo que generó el snapshot ya
validado.

    python pruebas/test_extractores.py                      # solo protocolo y ventana
    python pruebas/test_extractores.py --crudos CARPETA      # además, comparación completa

La carpeta de --crudos debe tener crudo_ghl.json / crudo_meta.json, que NO están en el
repositorio porque llevan datos de pacientes.
"""
import argparse, datetime as dt, json, logging, os, pathlib, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
# igual que en el contenedor: la raíz de los extractores en el path
sys.path.insert(0, str(RAIZ / "extractores"))
logging.disable(logging.INFO)

from comun.fechas import dias_entre, ventana          # noqa: E402
from comun.mcp import ClienteMCP, _leer_sse           # noqa: E402

fallos = []


def ok(cond, etiqueta, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + etiqueta + (f"  {extra}" if extra else ""))
    if not cond:
        fallos.append(etiqueta)


# ══════════════════════════════════════════════════════════════════════════════
#  1 · La ventana: de aquí salen todos los descuadres con el CRM
# ══════════════════════════════════════════════════════════════════════════════
def prueba_ventana():
    print("\n== ventana de fechas ==")
    d, h = ventana("America/Denver", hoy=dt.date(2026, 8, 31))
    ok((d, h) == ("2026-05-03", "2026-08-30"),
       "120 días terminando en el último día COMPLETO", f"{d} → {h}")
    ok(len(dias_entre(d, h)) == 120, "son 120 días exactos")
    d30, h30 = ventana("America/Denver", dias=30, hoy=dt.date(2026, 8, 31))
    ok((d30, h30) == ("2026-08-01", "2026-08-30"), "ventana corta de 30 días",
       f"{d30} → {h30}")
    # El día en curso NUNCA entra: en el CRM sigue acumulando.
    ok(h < "2026-08-31", "el día de hoy queda fuera")
    # La zona horaria manda, no el reloj del contenedor (que va en UTC).
    dt_tokio = ventana("Asia/Tokyo", hoy=dt.date(2026, 8, 31))
    ok(dt_tokio == ("2026-05-03", "2026-08-30"), "la ventana se calcula en la zona dada")


# ══════════════════════════════════════════════════════════════════════════════
#  2 · El protocolo MCP, tal y como lo exige el SDK que usa ghl-mcp
# ══════════════════════════════════════════════════════════════════════════════
def prueba_protocolo():
    print("\n== protocolo MCP ==")
    r = _leer_sse(b'event: ping\ndata: {"jsonrpc":"2.0","method":"n/x"}\n\n'
                  b'event: message\ndata: {"jsonrpc":"2.0","id":1,'
                  b'"result":{"content":[{"type":"text","text":"{\\"a\\":1}"}]}}\n\n')
    ok(r["result"]["content"][0]["text"] == '{"a":1}',
       "se lee el bloque útil de una respuesta SSE con varios bloques")
    r2 = _leer_sse(b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}')
    ok(r2["result"]["ok"] is True, "también se acepta JSON pelado, por si cambia")
    try:
        _leer_sse(b"no soy json")
        ok(False, "una respuesta ilegible tiene que levantar error")
    except RuntimeError:
        ok(True, "una respuesta ilegible levanta error en vez de devolver None")
    try:
        ClienteMCP("http://x", "")
        ok(False, "sin token tiene que fallar")
    except ValueError:
        ok(True, "sin token falla al construir, no a mitad de la extracción")


# ══════════════════════════════════════════════════════════════════════════════
#  3 · Meta y GHL contra servidores de mentira con las formas reales
# ══════════════════════════════════════════════════════════════════════════════
def servidor(handler, puerto):
    s = HTTPServer(("127.0.0.1", puerto), handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def prueba_meta(crudos: pathlib.Path | None):
    print("\n== extractor de Meta ==")
    esperado = (json.loads((crudos / "crudo_meta.json").read_text(encoding="utf-8"))
                if crudos and (crudos / "crudo_meta.json").exists() else None)
    if not esperado:
        print("  · saltada: no se pasó --crudos con crudo_meta.json")
        return
    gasto = esperado["gastoDiario"]
    anuncios = esperado["anunciosDiario"]
    thumbs = esperado["miniaturas"]
    PAG = 250

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            import urllib.parse as up
            u = up.urlparse(self.path)
            q = {k: v[0] for k, v in up.parse_qs(u.query).items()}
            desp = int(q.get("__desp", 0))
            base = f"http://127.0.0.1:8891{u.path}"

            def pagina(filas, extra):
                trozo = filas[desp:desp + PAG]
                out = {"data": trozo}
                if desp + PAG < len(filas):
                    out["paging"] = {"next": base + "?" + up.urlencode(
                        {**extra, "__desp": desp + PAG})}
                return out

            if u.path.endswith("/insights"):
                tr = json.loads(q.get("time_range", "{}"))
                d, h = tr.get("since"), tr.get("until")
                # La API DEVUELVE NÚMEROS COMO TEXTO. Si el extractor no los convierte,
                # el gasto se concatena en vez de sumarse.
                if q.get("level") == "campaign":
                    filas = [{"date_start": r["fecha"], "date_stop": r["fecha"],
                              "campaign_id": r["campana_id"], "campaign_name": r["campana"],
                              "spend": str(r["spend"]), "impressions": str(r["impressions"]),
                              "clicks": str(r["clicks"]),
                              "actions": [
                                  {"action_type": "post_engagement", "value": "99"},
                                  {"action_type": "lead", "value": str(r["conversiones"])},
                                  # el MISMO evento repetido con otro nombre: si el
                                  # extractor sumara los dos, contaría doble
                                  {"action_type": "offsite_conversion.fb_pixel_lead",
                                   "value": str(r["conversiones"])}]}
                             for r in gasto if d <= r["fecha"] <= h]
                else:
                    filas = [{"date_start": r["fecha"], "date_stop": r["fecha"],
                              "ad_id": r["anuncio_id"], "ad_name": r["anuncio"],
                              "campaign_id": r["campana_id"], "campaign_name": r["campana"],
                              "spend": str(r["spend"]), "impressions": str(r["impressions"]),
                              "clicks": str(r["clicks"]),
                              "actions": [{"action_type": "lead",
                                           "value": str(r["conversiones"])}]}
                             for r in anuncios if d <= r["fecha"] <= h]
                cuerpo = pagina(filas, {k: q[k] for k in
                                        ("level", "time_range", "fields", "time_increment")
                                        if k in q})
            elif u.path.endswith("/ads"):
                filas = [{"id": k, "name": "x", "creative": {"thumbnail_url": v}}
                         for k, v in thumbs.items()]
                cuerpo = pagina(filas, {"fields": q.get("fields", "")})
            else:
                cuerpo = {"name": "Cuenta de prueba", "timezone_name": "America/Denver",
                          "currency": "USD", "account_status": 1}
            d_ = json.dumps(cuerpo).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(d_)))
            self.end_headers()
            self.wfile.write(d_)

    s = servidor(H, 8891)
    try:
        import meta.extraer as M
        M.BASE = "http://127.0.0.1:8891/v26.0"
        real = M.ventana
        M.ventana = lambda tz, dias=120, hoy=None: ("2026-05-03", "2026-08-30")
        try:
            got = M.extraer_cliente({"slug": "x", "tz": "America/Denver",
                                     "cuentas": [{"plataforma": "Meta", "id": "123"}]},
                                    "token-falso")
        finally:
            M.ventana = real
    finally:
        s.shutdown()

    k = lambda f: (f["fecha"], f["campana_id"])
    campos = ("fecha", "campana_id", "campana", "red", "spend", "impressions",
              "clicks", "conversiones")
    a = [{c: f.get(c) for c in campos} for f in sorted(gasto, key=k)]
    b = [{c: f.get(c) for c in campos} for f in sorted(got["gastoDiario"], key=k)]
    ok(a == b, "gasto diario idéntico al trozo verificado", f"{len(a)} filas")
    ok(round(sum(f["spend"] for f in b), 2) == round(sum(f["spend"] for f in a), 2),
       "el gasto suma lo mismo (los números venían como texto)")
    ok(sum(f["conversiones"] for f in b) == sum(f["conversiones"] for f in a),
       "las conversiones no se cuentan dos veces")
    ok(got["miniaturas"] == thumbs, "miniaturas idénticas", f"{len(thumbs)}")
    ok(len(got["anunciosDiario"]) == len(anuncios), "mismo número de filas de anuncio",
       f"{len(anuncios)}")


def prueba_ghl(crudos: pathlib.Path | None):
    print("\n== extractor de GoHighLevel ==")
    ruta = (crudos / "crudo_ghl.json") if crudos else None
    if not (ruta and ruta.exists()):
        print("  · saltada: no se pasó --crudos con crudo_ghl.json")
        return
    fx = json.loads(ruta.read_text(encoding="utf-8"))
    opps, vend, calls = fx["oportunidades"], fx["vendedores"], fx["llamadas"]
    por_valor = {}
    for o in opps:
        por_valor.setdefault(round(float(o.get("val") or 0), 2), o["oid"])
    pipes = [{"id": p["id"], "name": p["n"],
              "stages": [{"id": s["id"], "name": s["n"], "position": j}
                         for j, s in enumerate(p["stages"])]} for p in fx["pipelines"]]
    vend_crudo, conv_de = [], {}
    for i, v in enumerate(vend):
        conv = f"conv{i:04d}"
        conv_de[conv] = v
        r = {"cid": f"c{i}", "n": v["n"], "asg": v["asg"], "conv": conv,
             "tipo": v["tipo"], "rtHum": v["rtHum"], "rtAut": v["rtAut"],
             "msgIn": v["msgIn"], "msgOut": v["msgOut"]}
        for kk in ("ciAtt", "ciPerd", "coCon", "coSin"):
            if v[kk]:
                r[kk] = v[kk]          # las claves en cero se omiten, como la API real
        if v["humBy"]:
            r["humBy"] = v["humBy"]
        if v["t0to"]:
            r["t0to"] = v["t0to"]
        if v["oppStatus"]:
            r["opp"] = {"id": por_valor.get(round(float(v["oppValue"]), 2)) or f"o{i}",
                        "status": v["oppStatus"]}
        vend_crudo.append(r)
    con_llam = [c for c, v in conv_de.items()
                if any(v[k] for k in ("coCon", "coSin", "ciAtt", "ciPerd"))]
    por_conv = {c: [] for c in con_llam}
    for i, c in enumerate(calls):
        por_conv[con_llam[i % len(con_llam)]].append(c)
    usuarios = fx["usuarios"]

    def ndjson(meta, filas):
        return "\n".join([json.dumps({"meta": meta})] + [json.dumps(f) for f in filas])

    def responder(nombre, args):
        if nombre == "ghl_list_pipelines":
            return {"pipelines": pipes}
        if nombre == "ghl_export_opportunities_compact":
            lim = args.get("limit", 500)
            off = (args.get("cursor") or {}).get("startAfter", 0)
            trozo, hay = opps[off:off + lim], off + lim < len(opps)
            return ndjson({"total": len(opps), "enRango": len(opps), "hasMore": hay,
                           "cursor": {"startAfter": off + lim, "startAfterId": "x"}
                           if hay else None}, trozo)
        if nombre == "ghl_export_seller_performance":
            lim = args.get("limit", 100)
            off = (args.get("cursor") or {}).get("offset", 0)
            trozo, hay = vend_crudo[off:off + lim], off + lim < len(vend_crudo)
            return ndjson({"total": 1986, "enRango": len(vend_crudo), "hasMore": hay,
                           "cursor": {"offset": off + lim} if hay else None}, trozo)
        if nombre == "ghl_api_get":
            path = args.get("path", "")
            if path.startswith("/users"):
                return {"users": [{"id": k, "name": v["n"]} for k, v in usuarios.items()]}
            conv = path.split("/")[2] if path.count("/") >= 2 else ""
            return {"messages": {"nextPage": False, "messages": [
                {"userId": c["userId"], "contactId": c["contactId"],
                 "direction": c["dir"], "status": c["status"], "dateAdded": c["ts"],
                 "meta": {"call": {"duration": c["dur"], "status": c["status"]}}}
                for c in por_conv.get(conv, [])]}}
        raise KeyError(nombre)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _sse(self, obj, code=200):
            d = f"event: message\ndata: {json.dumps(obj)}\n\n".encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(d)))
            self.end_headers()
            self.wfile.write(d)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
            acc = self.headers.get("Accept") or ""
            # El SDK real exige LAS DOS. Se replica para que la prueba lo garantice.
            if "application/json" not in acc or "text/event-stream" not in acc:
                self._sse({"jsonrpc": "2.0", "id": None,
                           "error": {"code": -32000, "message": "Not Acceptable"}}, 406)
                return
            if self.headers.get("Authorization") != "Bearer secreto":
                self._sse({"jsonrpc": "2.0", "id": None,
                           "error": {"code": -32001, "message": "No autorizado"}}, 401)
                return
            p = req.get("params") or {}
            if req.get("method") == "tools/list":
                self._sse({"jsonrpc": "2.0", "id": req.get("id"),
                           "result": {"tools": [{"name": "ghl_api_get"}]}})
                return
            try:
                res = responder(p.get("name"), p.get("arguments") or {})
            except KeyError as e:
                self._sse({"jsonrpc": "2.0", "id": req.get("id"),
                           "result": {"isError": True,
                                      "content": [{"type": "text",
                                                   "text": f"Tool {e} not found"}]}})
                return
            texto = res if isinstance(res, str) else json.dumps(res)
            self._sse({"jsonrpc": "2.0", "id": req.get("id"),
                       "result": {"content": [{"type": "text", "text": texto}]}})

    s = servidor(H, 8892)
    try:
        os.environ["GHL_PAUSA_LLAMADAS"] = "0"
        import ghl.extraer as G
        G.PAUSA = 0
        real = G.ventana
        G.ventana = lambda tz, dias=120, hoy=None: ventana(tz, dias, hoy=dt.date(2026, 8, 31))
        mcp = ClienteMCP("http://127.0.0.1:8892", "secreto")
        try:
            got = G.extraer_cliente({"slug": "x", "tz": "America/Denver",
                                     "config": {"ghlLocationId": "LOC",
                                                "tz": "America/Denver"}}, mcp)
        finally:
            G.ventana = real
        # un token que no vale tiene que fallar, no devolver vacío
        try:
            ClienteMCP("http://127.0.0.1:8892", "malo").llamar("ghl_api_get",
                                                               {"client": "x", "path": "/users/"})
            ok(False, "un token inválido debe ser rechazado")
        except RuntimeError:
            ok(True, "un token inválido es rechazado")
    finally:
        s.shutdown()

    ok(got["pipelines"] == fx["pipelines"], "pipelines y etapas idénticos, en orden")
    ko = lambda o: o["oid"]
    ok(sorted(got["oportunidades"], key=ko) == sorted(opps, key=ko),
       "oportunidades idénticas (paginación por cursor)", f"{len(opps)}")
    ok(got["usuarios"] == fx["usuarios"], "usuarios idénticos")
    kv = lambda v: (v["n"], v["msgIn"], v["msgOut"])
    ok(sorted(got["vendedores"], key=kv) == sorted(vend, key=kv),
       "vendedores idénticos, con el importe cruzado desde las oportunidades",
       f"{len(vend)}")
    # 'conv' no lo consume construir.py y en la prueba es inventado
    limpia = lambda c: {k: v for k, v in c.items() if k != "conv"}
    kc = lambda c: (c["ts"], str(c["contactId"]), str(c["dur"]), c["dir"])
    a = sorted((limpia(c) for c in calls), key=kc)
    b = sorted((limpia(c) for c in got["llamadas"]), key=kc)
    # el status nulo se normaliza en el extractor; construir.py hacía lo mismo
    norm = lambda f: [{**x, "status": x.get("status") or "sin estado"} for x in f]
    ok(norm(a) == norm(b), "llamadas idénticas", f"{len(a)}")
    ok(got["ventanaLlamadas"]["desde"] == "2026-05-03",
       "la ventana de llamadas ya cubre los 120 días (cierra H-3)",
       f"{got['ventanaLlamadas']}")
    ok(got["_meta"]["conversacionesConLlamadas"] < len(vend),
       "solo se piden mensajes a las conversaciones que tienen llamadas",
       f"{got['_meta']['conversacionesConLlamadas']} de {len(vend)}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--crudos", help="carpeta con crudo_ghl.json y crudo_meta.json")
    a = p.parse_args()
    crudos = pathlib.Path(a.crudos) if a.crudos else None
    prueba_ventana()
    prueba_protocolo()
    prueba_meta(crudos)
    prueba_ghl(crudos)
    print("\n" + (f"FALLOS: {fallos}" if fallos else "todo OK"))
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
