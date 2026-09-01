#!/usr/bin/env python3
"""
Parte un dashboard.html en visor + app.js + snapshot, en local. Útil para inspeccionar
el resultado sin pasar por el servicio; en producción se usa scripts/subir_visor.py.

    python scripts/partir_visor.py dashboard.html --salida /tmp/visor --snapshot /tmp/s.json
"""
import argparse, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "web"))
from app.visor import partir_html  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("dashboard")
p.add_argument("--salida", default="/tmp/visor")
p.add_argument("--snapshot", default=None)
a = p.parse_args()

r = partir_html(pathlib.Path(a.dashboard).read_text(encoding="utf-8"))
d = pathlib.Path(a.salida); d.mkdir(parents=True, exist_ok=True)
(d / "index.html").write_text(r["index"], encoding="utf-8")
(d / "app.js").write_text(r["app"], encoding="utf-8")
print(f"visor    {a.salida}/index.html  {len(r['index'])/1024:.0f} KB")
print(f"app.js   {a.salida}/app.js      {len(r['app'])/1024:.0f} KB  · hash {r['hash']}")
if a.snapshot:
    pathlib.Path(a.snapshot).write_text(
        json.dumps(r["datos"], ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"snapshot {a.snapshot}  {len(r['datos']['leads'])} oportunidades")
