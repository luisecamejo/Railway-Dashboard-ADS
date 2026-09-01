#!/usr/bin/env python3
"""
Publica un snapshot en el servicio de reportes.

    python scripts/publicar.py aesthetics-by-cliff snapshot.json
    python scripts/publicar.py aesthetics-by-cliff snapshot.json --url https://...up.railway.app

Lee el token de administración de la variable de entorno REPORTES_ADMIN_TOKEN y la URL
de REPORTES_URL. Si el snapshot no pasa las comprobaciones del servidor, imprime
exactamente qué falló y sale con código 1 — así un despliegue automático se para.
"""
import argparse, json, os, sys, urllib.error, urllib.request


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cliente")
    p.add_argument("snapshot")
    p.add_argument("--url", default=os.environ.get("REPORTES_URL", "http://127.0.0.1:8077"))
    p.add_argument("--token", default=os.environ.get("REPORTES_ADMIN_TOKEN", ""))
    a = p.parse_args()

    if not a.token:
        sys.exit("Falta el token: exporta REPORTES_ADMIN_TOKEN o pasa --token.")

    crudo = open(a.snapshot, "rb").read()
    req = urllib.request.Request(f"{a.url.rstrip('/')}/admin/snapshots/{a.cliente}",
                                 data=crudo, method="POST",
                                 headers={"X-Admin-Token": a.token,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.loads(r.read())
            print(f"✓ publicado · {d.get('n_leads')} oportunidades · "
                  f"{int(d.get('bytes', 0)) / 1024:.0f} KB · {d.get('generado')}")
            return
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read())
        except Exception:
            sys.exit(f"✗ HTTP {e.code}: {e.reason}")
        det = d.get("detail", d)
        print(f"✗ el snapshot NO se publicó (HTTP {e.code})", file=sys.stderr)
        if isinstance(det, dict) and det.get("problemas"):
            for pr in det["problemas"]:
                print(f"  · {pr}", file=sys.stderr)
        else:
            print(f"  {det}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
