# -*- coding: utf-8 -*-
"""
Cliente del servicio `reportes` visto desde un extractor.

El extractor no lleva la lista de clientes ni los ids de cuenta en variables de
entorno: se los PREGUNTA al servicio. Así hay una sola fuente de verdad (la
configuración del cliente, que se edita sin desplegar) y añadir un cliente nuevo
no obliga a tocar tres servicios.
"""
from __future__ import annotations

import json
import logging

from .http import json_get, json_post, pedir

log = logging.getLogger("extractor.reportes")


class Reportes:
    def __init__(self, base: str, token: str, *, timeout: int = 180):
        if not base:
            raise ValueError("Falta REPORTES_URL.")
        if not token:
            raise ValueError("Falta REPORTES_ADMIN_TOKEN.")
        self.base = base.rstrip("/")
        self.cab = {"X-Admin-Token": token}
        self.timeout = timeout

    # ── lectura ───────────────────────────────────────────
    def clientes(self) -> list[dict]:
        return json_get(f"{self.base}/admin/estado", cabeceras=self.cab,
                        timeout=self.timeout).get("clientes") or []

    def config(self, slug: str) -> dict:
        return json_get(f"{self.base}/admin/config/{slug}", cabeceras=self.cab,
                        timeout=self.timeout).get("config") or {}

    def cuentas_de(self, slug: str, plataforma: str) -> list[dict]:
        """Las cuentas de esa plataforma declaradas en la config del cliente."""
        cfg = self.config(slug)
        return [c for c in (cfg.get("cuentas") or [])
                if str(c.get("plataforma", "")).lower() == plataforma.lower()]

    def objetivos(self, plataforma: str) -> list[dict]:
        """
        Clientes activos con al menos una cuenta de esa plataforma.

        Devuelve [{slug, nombre, tz, cuentas:[...]}]. Un cliente sin cuentas de esa
        plataforma no es un error: simplemente ese extractor no tiene nada que hacer
        con él.
        """
        fuera = []
        for c in self.clientes():
            if not c.get("activo", True):
                continue
            cfg = self.config(c["slug"])
            cuentas = [x for x in (cfg.get("cuentas") or [])
                       if str(x.get("plataforma", "")).lower() == plataforma.lower()]
            if not cuentas:
                continue
            if not cfg.get("tz"):
                log.warning("%s no declara zona horaria en su config: se salta, porque "
                            "sin ella las fechas no cuadran con el CRM", c["slug"])
                continue
            fuera.append({"slug": c["slug"], "nombre": cfg.get("nombre") or c["nombre"],
                          "tz": cfg["tz"], "cuentas": cuentas, "config": cfg})
        return fuera

    # ── escritura ────────────────────────────────────────
    def guardar_config(self, slug: str, cfg: dict) -> dict:
        """
        Deja la configuración de construcción del cliente.

        No la usa la extracción diaria: la usa `operar.py config` al dar de alta un
        cliente o cuando su configuración cambia. Está aquí y no en el script para que
        haya un solo sitio que sepa hablar con el servicio.
        """
        return json_post(f"{self.base}/admin/config/{slug}", cfg,
                         cabeceras=self.cab, timeout=self.timeout)

    def enviar_crudo(self, slug: str, fuente: str, datos: dict) -> dict:
        crudo = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        _c, cuerpo, _h = pedir(
            f"{self.base}/admin/crudo/{slug}/{fuente}", metodo="POST", cuerpo=crudo,
            cabeceras={**self.cab, "Content-Type": "application/json",
                       "Accept": "application/json"},
            timeout=self.timeout)
        return json.loads(cuerpo or b"{}")

    def construir(self, slug: str, *, publicar: bool = True) -> dict:
        return json_post(f"{self.base}/admin/construir/{slug}", {"publicar": publicar},
                         cabeceras=self.cab, timeout=self.timeout)

    # ── cola de refresco a demanda ──────────────────────────────
    # Solo las usa `refrescar.py`. Están aquí y no allí por la misma razón que el
    # resto: un único sitio sabe hablar con el servicio.
    def cola_refresco(self) -> list[str]:
        """Los clientes que pidieron refresco. Al pedirla, quedan marcados en curso."""
        r = json_get(f"{self.base}/admin/cola-refresco", cabeceras=self.cab,
                     timeout=self.timeout)
        return list(r.get("pendientes") or [])

    def cerrar_refresco(self, slug: str, *, ok: bool, detalle: str = "") -> dict:
        return json_post(f"{self.base}/admin/cola-refresco/{slug}",
                         {"ok": ok, "detalle": detalle},
                         cabeceras=self.cab, timeout=self.timeout)
