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

## Probarlos sin credenciales

`pruebas/test_extractores.py` levanta un Graph API y un ghl-mcp de mentira con las
formas de respuesta REALES y comprueba que la salida del extractor es la que produjo el
snapshot ya verificado. No hace falta ningún token.
