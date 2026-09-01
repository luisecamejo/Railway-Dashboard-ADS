#!/usr/bin/env python3
"""
Sube un dashboard.html de una pieza al servicio. El servidor lo parte en visor + app.js
y lo guarda; a partir de ahí TODOS los clientes ven el dashboard nuevo sin redespliegue.

    REPORTES_ADMIN_TOKEN=... python scripts/subir_visor.py dashboard.html \\
        --url https://reportes.up.railway.app
"""
import argparse, json, os, sys, urllib.error, urllib.request

p = argparse.ArgumentParser()
p.add_argument("dashboard")
p.add_argument("--url", default=os.environ.get("REPORTES_URL", "http://127.0.0.1:8077"))
p.add_argument("--token", default=os.environ.get("REPORTES_ADMIN_TOKEN", ""))
a = p.parse_args()
if not a.token:
    sys.exit("Falta el token: exporta REPORTES_ADMIN_TOKEN o pasa --token.")

req = urllib.request.Request(
    a.url.rstrip("/") + "/admin/visor", data=open(a.dashboard, "rb").read(), method="POST",
    headers={"X-Admin-Token": a.token, "Content-Type": "text/html; charset=utf-8"})
try:
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read())
        print(f"✓ visor actualizado · hash {d['hash']} · "
              f"index {d['index_kb']} KB · app.js {d['app_kb']} KB")
except urllib.error.HTTPError as e:
    try:
        det = json.loads(e.read()).get("detail")
    except Exception:
        det = e.reason
    sys.exit(f"✗ no se subió (HTTP {e.code}): {det}")
