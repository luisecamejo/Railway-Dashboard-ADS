# Extractores

Un servicio de Railway por fuente. Cada uno es una **tarea programada (cron)**, no un
servicio encendido: solo se paga lo que corre.

| Servicio | Comando de inicio | Qué hace |
|---|---|---|
| `extractor-ghl` | `python -m ghl.extraer` | CRM: oportunidades, pipelines, vendedores, llamadas. Al terminar dispara la construcción del snapshot. |
| `extractor-meta` | `python -m meta.extraer` | Meta Ads: gasto diario, campañas, anuncios, miniaturas. |
| `extractor-google` | `python -m google.extraer` | Google Ads (pendiente del token de desarrollador). |

Un solo token de desarrollador de Google Ads sirve para **todos** los clientes: se pide
una vez sobre una cuenta MCC y lo que cambia por cliente es la cuenta que se lee, que
sale de su `clientes/SLUG.json`. Por eso no se monta nada por cliente para Google.

**Directorio raíz de los tres servicios en Railway: `/extractores`.**

## Cómo encajan

```
extractor-ghl    ──POST /admin/crudo/{cliente}/ghl────┐
extractor-meta   ──POST /admin/crudo/{cliente}/meta───┼──> reportes junta, valida
extractor-google ──POST /admin/crudo/{cliente}/google─┘    y publica el snapshot
```

Ninguno construye el snapshot ni sabe qué clientes hay: **se lo preguntan a `reportes`**,
que guarda la configuración de cada cliente. Añadir un cliente nuevo no toca ningún
extractor.

**Nada de esto es de un cliente concreto.** Cada extractor recorre TODOS los clientes
activos que declaren una cuenta de su plataforma (`SOLO_CLIENTE` existe solo para
probar). Un cliente sin cuenta de Google no es un error: ese extractor simplemente no
tiene nada que hacer con él.

Si Meta falla un día, su trozo se queda con los datos de ayer y el reporte sigue en pie
con el resto, en vez de quedarse sin snapshot entero.

## Por qué hay un extractor de GHL si ya existe `ghl-mcp`

Porque hacen cosas distintas, y la pregunta sale sola al ver que `ghl-mcp` ya sabe
hablar con GoHighLevel.

`ghl-mcp` es **la conexión**: resuelve el OAuth de agencia, refresca los tokens, cachea
la lista de sub-cuentas y expone herramientas (`ghl_export_opportunities_compact`,
`ghl_export_seller_performance`, `ghl_api_get`). Contesta UNA pregunta cuando alguien se
la hace. No tiene horario, no sabe qué clientes hay que reportar, no sabe qué es una
ventana de 120 días y no sabe que existe el servicio `reportes`.

El extractor es **la decisión**: qué preguntar, cuándo, para quién y qué hacer con la
respuesta.

- Le pregunta a `reportes` qué clientes están activos, su `ghlLocationId` y su zona.
- Calcula la ventana en la zona **del negocio** y recorta los bordes a mano, porque el
  filtro de fechas del CRM no interpreta el día en esa zona. Esto es lo que hace que las
  cifras cuadren con el CRM, y no está —ni debe estar— en el MCP.
- Pagina los exports y decide que **solo** se piden los mensajes de las conversaciones
  que traen contadores de llamada: 880 en vez de 1.986.
- Cruza el importe de la oportunidad en las filas de vendedor.
- Deja el trozo crudo en `reportes` y dispara la construcción.

Así que no: con `ghl-mcp` solo no se actualiza un reporte. Lo que sí es cierto —y ya está
demostrado en este repositorio— es que el extractor **no necesita ser un servicio aparte**:
los extractores no tienen dependencias más allá de la librería estándar, y
`extractores/refrescar.py` llama a estos mismos `main()` desde dentro del servicio
`reportes` para el botón «Actualizar ahora». Tener tres crones separados es una elección
—aísla los fallos y separa los logs— no un requisito.

## Variables

Comunes a los tres:

