"""
Partir un dashboard de una pieza en visor + datos.

El servicio guarda el visor como un dato más (igual que los snapshots): se sube el
dashboard.html completo a POST /admin/visor y aquí se parte. Ventajas:
  · el repositorio no lleva archivos generados
  · actualizar el dashboard no necesita un despliegue nuevo
  · el visor y los datos que lo alimentan viven en el mismo sitio

Cómo funciona: el HTML original define `const DATA = {...}` dentro de su <script>. Se
sustituye por `const DATA = window.__SNAPSHOT__` y ese <script> pasa a app.js, que solo
se carga cuando el cargador ya ha rellenado window.__SNAPSHOT__ con lo que devolvió el
servidor. Todo lo demás del dashboard queda intacto: es la garantía de que se ve igual.
"""
from __future__ import annotations

import hashlib
import json

META_NOINDEX = '<meta name="robots" content="noindex, nofollow, noarchive, nosnippet">\n'

ESTILOS_BOOT = """<style>
/* ── Fase 0 · estados de carga del visor ─────────────────────────────── */
#boot{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;
  background:var(--bg,#0b0f14);z-index:9999;font-family:var(--d,system-ui);padding:24px}
#boot .bx{max-width:440px;text-align:center}
#boot .sp{width:26px;height:26px;margin:0 auto 18px;border:2px solid rgba(255,255,255,.16);
  border-top-color:var(--pu,#8b7cf6);border-radius:50%;animation:bsp .8s linear infinite}
@keyframes bsp{to{transform:rotate(360deg)}}
#boot h3{font-size:15px;font-weight:600;margin:0 0 8px;color:var(--tx,#e8edf4)}
#boot p{font-size:13px;line-height:1.55;margin:0 0 6px;color:var(--mut,#93a1b5)}
#boot code{font-family:var(--m,ui-monospace);font-size:12px;background:rgba(255,255,255,.06);
  padding:2px 5px;border-radius:3px}
#boot .rt{margin-top:16px;display:inline-block;font-size:13px;font-weight:600;cursor:pointer;
  background:var(--pu,#8b7cf6);color:#fff;border:0;padding:9px 18px;border-radius:6px;font-family:inherit}
@media (prefers-reduced-motion:reduce){#boot .sp{animation-duration:2.4s}}
</style>
"""

CAPA_BOOT = """
 <div id="boot"><div class="bx">
   <div class="sp" id="bootsp"></div>
   <h3 id="boott">Cargando los datos del reporte…</h3>
   <p id="bootm">Un momento.</p>
 </div></div>"""

