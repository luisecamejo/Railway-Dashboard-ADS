"""
Almacén de clientes, snapshots, enlaces y visor.

Dos implementaciones con la misma interfaz:
  · Postgres  — se activa sola si existe DATABASE_URL.
  · Ficheros  — para desarrollo, pruebas y despliegues con volumen, sin base de datos.

Se elige en tiempo de arranque; el resto del servicio no sabe cuál está usando.
"""
from __future__ import annotations

import json
import os
import pathlib
import secrets
import threading
from datetime import datetime, timezone
from typing import Optional


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def nuevo_token() -> str:
    """32 caracteres seguros. Suficiente para que no se adivine por fuerza bruta."""
    return secrets.token_urlsafe(24)


# ══════════════════════════════════════════════════════════════════════════════
#  Postgres
# ══════════════════════════════════════════════════════════════════════════════
ESQUEMA = """
CREATE TABLE IF NOT EXISTS clientes (
  slug              TEXT PRIMARY KEY,
  nombre            TEXT NOT NULL,
  ghl_location_id   TEXT,
  tz                TEXT,
  fuentes           JSONB NOT NULL DEFAULT '["crm"]'::jsonb,
  activo            BOOLEAN NOT NULL DEFAULT TRUE,
  creado            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS snapshots (
  id        BIGSERIAL PRIMARY KEY,
  cliente   TEXT NOT NULL REFERENCES clientes(slug) ON DELETE CASCADE,
  datos     JSONB NOT NULL,
  generado  TIMESTAMPTZ NOT NULL DEFAULT now(),
  desde     DATE,
  hasta     DATE,
  n_leads   INTEGER,
  bytes     INTEGER
);
CREATE INDEX IF NOT EXISTS ix_snapshots_cliente ON snapshots (cliente, generado DESC);

CREATE TABLE IF NOT EXISTS enlaces (
  token          TEXT PRIMARY KEY,
  cliente        TEXT NOT NULL REFERENCES clientes(slug) ON DELETE CASCADE,
  modo           TEXT NOT NULL DEFAULT 'cliente',
  nota           TEXT,
  caduca         TIMESTAMPTZ,
  revocado       BOOLEAN NOT NULL DEFAULT FALSE,
  creado         TIMESTAMPTZ NOT NULL DEFAULT now(),
  accesos        INTEGER NOT NULL DEFAULT 0,
  ultimo_acceso  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_enlaces_cliente ON enlaces (cliente);

CREATE TABLE IF NOT EXISTS visor (
  id         INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  index_html TEXT NOT NULL,
  app_js     TEXT NOT NULL,
  hash       TEXT NOT NULL,
  subido     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS extracciones (
  id         BIGSERIAL PRIMARY KEY,
  cliente    TEXT NOT NULL,
  inicio     TIMESTAMPTZ NOT NULL DEFAULT now(),
  fin        TIMESTAMPTZ,
  estado     TEXT NOT NULL DEFAULT 'en_curso',
  modo       TEXT,
  llamadas   INTEGER,
  detalle    TEXT
);
"""