| Variable | Valor |
|---|---|
| `REPORTES_URL` | `https://reportes-production-a40d.up.railway.app` |
| `REPORTES_ADMIN_TOKEN` | `${{reportes.ADMIN_TOKEN}}` — por referencia, el valor no se copia |
| `SOLO_CLIENTE` | opcional, un slug, para probar con uno solo |

`extractor-ghl`:

| Variable | Valor |
|---|---|
| `GHL_MCP_URL` | `https://sentinel-mcp-bd11.up.railway.app` |
| `GHL_MCP_TOKEN` | `${{ghl-mcp.GHL_MCP_HTTP_TOKEN}}` — por referencia |
| `GHL_DIAS_VENDEDORES` | `120` (por defecto). Cierra el hallazgo H-3. |
| `CONSTRUIR_AL_TERMINAR` | `1` |

Ajuste fino del ritmo (todas tienen valor por defecto; solo se ponen para desviarse):

| Variable | Por defecto | Para qué |
|---|---|---|
| `GHL_PAUSA_LLAMADAS` | `0.12` | Pausa entre peticiones de mensajes. GoHighLevel admite ~10 peticiones/segundo por sub-cuenta; 0,12 s deja el bucle en ~8/s. Con el `0.05` anterior iba a ~20/s, el doble del techo, y el 429 era cuestión de tiempo. |
| `GHL_PAUSA_ENTRE_CLIENTES` | `20` | Segundos de descanso entre un cliente y el siguiente, para no arrastrar la cuota que gastó el anterior. |
| `GHL_ENFRIAMIENTO` | `120` | Espera antes de la segunda pasada sobre los clientes que fallaron. |
| `GHL_MCP_TIMEOUT` | `240` | Lo que se le da al MCP para contestar una página. |
| `GHL_LOTE_VENDEDORES` | `50` | Conversaciones por página del export de vendedores. Subirlo hace que una página no quepa en el timeout. |
| `GHL_TOPE_PAGINAS` | `400` | Tope de seguridad: antes que devolver datos a medias, falla. |
| `GHL_CONCURRENCIA` | `3` | Clientes a la vez. Ver «Cuando haya 100 clientes». |

`extractor-meta` y `extractor-google` tienen sus equivalentes: `META_CONCURRENCIA` (4),
`META_PAUSA_ENTRE_CLIENTES`, `META_ENFRIAMIENTO`, `GOOGLE_CONCURRENCIA` (4), etc. Y
Meta tiene una propia que es la que evita el fallo del 11-sep:

| Variable | Por defecto | Para qué |
|---|---|---|
| `META_DIAS_TRAMO` | `30` | Días por petición de insights. La Marketing API no falla por pedirle 120 días: falla por TARDAR, y contesta un HTTP 400 con `error_subcode 1504018` que dice literalmente «prueba con un intervalo de fechas menor». Cuatro peticiones de 30 días cuestan lo mismo en cuota y ninguna se acerca a su tiempo de espera. |
| `META_TOPE_PARTICIONES` | `40` | Si aun así un tramo se agota, se parte por la mitad y se reintenta, hasta llegar a un solo día. Este es el tope de esas particiones. |
| `META_ESPERA_CUOTA` | `60` | Segundos de espera cuando Meta responde un límite de peticiones (códigos 4, 17, 32, 613, 80000-80014). Llegan con HTTP 400, así que el reintento por código de estado no los ve: hay que mirar el cuerpo. |

`extractor-meta`:

| Variable | Valor |
|---|---|
| `META_TOKEN` | token del usuario del sistema con `ads_read`. **Lo pone Luis a mano.** |
| `META_API_VERSION` | `v26.0` |
| `META_TIPOS_LEAD` | opcional, para ajustar qué acción cuenta como lead |

### Por qué las URLs son públicas y no `*.railway.internal`