CARGADOR = """
<script>
/* ══════════════════════════════════════════════════════════════════════════════════════
   ARRANQUE DEL VISOR · Fase 0
   Este archivo NO lleva datos dentro. Pide su snapshot al servidor y solo entonces carga
   app.js, que es el dashboard entero. La URL del snapshot es relativa al visor: el
   servicio sirve el visor en /r/{token}/ y los datos en /r/{token}/snapshot.json, así que
   este mismo archivo vale para todos los clientes y se cachea una sola vez.
   ══════════════════════════════════════════════════════════════════════════════════════ */
(function(){
  var boot=document.getElementById('boot');
  function fallo(titulo,detalle,reintentar){
    var sp=document.getElementById('bootsp'); if(sp) sp.style.display='none';
    document.getElementById('boott').textContent=titulo;
    document.getElementById('bootm').innerHTML=detalle+(reintentar?
      '<br><button class="rt" onclick="location.reload()">Volver a intentar</button>':'');
  }
  var url='./snapshot.json';
  if(location.protocol!=='file:'){
    try{ url=new URL('snapshot.json', location.href).href; }catch(e){}
  }
  fetch(url,{cache:'no-store',credentials:'same-origin'})
    .then(function(r){
      if(r.status===404) throw {tipo:'404'};
      if(r.status===401||r.status===403) throw {tipo:'403'};
      if(!r.ok) throw {tipo:'http',cod:r.status};
      return r.json();
    })
    .then(function(d){
      if(!d||!Array.isArray(d.leads)) throw {tipo:'forma'};
      window.__SNAPSHOT__=d;
      var s=document.createElement('script');
      s.src=(location.protocol==='file:'?'./app.js':'/app.js');
      s.onload=function(){ if(boot&&boot.parentNode) boot.parentNode.removeChild(boot); };
      s.onerror=function(){ fallo('No se pudo cargar el visor',
        'Los datos llegaron bien, pero el archivo <code>app.js</code> del dashboard no. Suele ser un despliegue a medias.',true); };
      document.body.appendChild(s);
    })
    .catch(function(e){
      var t=(e&&e.tipo)||'red';
      if(t==='404')      fallo('Este reporte no existe','El enlace es incorrecto o el reporte se dio de baja. Pide un enlace nuevo.',false);
      else if(t==='403') fallo('Este enlace ya no es válido','Ha caducado o se revocó el acceso. Pide un enlace nuevo.',false);
      else if(t==='forma') fallo('Los datos llegaron incompletos','El servidor respondió, pero el snapshot no tiene la forma esperada. Hay que regenerarlo.',true);
      else if(t==='http') fallo('El servidor devolvió un error','Código <code>'+e.cod+'</code>. Suele pasar mientras se publica un snapshot nuevo.',true);
      else               fallo('Sin conexión con el servidor','No se pudo alcanzar el servicio de reportes. Comprueba tu conexión.',true);
    });
})();
</script>
</body></html>"""

CABECERA_APP = """/* Dashboard de adquisición · Sentinel Marketing
   GENERADO — no editar a mano. Sale de partir el dashboard.html subido a /admin/visor.
   Espera que window.__SNAPSHOT__ ya esté poblado antes de evaluarse. */
"""


def partir(html: str):
    i = html.index('const DATA = ')
    j = html.index('\n', i)
    datos = json.loads(html[i + len('const DATA = '):j].rstrip(';'))
    html = html[:i] + 'const DATA = window.__SNAPSHOT__;' + html[j:]

    ancla = html.index('const DATA = window.__SNAPSHOT__;')
    k = html.rindex('<script', 0, ancla)
    ini = html.index('>', k) + 1
    fin = html.index('</script>', ini)
    cabeza, cuerpo = html[:k], html[ini:fin]

    if '</head>' not in cabeza:
        raise ValueError('El HTML no tiene </head>: no es el dashboard esperado.')
    cabeza = cabeza.replace('</head>', META_NOINDEX + ESTILOS_BOOT + '</head>', 1)

    if cabeza.count('<div id="view"></div>') != 1:
        raise ValueError('No encuentro el contenedor #view: revisa si cambió el markup.')
    cabeza = cabeza.replace('<div id="view"></div>',
                            '<div id="view"></div>' + CAPA_BOOT, 1)
    return cabeza + CARGADOR, CABECERA_APP + cuerpo, datos


def partir_html(html: str) -> dict:
    """Devuelve {index, app, hash, datos} o levanta ValueError con un motivo legible."""
    if 'const DATA = ' not in html:
        raise ValueError("El archivo no parece el dashboard: no encuentro 'const DATA ='.")
    try:
        index, app, datos = partir(html)
    except ValueError:
        raise
    except Exception as ex:
        raise ValueError(f"No pude partir el dashboard: {ex}")

    if 'window.__SNAPSHOT__' not in app:
        raise ValueError("El app.js resultante no lee el snapshot.")
    if '"leads"' in index or 'const DATA = {' in index:
        raise ValueError("El visor resultante todavía lleva datos dentro.")

    return {
        "index": index,
        "app": app,
        "hash": hashlib.sha256(app.encode()).hexdigest()[:12],
        "datos": datos,
    }