class AlmacenPostgres:
    tipo = "postgres"

    def __init__(self, dsn: str):
        from psycopg_pool import ConnectionPool
        # Railway entrega postgres://; psycopg quiere postgresql://
        if dsn.startswith("postgres://"):
            dsn = "postgresql://" + dsn[len("postgres://"):]
        self.pool = ConnectionPool(dsn, min_size=1, max_size=5, open=True,
                                   kwargs={"autocommit": True})
        with self.pool.connection() as c:
            c.execute(ESQUEMA)

    # ── clientes ──────────────────────────────────────────────────────────
    def guardar_cliente(self, slug, nombre, ghl_location_id=None, tz=None, fuentes=None):
        with self.pool.connection() as c:
            c.execute(
                """INSERT INTO clientes (slug,nombre,ghl_location_id,tz,fuentes)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (slug) DO UPDATE SET
                     nombre=EXCLUDED.nombre,
                     ghl_location_id=COALESCE(EXCLUDED.ghl_location_id, clientes.ghl_location_id),
                     tz=COALESCE(EXCLUDED.tz, clientes.tz),
                     fuentes=EXCLUDED.fuentes""",
                (slug, nombre, ghl_location_id, tz, json.dumps(fuentes or ["crm"])))
        return self.cliente(slug)

    def cliente(self, slug) -> Optional[dict]:
        with self.pool.connection() as c:
            r = c.execute("SELECT slug,nombre,ghl_location_id,tz,fuentes,activo,creado "
                          "FROM clientes WHERE slug=%s", (slug,)).fetchone()
        if not r:
            return None
        return dict(zip(("slug", "nombre", "ghl_location_id", "tz", "fuentes", "activo", "creado"), r))

    def clientes(self) -> list[dict]:
        with self.pool.connection() as c:
            rs = c.execute("""SELECT c.slug,c.nombre,c.activo,
                                (SELECT max(generado) FROM snapshots s WHERE s.cliente=c.slug),
                                (SELECT count(*) FROM enlaces e WHERE e.cliente=c.slug AND NOT e.revocado)
                              FROM clientes c ORDER BY c.nombre""").fetchall()
        return [dict(zip(("slug", "nombre", "activo", "ultimo_snapshot", "enlaces"), r)) for r in rs]

    # ── snapshots ─────────────────────────────────────────────────────────
    def publicar_snapshot(self, cliente, datos: dict) -> dict:
        crudo = json.dumps(datos, ensure_ascii=False, separators=(",", ":"))
        with self.pool.connection() as c:
            r = c.execute(
                """INSERT INTO snapshots (cliente,datos,desde,hasta,n_leads,bytes)
                   VALUES (%s,%s::jsonb,%s,%s,%s,%s) RETURNING id,generado""",
                (cliente, crudo, datos.get("desde"), datos.get("hasta"),
                 len(datos.get("leads") or []), len(crudo.encode()))).fetchone()
        return {"id": r[0], "generado": r[1], "n_leads": len(datos.get("leads") or []),
                "bytes": len(crudo.encode())}

    def snapshot(self, cliente) -> Optional[dict]:
        with self.pool.connection() as c:
            r = c.execute("SELECT datos FROM snapshots WHERE cliente=%s "
                          "ORDER BY generado DESC LIMIT 1", (cliente,)).fetchone()
        return r[0] if r else None

    def historial(self, cliente, limite=20) -> list[dict]:
        with self.pool.connection() as c:
            rs = c.execute("SELECT id,generado,desde,hasta,n_leads,bytes FROM snapshots "
                           "WHERE cliente=%s ORDER BY generado DESC LIMIT %s",
                           (cliente, limite)).fetchall()
        return [dict(zip(("id", "generado", "desde", "hasta", "n_leads", "bytes"), r)) for r in rs]

    def purgar_snapshots(self, cliente, conservar=30) -> int:
        with self.pool.connection() as c:
            r = c.execute("""DELETE FROM snapshots WHERE cliente=%s AND id NOT IN (
                               SELECT id FROM snapshots WHERE cliente=%s
                               ORDER BY generado DESC LIMIT %s)""",
                          (cliente, cliente, conservar))
            return r.rowcount

    # ── visor ─────────────────────────────────────────────────────────────
    def guardar_visor(self, index_html, app_js, hash_) -> dict:
        with self.pool.connection() as c:
            c.execute("""INSERT INTO visor (id,index_html,app_js,hash,subido)
                         VALUES (1,%s,%s,%s,now())
                         ON CONFLICT (id) DO UPDATE SET
                           index_html=EXCLUDED.index_html, app_js=EXCLUDED.app_js,
                           hash=EXCLUDED.hash, subido=now()""",
                      (index_html, app_js, hash_))
        return self.visor()

    def visor(self) -> Optional[dict]:
        with self.pool.connection() as c:
            r = c.execute("SELECT index_html,app_js,hash,subido FROM visor WHERE id=1").fetchone()
        if not r:
            return None
        return {"index": r[0], "app": r[1], "hash": r[2], "subido": r[3]}

    # ── enlaces ───────────────────────────────────────────────────────────
    def crear_enlace(self, cliente, modo="cliente", nota=None, caduca=None) -> dict:
        tok = nuevo_token()
        with self.pool.connection() as c:
            c.execute("INSERT INTO enlaces (token,cliente,modo,nota,caduca) VALUES (%s,%s,%s,%s,%s)",
                      (tok, cliente, modo, nota, caduca))
        return {"token": tok, "cliente": cliente, "modo": modo, "nota": nota, "caduca": caduca}

    def enlace(self, token) -> Optional[dict]:
        with self.pool.connection() as c:
            r = c.execute("SELECT token,cliente,modo,nota,caduca,revocado,creado,accesos "
                          "FROM enlaces WHERE token=%s", (token,)).fetchone()
        if not r:
            return None
        return dict(zip(("token", "cliente", "modo", "nota", "caduca", "revocado", "creado", "accesos"), r))

    def enlaces(self, cliente=None) -> list[dict]:
        q = ("SELECT token,cliente,modo,nota,caduca,revocado,creado,accesos,ultimo_acceso "
             "FROM enlaces")
        p: tuple = ()
        if cliente:
            q += " WHERE cliente=%s"
            p = (cliente,)
        q += " ORDER BY creado DESC"
        with self.pool.connection() as c:
            rs = c.execute(q, p).fetchall()
        cols = ("token", "cliente", "modo", "nota", "caduca", "revocado", "creado", "accesos", "ultimo_acceso")
        return [dict(zip(cols, r)) for r in rs]

    def revocar_enlace(self, token) -> bool:
        with self.pool.connection() as c:
            r = c.execute("UPDATE enlaces SET revocado=TRUE WHERE token=%s", (token,))
        return r.rowcount > 0

    def marcar_acceso(self, token) -> None:
        try:
            with self.pool.connection() as c:
                c.execute("UPDATE enlaces SET accesos=accesos+1, ultimo_acceso=now() "
                          "WHERE token=%s", (token,))
        except Exception:
            pass  # contar accesos nunca debe tumbar una visita