Sería mejor por la red privada (no sale a internet y no hace falta token en la URL),
pero el SDK de MCP que usa `ghl-mcp` **valida la cabecera `Host`** contra su variable
`ALLOWED_HOSTS`. Llamando por dentro el Host es `ghl-mcp.railway.internal` y responde:

```
403  {"error":{"code":-32000,"message":"Invalid Host: ghl-mcp.railway.internal"}}
```

(comprobado levantando una réplica de su `http-server.js` con el mismo SDK).

Para pasar a red privada: **añade `ghl-mcp.railway.internal` a `ALLOWED_HOSTS` de
ghl-mcp** (sin quitar lo que ya hay) y cambia `GHL_MCP_URL` a
`http://ghl-mcp.railway.internal:8080`. El código no necesita ningún cambio.

## Cuando haya 100 clientes

Lo que hay hoy aguanta ~20 clientes. Lo que sigue es dónde se rompe y en qué orden hay
que arreglarlo, medido sobre la extracción real del 11-sep-2026, no a ojo.

### Dónde se va el tiempo

El cliente más grande (aesthetics-by-cliff, 1.170 conversaciones) tardó **19 min 36 s**
y gastó ~900 peticiones. Repartidos así:

| Paso | Tiempo | % |
|---|---|---|
| export de vendedores (24 páginas) | 682 s | 58% |
| llamadas (880 peticiones, una por conversación) | 469 s | 40% |
| oportunidades (3 páginas) | 19 s | 2% |
| pipelines + usuarios | 2 s | <1% |

**El 98% del tiempo son dos pasos, y los dos vuelven a leer los mismos 120 días cada
noche.** Ahí está todo.

### Lo que ya está resuelto

**Varios clientes a la vez** (`*_CONCURRENCIA`). Los límites de GoHighLevel (~100
peticiones por cada 10 s) y los de Meta son **por sub-cuenta y por cuenta publicitaria**,
no por token de agencia: dos clientes distintos no compiten por la misma cuota. Ir de
uno en uno no protegía de nada, solo alargaba. Cada obrero mantiene su propio ritmo
dentro del cliente que le toca, que es donde sí hay un límite real.

De 8 a 100 clientes, en serie, la pasada de GHL pasaría de ~30 min a **más de 8 horas**.
Con 3 en paralelo baja a ~2 h 45. Es una mejora, pero no es la solución.

### Lo que falta, y es lo que de verdad decide

**Extracción incremental.** De los 120 días de la ventana, 119 ya se extrajeron ayer.
Volver a pedirlos es trabajo O(clientes × ventana) cuando debería ser O(clientes × lo
que cambió).

La clave está en que las dos partes se comportan distinto:

- **Los pasos caros son de solo-añadir.** Una llamada del 3 de agosto no va a cambiar
  nunca, y una conversación cerrada tampoco. El export de vendedores y las llamadas
  (el 98% del tiempo) se pueden pedir solo desde la última extracción, con un par de
  días de solape por seguridad, y fusionar con lo que ya había.
- **El paso que SÍ cambia hacia atrás es el barato.** Una oportunidad creada en
  noviembre se puede ganar hoy — es literalmente el caso de Golden Rose, donde 6 de 7
  ventas son de contactos anteriores a la ventana. Ese paso cuesta 19 s y 3 páginas, así
  que se sigue leyendo **entero** cada noche. No se toca.

Esa asimetría es la que hace el cambio seguro: se vuelve incremental justo donde es
barato hacerlo bien, y se sigue releyendo todo justo donde es caro equivocarse.

Efecto estimado sobre el cliente grande: de ~900 peticiones y 20 min a **~30 peticiones
y bastante menos de un minuto**. Con eso, 100 clientes y 3 en paralelo caben de sobra en
una madrugada.

Qué hace falta para montarlo:

1. `reportes` ya guarda el último trozo crudo por cliente y lo expone
   (`GET /admin/crudo/{slug}`). El extractor lo pide antes de empezar.
2. Extrae solo `[último_hasta − 2 días, hasta]` para vendedores y llamadas.
3. Fusiona por `conv` y por id de llamada, y descarta lo que ya cayó fuera de los 120
   días.
