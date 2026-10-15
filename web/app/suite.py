# -*- coding: utf-8 -*-
"""
Acceso del equipo a través de Sentinel Suite (suite.sentinelmarketing.net).

Quién entra al panel y a los dashboards internos lo decide la suite: la persona inicia
sesión allí y la suite deja una cookie firmada (`sentinel_sso`, JWT EdDSA, 15 minutos)
válida en todos los subdominios de sentinelmarketing.net. Aquí solo se comprueba:

  · que la firma es de la suite (clave pública en /.well-known/jwks.json),
  · que la persona tiene la herramienta `reportes` (rol admin o equipo),
  · y que la subcuenta (el slug del cliente, el mismo en la suite y aquí) está entre las suyas.

Lo que NO cambia:

  · Los enlaces con token (/r/<token>/…) que ve el cliente, empotrados en GoHighLevel.
  · La cabecera X-Admin-Token: la siguen usando los extractores y los scripts.

Sin SUITE_URL el acceso por la suite queda apagado (pruebas antiguas, desarrollo local).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

from fastapi import Request

from .sentinel_suite_auth import SSO_COOKIE, SuiteAuth, can_access_subaccount

SUITE_URL = os.environ.get("SUITE_URL", "").strip().rstrip("/")
HERRAMIENTA = "reportes"
ROLES = ("admin", "equipo")

_auth: Optional[SuiteAuth] = SuiteAuth(hub_url=SUITE_URL, tool=HERRAMIENTA) if SUITE_URL else None


def activa() -> bool:
    return _auth is not None


@dataclass
class Persona:
    email: str
    nombre: Optional[str]
    rol: str                 # rol en reportes: admin | equipo
    subs: object             # "*" = todas las subcuentas, o lista de slugs
    claims: dict

    def llega(self, slug: str) -> bool:
        return can_access_subaccount(self.claims, slug)

    def filtra(self, slugs):
        """Deja solo los clientes que esta persona puede ver."""
        return list(slugs) if self.subs == "*" else [s for s in slugs if s in (self.subs or [])]


def persona(peticion: Request) -> Optional[Persona]:
    """La persona de la cookie de la suite, si es válida y tiene acceso a reportes."""
    if not _auth:
        return None
    claims = _auth.verify(peticion.cookies.get(SSO_COOKIE))
    if not claims:
        return None
    rol = (claims.get("tools") or {}).get(HERRAMIENTA)
    if rol not in ROLES:
        return None
    return Persona(email=claims.get("email", ""), nombre=claims.get("name"), rol=rol,
                   subs=claims.get("subs", []), claims=claims)


def hay_cookie(peticion: Request) -> bool:
    return bool(peticion.cookies.get(SSO_COOKIE))


def url_propia(peticion: Request) -> str:
    proto = peticion.headers.get("x-forwarded-proto", peticion.url.scheme).split(",")[0].strip()
    host = peticion.headers.get("x-forwarded-host", peticion.headers.get("host", peticion.url.netloc))
    qs = f"?{peticion.url.query}" if peticion.url.query else ""
    return f"{proto}://{host}{peticion.url.path}{qs}"


def origen_propio(peticion: Request) -> str:
    proto = peticion.headers.get("x-forwarded-proto", peticion.url.scheme).split(",")[0].strip()
    host = peticion.headers.get("x-forwarded-host", peticion.headers.get("host", peticion.url.netloc))
    return f"{proto}://{host}"


def login(peticion: Request, sub: Optional[str] = None) -> str:
    """
    Adónde mandar a quien llega sin sesión válida: la suite renueva la cookie (o pide
    iniciar sesión) y vuelve aquí. Si tras renovar sigue sin acceso, el aviso lo da la suite.
    """
    url = f"{SUITE_URL}/sso/refresh?return={quote(url_propia(peticion), safe='')}&tool={HERRAMIENTA}"
    return url + (f"&sub={quote(sub, safe='')}" if sub else "")


def escritura_segura(peticion: Request) -> bool:
    """
    La cookie de la suite viaja también en peticiones que otro subdominio de
    sentinelmarketing.net le haga hacer al navegador (mismo «sitio»). Para lo que cambia
    datos se exige que la petición venga de esta misma web.
    """
    if peticion.method in ("GET", "HEAD", "OPTIONS"):
        return True
    origen = peticion.headers.get("origin")
    if origen:
        return origen == origen_propio(peticion)
    # Sin Origin (navegadores viejos): al menos la cabecera que solo pone nuestro panel
    return peticion.headers.get("x-requested-with") == "panel-reportes"