# ══════════════════════════════════════════════════════════════════════════════
#  Ficheros (desarrollo, pruebas y despliegue con volumen)
# ══════════════════════════════════════════════════════════════════════════════
class AlmacenFicheros:
    tipo = "ficheros"

    def __init__(self, raiz: str = "_datos_local"):
        self.raiz = pathlib.Path(raiz)
        (self.raiz / "snapshots").mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._idx = self.raiz / "indice.json"
        if not self._idx.exists():
            self._escribir({"clientes": {}, "enlaces": {}, "historial": {}})

    def _leer(self) -> dict:
        return json.loads(self._idx.read_text(encoding="utf-8"))

    def _escribir(self, d: dict) -> None:
        tmp = self._idx.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        tmp.replace(self._idx)

    def guardar_cliente(self, slug, nombre, ghl_location_id=None, tz=None, fuentes=None):
        with self._lock:
            d = self._leer()
            prev = d["clientes"].get(slug, {})
            d["clientes"][slug] = {
                "slug": slug, "nombre": nombre,
                "ghl_location_id": ghl_location_id or prev.get("ghl_location_id"),
                "tz": tz or prev.get("tz"),
                "fuentes": fuentes or prev.get("fuentes") or ["crm"],
                "activo": prev.get("activo", True),
                "creado": prev.get("creado") or ahora().isoformat(),
            }
            self._escribir(d)
        return self.cliente(slug)

    def cliente(self, slug):
        return self._leer()["clientes"].get(slug)

    def clientes(self):
        d = self._leer()
        out = []
        for slug, c in sorted(d["clientes"].items(), key=lambda kv: kv[1]["nombre"]):
            h = d["historial"].get(slug) or []
            out.append({"slug": slug, "nombre": c["nombre"], "activo": c["activo"],
                        "ultimo_snapshot": h[0]["generado"] if h else None,
                        "enlaces": sum(1 for e in d["enlaces"].values()
                                       if e["cliente"] == slug and not e["revocado"])})
        return out

    def publicar_snapshot(self, cliente, datos):
        crudo = json.dumps(datos, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            (self.raiz / "snapshots" / f"{cliente}.json").write_text(crudo, encoding="utf-8")
            d = self._leer()
            reg = {"id": len(d["historial"].get(cliente, [])) + 1,
                   "generado": ahora().isoformat(),
                   "desde": datos.get("desde"), "hasta": datos.get("hasta"),
                   "n_leads": len(datos.get("leads") or []), "bytes": len(crudo.encode())}
            d["historial"].setdefault(cliente, []).insert(0, reg)
            self._escribir(d)
        return reg

    def snapshot(self, cliente):
        f = self.raiz / "snapshots" / f"{cliente}.json"
        if not f.exists():
            return None
        return json.loads(f.read_text(encoding="utf-8"))

    def historial(self, cliente, limite=20):
        return (self._leer()["historial"].get(cliente) or [])[:limite]

    def purgar_snapshots(self, cliente, conservar=30):
        return 0

    def guardar_visor(self, index_html, app_js, hash_):
        with self._lock:
            d = self.raiz / "visor"
            d.mkdir(exist_ok=True)
            (d / "index.html").write_text(index_html, encoding="utf-8")
            (d / "app.js").write_text(app_js, encoding="utf-8")
            (d / "meta.json").write_text(
                json.dumps({"hash": hash_, "subido": ahora().isoformat()}), encoding="utf-8")
        return self.visor()

    def visor(self):
        d = self.raiz / "visor"
        if not (d / "meta.json").exists():
            return None
        m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        return {"index": (d / "index.html").read_text(encoding="utf-8"),
                "app": (d / "app.js").read_text(encoding="utf-8"),
                "hash": m["hash"], "subido": m["subido"]}

    def crear_enlace(self, cliente, modo="cliente", nota=None, caduca=None):
        tok = nuevo_token()
        with self._lock:
            d = self._leer()
            d["enlaces"][tok] = {"token": tok, "cliente": cliente, "modo": modo, "nota": nota,
                                 "caduca": caduca.isoformat() if hasattr(caduca, "isoformat") else caduca,
                                 "revocado": False, "creado": ahora().isoformat(),
                                 "accesos": 0, "ultimo_acceso": None}
            self._escribir(d)
        return d["enlaces"][tok]

    def enlace(self, token):
        e = self._leer()["enlaces"].get(token)
        if e and isinstance(e.get("caduca"), str):
            e = dict(e, caduca=datetime.fromisoformat(e["caduca"]))
        return e

    def enlaces(self, cliente=None):
        es = list(self._leer()["enlaces"].values())
        if cliente:
            es = [e for e in es if e["cliente"] == cliente]
        return sorted(es, key=lambda e: e["creado"], reverse=True)

    def revocar_enlace(self, token):
        with self._lock:
            d = self._leer()
            if token not in d["enlaces"]:
                return False
            d["enlaces"][token]["revocado"] = True
            self._escribir(d)
        return True

    def marcar_acceso(self, token):
        try:
            with self._lock:
                d = self._leer()
                if token in d["enlaces"]:
                    d["enlaces"][token]["accesos"] += 1
                    d["enlaces"][token]["ultimo_acceso"] = ahora().isoformat()
                    self._escribir(d)
        except Exception:
            pass


def abrir_almacen():
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if dsn:
        return AlmacenPostgres(dsn)
    return AlmacenFicheros(os.environ.get("RUTA_DATOS_LOCAL", "_datos_local"))
