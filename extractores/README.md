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
| `REPORTES_ADMIN_TOKEN` | `${{reportes.ADMIN_TOKEN}}` — por referencia, el valor no se copia. Es el token de **máquinas**: las personas entran a reportes por Sentinel Suite, los extractores no (van directos al dominio de Railway, que con `X-Admin-Token` no redirige a la suite) |
| `SOLO_CLIENTE` | opcional, un slug, para probar con uno solo |

`extractor-ghl` (y también el servicio `reportes`, que usa el mismo cliente para la ficha
de cada cliente y para el botón «Actualizar ahora»):

| Variable | Valor |
|---|---|
| `GHL_MCP_URL` | `https://sentinel-mcp-production-7a17.up.railway.app` — el **dominio propio** del servicio `sentinel-mcp` (proyecto Railway `mcp-ghl`). NO la ruta de la suite ni el dominio antiguo: ver «Cómo entra un programa al MCP». |
| `GHL_MCP_TOKEN` | un **token de servicio de Sentinel Suite** (`ss_…`): Administración → Integraciones → crear token con permiso `subaccounts:read`. Se enseña una sola vez. **Lo crea Luis a mano** y se pega en los dos servicios (no hay referencia `${{…}}` posible: el MCP vive en otro proyecto). |
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

### Cómo entra un programa al MCP (desde el 17-oct-2026)

Desde la fase E el MCP de GoHighLevel vive **detrás de Sentinel Suite**: la gente lo usa
en `https://suite.sentinelmarketing.net/mcp/ghl`, con OAuth de la suite, y el servicio
ya no tiene token propio (`GHL_MCP_HTTP_TOKEN` desapareció). Un programa no puede
entrar por ahí —el gateway exige una persona con sesión o un access token OAuth—, así
que los extractores y `reportes` entran por **otra puerta**, pensada para máquinas:

```
extractor-ghl / reportes ──Bearer ss_…──> https://sentinel-mcp-production-7a17.up.railway.app/mcp
                                                   │
                                                   └── GET suite/api/v1/subaccounts (con ese mismo token)
                                                       200 → token vivo + lista de subcuentas → solo lectura
```

- El token es un **token de servicio de la suite** (`ss_…`, permiso `subaccounts:read`).
  El MCP lo comprueba contra la suite en cada petición (con caché de un minuto) y le da
  **solo lectura sobre todas las subcuentas registradas** en la suite. Revocar el token
  en la suite corta el acceso.
- Solo ve las sub-cuentas **dadas de alta en Sentinel Suite** (Administración →
  Subcuentas, con su location id). Un cliente nuevo en reportes que no esté en la
  suite no aparece en la lista del panel ni se extrae: hay que registrarlo allí.
- El código del cliente (`comun/mcp.py`) no cambió de forma: sigue siendo `POST /mcp`
  con `Authorization: Bearer` y `tools/call` sin `initialize`. Lo que sí hace ahora es
  **explicar** los dos fallos de configuración en vez de dejar un «HTTP 308»:

| Síntoma en el log o en el panel | Qué pasa | Arreglo |
|---|---|---|
| `GHL_MCP_URL (…) redirige (HTTP 308)` | la URL es el dominio antiguo (`sentinel-mcp-bd11…` o `mcp.sentinelmarketing.net`), que manda a la suite | poner el dominio propio del servicio `sentinel-mcp` |
| `El MCP rechazó el token (HTTP 401)` | `GHL_MCP_TOKEN` no es un `ss_…` vivo (o el token lo revocaron en la suite) | crear otro token de servicio y pegarlo en `reportes` y `extractor-ghl` |
| `403 Invalid Host` | el dominio de `GHL_MCP_URL` no está en `ALLOWED_HOSTS` del servicio MCP | añadirlo allí (sin quitar lo que hay) |

Cambiar las variables en Railway (la carpeta de este repo está enlazada al proyecto):

```bash
railway variables --service reportes --set GHL_MCP_URL=https://sentinel-mcp-production-7a17.up.railway.app --set GHL_MCP_TOKEN=ss_…
```

```bash
railway variables --service extractor-ghl --set GHL_MCP_URL=https://sentinel-mcp-production-7a17.up.railway.app --set GHL_MCP_TOKEN=ss_…
```

Y para comprobarlo sin esperar al cron: `GET /admin/cuentas` del panel
(`https://suite.sentinelmarketing.net/reportes/admin`) tiene que listar las sub-cuentas
de GHL, y una ejecución manual del extractor con `SOLO_CLIENTE` tiene que terminar bien.

**Por qué el dominio público y no `*.railway.internal`:** la red privada de Railway solo
une servicios del **mismo** proyecto, y el MCP vive en el proyecto `mcp-ghl` mientras
que `reportes` y los extractores están en «GHL MCP + Claude». Además, el SDK de MCP
valida la cabecera `Host` contra `ALLOWED_HOSTS` del servicio: cualquier dominio por el
que se le llame tiene que estar ahí (comprobado levantando una réplica de su
`http-server.js` con el mismo SDK).

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

`pruebas/test_mcp_servicio.py` levanta una réplica del MCP tal y como responde desde la
fase E (308 en el dominio antiguo, 401 con un token que no es de servicio, SSE con uno
bueno) y comprueba que los dos fallos de configuración se explican en vez de salir como
«HTTP 308», que no se reintentan, y que con el dominio propio y un `ss_…` el
`tools/call` sigue funcionando.

`pruebas/test_reintentos.py` reproduce los dos fallos que dejaron a 6 de 8 clientes sin
reporte el 11-sep-2026 — un `IncompleteRead` a media respuesta y un 429 de GoHighLevel
escondido dentro de un HTTP 200 — y comprueba que ahora se reintentan.

`pruebas/test_resistencia.py` levanta una Marketing API de mentira que se «agota» igual
que la de verdad y comprueba que los 120 días se recuperan partiendo el rango; que un
rango que no se puede servir **falla en voz alta en vez de devolver un agujero**; que un
token inválido no gasta reintentos; que varios clientes van de verdad en paralelo sin
perder ni repetir ninguno; y que el access token de Google se renueva antes de caducar.

Ninguna de las dos necesita red ni credenciales.
