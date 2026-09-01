# Reportes Sentinel

Servicio que sirve los dashboards de adquisición de los clientes de Sentinel Marketing.
Un solo visor, un snapshot de datos por cliente, y un enlace con token por destinatario.

## Por qué está partido en dos

Antes cada reporte era un `dashboard.html` de ~1 MB con los datos soldados dentro: para
refrescar una cifra había que regenerar y volver a mover el archivo completo, y arreglar
un bug significaba regenerar el reporte de todos los clientes.

Ahora son dos piezas:

| Pieza | Qué es | Peso | Cuándo cambia |
|---|---|---|---|
| visor (`index.html` + `app.js`) | El dashboard. Igual para todos los clientes. | ~230 KB (76 KB comprimido) | Solo al mejorar el dashboard |
| snapshot por cliente | Los datos: leads, gasto diario, llamadas, etapas. | ~750 KB (~108 KB comprimido) | Cada vez que se refresca |

El visor se cachea en el navegador de forma permanente (va versionado por el hash de
`app.js`), así que a partir de la segunda visita solo se descargan los datos.

## El visor no vive en el repositorio

No hay archivos generados versionados. El dashboard se sube como dato:

```
POST /admin/visor   ← el dashboard.html de una pieza
```

El servicio lo parte, comprueba que el resultado no lleva datos dentro, y lo guarda.
Actualizar el dashboard de **todos** los clientes es esa única llamada: no hace falta
desplegar de nuevo ni regenerar ningún snapshot.

## Estructura

```
web/                  servicio FastAPI
  app/main.py         rutas y validador de snapshots
  app/almacen.py      persistencia: Postgres si hay DATABASE_URL, ficheros si no
  app/privacidad.py   enmascarado de datos personales para los enlaces demo
  app/visor.py        parte un dashboard.html de una pieza en visor + datos
extractor/            construcción del snapshot desde GHL + Meta + Google (Fase 1)
scripts/
  subir_visor.py      sube un dashboard.html nuevo al servicio
  publicar.py         sube un snapshot al servicio
  partir_visor.py     parte un dashboard en local, para inspeccionarlo
pruebas/
  test_validar.py     el snapshot roto no llega al cliente
```

## Modos de enlace

- `interno` — todo tal cual. Para el equipo.
- `cliente` — todo tal cual. El cliente es el dueño de sus datos.
- `demo` — nombres de leads enmascarados (`Z. S.`, `•••• 6024`) y nombre del negocio
  oculto. El enmascarado se hace **en el servidor**: un enlace demo nunca transporta
  el nombre real de un paciente.

## Rutas

Públicas (con token en la URL):

```
GET /r/{token}/                  el visor
GET /r/{token}/snapshot.json     los datos, ya ajustados al modo del enlace
GET /app.js                      el dashboard, versionado por hash
GET /salud                       healthcheck
```

Administración (cabecera `X-Admin-Token`):

```
GET  /admin/estado
POST /admin/visor                           el dashboard.html completo como cuerpo
POST /admin/clientes                        {slug, nombre, ghl_location_id, tz, fuentes}
POST /admin/snapshots/{slug}                el snapshot completo como cuerpo JSON
GET  /admin/snapshots/{slug}                historial
POST /admin/enlaces                         {cliente, modo, nota, caduca}
GET  /admin/enlaces
POST /admin/enlaces/{token}/revocar
```

## El snapshot no se publica si no cuadra

`POST /admin/snapshots/{slug}` rechaza con `422` y una lista de problemas concretos si:

- faltan claves obligatorias, o la ventana está invertida
- hay oportunidades duplicadas (el bug del cursor de paginación de GoHighLevel)
- alguna oportunidad se creó fuera de la ventana declarada
- un lead tiene el contacto dado de alta antes de la ventana **sin** marca de recurrente
  (eso infla el CPL y el ROAS del periodo)
- el cliente no declara zona horaria
- se declara gasto diario pero hay bloques de varios días
- el JSON contiene `NaN` o `Infinity`

## Desarrollo

```bash
cd web
pip install -r requirements.txt
ADMIN_TOKEN=pruebalocal uvicorn app.main:app --port 8077 --reload
```

Sin `DATABASE_URL` guarda en `_datos_local/`. Después:

```bash
export REPORTES_ADMIN_TOKEN=pruebalocal
python scripts/subir_visor.py ruta/al/dashboard.html
python scripts/partir_visor.py ruta/al/dashboard.html --snapshot /tmp/snap.json
python scripts/publicar.py aesthetics-by-cliff /tmp/snap.json
python pruebas/test_validar.py
```

## Variables

| Variable | Para qué |
|---|---|
| `ADMIN_TOKEN` | protege todo `/admin`. Sin ella, `/admin` devuelve 503. |
| `DATABASE_URL` | si está, usa Postgres; si no, ficheros. |
| `RUTA_DATOS_LOCAL` | carpeta del almacén de ficheros (en Railway, el volumen). |
