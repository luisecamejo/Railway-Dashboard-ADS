# -*- coding: utf-8 -*-
"""
Seguridad del servicio de reportes: cabeceras, límite de intentos y topes de tamaño.

Lo que protege y de qué:

  · Cabeceras — que el reporte no se indexe, no filtre el token por el Referer, no se
    pueda empotrar salvo donde tú digas, y que el navegador no cargue nada de fuera
    que no esté declarado.
  · Límite de intentos — el token de administración y los de enlace son de 192 bits y
    no se adivinan por fuerza bruta, pero sin límite un bot puede machacar el servicio
    y llenar los logs. Además, si alguien lo intenta, queda registrado.
  · Topes de tamaño — un cuerpo enorme en /admin/visor o /admin/snapshots agotaría la
    memoria del contenedor. Se rechaza antes de leerlo.

El límite vive en memoria del proceso. Con una réplica (lo actual) es exacto; con varias
sería por réplica, que sigue siendo suficiente para lo que protege.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import defaultdict, deque

log = logging.getLogger("reportes.seguridad")

# ═════════════════════════════════════════════════════════════════════════════
#  Cabeceras
# ═════════════════════════════════════════════════════════════════════════════
BASE = {
    "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    # Railway ya sirve solo por HTTPS; esto impide que un navegador acepte un http://
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Permissions-Policy": ("accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
                           "magnetometer=(), microphone=(), payment=(), usb=()"),
}

# Las miniaturas de los creativos son URLs firmadas del CDN de Meta y el subdominio
# cambia (scontent-fra3-1, scontent-fra5-2…), así que se permite cualquier https para
# imágenes. Todo lo demás queda cerrado: el reporte no pide nada a terceros.
_CSP_REPORTE = [
    "default-src 'none'",
    "script-src 'self' 'unsafe-inline'",   # el dashboard lleva su script en línea
    # Google Fonts: el dashboard pide Montserrat y JetBrains Mono. Sin estos dos origenes
    # la CSP bloquea la hoja de estilos y el reporte del cliente sale en la fuente del
    # sistema (verificado con Playwright: "Refused to load the stylesheet"). Son dos
    # hosts de solo lectura de Google, no reciben ningun dato del reporte.
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "img-src 'self' data: https:",
    "font-src 'self' data: https://fonts.gstatic.com",
    "connect-src 'self'",                  # solo pide su propio snapshot
    "base-uri 'none'",
    "form-action 'none'",
]

_ORIGEN = re.compile(r"^https://(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def origen_valido(v: str) -> bool:
    """Solo https y un host, con comodín opcional en el primer nivel. Sin rutas ni puertos."""
    return bool(_ORIGEN.match((v or "").strip().lower()))


def normaliza_dominios(valor) -> list[str]:
    """Acepta lista o texto separado por comas/espacios. Devuelve orígenes válidos, sin repetir."""
    if isinstance(valor, str):
        crudos = re.split(r"[\s,;]+", valor)
    else:
        crudos = list(valor or [])
    fuera, vistos = [], set()
    for c in crudos:
        c = (c or "").strip().rstrip("/").lower()
        if not c:
            continue
        if not c.startswith("http"):
            c = "https://" + c
        if origen_valido(c) and c not in vistos:
            vistos.add(c)
            fuera.append(c)
    return fuera


def cabeceras(extra: dict | None = None, *, csp_reporte: bool = False,
              empotrable_en: list[str] | None = None) -> dict:
    """
    csp_reporte    — aplica la política estricta de contenido de la página del reporte.
    empotrable_en  — orígenes que pueden meter esto en un iframe. Vacío o None = ninguno.
    """
    h = dict(BASE)
    if empotrable_en:
        marco = "frame-ancestors " + " ".join(empotrable_en)
    else:
        marco = "frame-ancestors 'none'"
        # X-Frame-Options no admite varios orígenes; solo se manda cuando es "nadie",
        # que es justo el caso que sí entiende cualquier navegador viejo.
        h["X-Frame-Options"] = "DENY"
    h["Content-Security-Policy"] = "; ".join((_CSP_REPORTE if csp_reporte else
                                              ["default-src 'self'", "base-uri 'none'"]) + [marco])
    if extra:
        h.update(extra)
    return h


# ═════════════════════════════════════════════════════════════════════════════
#  Límite de intentos
# ═════════════════════════════════════════════════════════════════════════════
class Limitador:
    """
    Ventana deslizante por IP. Solo cuentan los FALLOS: quien acierta no gasta cuota.

    intentos/ventana → si se pasa, bloqueo durante `castigo` segundos.

    Quien presenta la credencial correcta NO se ve afectado: se comprueba primero y, si
    acierta, se le limpia el contador. Así el límite frena bots sin poder dejar fuera al
    operador legítimo ni a un cliente que comparta IP con alguien que trastea.
    """

    def __init__(self, intentos: int, ventana: int, castigo: int, etiqueta: str):
        self.intentos, self.ventana, self.castigo = intentos, ventana, castigo
        self.etiqueta = etiqueta
        self._fallos: dict[str, deque] = defaultdict(deque)
        self._bloqueo: dict[str, float] = {}
        self._lock = threading.Lock()

    def bloqueado(self, ip: str) -> int:
        """Segundos que quedan de bloqueo, 0 si puede pasar."""
        with self._lock:
            resta = int(self._bloqueo.get(ip, 0) - time.time())
            if resta <= 0:
                self._bloqueo.pop(ip, None)
                return 0
            return resta

    def acierto(self, ip: str) -> None:
        """Quien acierta no gasta cuota, y de paso se le limpia el historial de fallos."""
        with self._lock:
            self._fallos.pop(ip, None)
            self._bloqueo.pop(ip, None)

    def fallo(self, ip: str, detalle: str = "") -> int:
        """Registra un fallo. Devuelve los segundos de bloqueo, o 0 si todavía puede probar."""
        ahora = time.time()
        with self._lock:
            resta = int(self._bloqueo.get(ip, 0) - ahora)
            if resta > 0:
                return resta
            q = self._fallos[ip]
            q.append(ahora)
            while q and ahora - q[0] > self.ventana:
                q.popleft()
            n = len(q)
            if n >= self.intentos:
                self._bloqueo[ip] = ahora + self.castigo
                q.clear()
                log.warning("BLOQUEADA %s · %s · %d fallos · %ds %s",
                            ip, self.etiqueta, n, self.castigo, detalle)
                return self.castigo
            log.info("fallo %s · %s · %d/%d %s", ip, self.etiqueta, n, self.intentos, detalle)
            return 0

    def limpia(self) -> None:
        """Suelta memoria de IPs que ya no interesan. Se llama de vez en cuando."""
        ahora = time.time()
        with self._lock:
            for ip in [k for k, v in self._bloqueo.items() if v < ahora]:
                self._bloqueo.pop(ip, None)
            for ip in [k for k, q in self._fallos.items()
                       if not q or ahora - q[-1] > self.ventana * 4]:
                self._fallos.pop(ip, None)


def ip_cliente(request) -> str:
    """
    IP real detrás del proxy de Railway.

    Se toma la ÚLTIMA entrada de X-Forwarded-For, que es la que pone el proxy de Railway.
    La primera la puede inventar el cliente, así que usarla dejaría el límite inútil.
    """
    xff = request.headers.get("x-forwarded-for") or ""
    partes = [p.strip() for p in xff.split(",") if p.strip()]
    if partes:
        return partes[-1]
    return (getattr(request, "client", None) and request.client.host) or "desconocida"


# ═════════════════════════════════════════════════════════════════════════════
#  Topes de tamaño
# ═════════════════════════════════════════════════════════════════════════════
async def leer_cuerpo(request, tope_mb: float, que: str) -> bytes:
    """Lee el cuerpo rechazando lo que pase del tope, sin cargarlo entero en memoria antes."""
    tope = int(tope_mb * 1024 * 1024)
    largo = request.headers.get("content-length")
    if largo and largo.isdigit() and int(largo) > tope:
        raise ValueError(f"El {que} pesa {int(largo)/1048576:.1f} MB y el tope es {tope_mb:g} MB.")
    trozos, total = [], 0
    async for trozo in request.stream():
        total += len(trozo)
        if total > tope:
            raise ValueError(f"El {que} pasa del tope de {tope_mb:g} MB.")
        trozos.append(trozo)
    return b"".join(trozos)