4. Oportunidades, pipelines y usuarios: igual que ahora, completos.

### Y después, cuando duela

- **Cola con estado por cliente** en vez de una lista en memoria: `slug`, último éxito,
  último error, intentos, duración. Con 100 clientes el log deja de ser leíble y hace
  falta una tabla que conteste «¿quién lleva dos días sin actualizarse?» de un vistazo.
  El servicio ya tiene una cola y un hilo obrero para el botón «Actualizar ahora»
  (`web/app/rutas_refrescar.py`): es el mismo mecanismo.
- **No extraer lo que nadie mira.** Diario para los clientes activos, semanal para el
  resto. Es la palanca más barata de todas y no requiere código nuevo, solo un campo en
  la configuración.

## Dar de alta un cliente

La configuración de construcción (productos, roles, SOP, cuentas de anuncios) vive en
el servicio, no aquí: es dato de negocio y cambia sin desplegar. `clientes/SLUG.json`
es solo el valor inicial, y `operar.py` lo deja en el servicio:

```bash
export REPORTES_URL=https://reportes-production-a40d.up.railway.app
export REPORTES_ADMIN_TOKEN=...              # nunca en la línea de comandos

python operar.py estado                      # qué clientes hay y a cuál le falta config
python operar.py config SLUG clientes/SLUG.json
python operar.py construir SLUG --ensayo     # construir SIN publicar, y mirar el resumen
python operar.py construir SLUG              # construir y publicar
```

`--ensayo` es la red de seguridad del primer arranque: enseña leads, gasto, llamadas y
cuántas etapas casaron con el SOP **antes** de tapar el reporte que el cliente ya está
viendo. Si el resumen no cuadra, no se ha publicado nada.

## Qué produjo la primera ejecución de verdad

Sirve de vara de medir: si una noche los números se alejan mucho de esto sin que haya
pasado nada, algo se rompió y no lo va a decir ningún error.

| | 1 de septiembre de 2026, ventana 2026-05-04 → 2026-08-31 |
|---|---|
| `extractor-meta` | 12.927,25 de gasto · 36 campañas · 1.451 filas de anuncios · 147 miniaturas |
| `extractor-ghl` | 994 oportunidades · 1 pipeline (11 etapas) · 1.091 conversaciones · 3.694 llamadas (3.097 salientes) |

Dos cosas que el log dijo y conviene no olvidar:

- **870 de 1.091 conversaciones traen llamadas.** Solo a esas se les piden los mensajes.
  Si algún día ese número se acerca al total, la extracción pasará de ~18 minutos a
  bastante más.
- **14 conversaciones ganadas tienen su oportunidad fuera de la ventana**, así que su
  ingreso no se atribuye al vendedor. No es un fallo: es el precio de una ventana móvil,
  y por eso se dice en voz alta en vez de callarlo.

## Probarlos sin credenciales

`pruebas/test_extractores.py` levanta un Graph API y un ghl-mcp de mentira con las
formas de respuesta REALES y comprueba que la salida del extractor es la que produjo el
snapshot ya verificado. No hace falta ningún token.

`pruebas/test_reintentos.py` reproduce los dos fallos que dejaron a 6 de 8 clientes sin
reporte el 11-sep-2026 — un `IncompleteRead` a media respuesta y un 429 de GoHighLevel
escondido dentro de un HTTP 200 — y comprueba que ahora se reintentan.

`pruebas/test_resistencia.py` levanta una Marketing API de mentira que se «agota» igual
que la de verdad y comprueba que los 120 días se recuperan partiendo el rango; que un
rango que no se puede servir **falla en voz alta en vez de devolver un agujero**; que un
token inválido no gasta reintentos; que varios clientes van de verdad en paralelo sin
perder ni repetir ninguno; y que el access token de Google se renueva antes de caducar.

Ninguna de las dos necesita red ni credenciales.
