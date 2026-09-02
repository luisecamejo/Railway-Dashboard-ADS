# extractor-google

`extraer.py` **ya está hecho y probado** (`pruebas/test_google.py`, 10 comprobaciones
sin credenciales ni red: reproduce las 204 filas y los 4.139,37 de gasto del trozo ya
validado). Lo que falta son las credenciales.

## Las cuatro piezas, y cuál sirve para todos los clientes

| Variable | Ámbito | Dónde se saca |
|---|---|---|
| `GOOGLE_DEVELOPER_TOKEN` | **UNA para toda la agencia** | API Center de una cuenta MCC de Google Ads |
| `GOOGLE_CLIENT_ID` | una para todos | Google Cloud → Credenciales → ID de cliente OAuth |
| `GOOGLE_CLIENT_SECRET` | una para todos | lo mismo |
| `GOOGLE_REFRESH_TOKEN` | uno por usuario de Google | el consentimiento OAuth, una sola vez |
| `GOOGLE_LOGIN_CUSTOMER_ID` | el id de la MCC | solo si se entra a las cuentas a través de ella |
| `GOOGLE_API_VERSION` | `v25` | se sube cuando Google jubile la versión |

Lo importante: **la espera del token no se multiplica por cartera.** Se pide una vez
sobre una MCC, y a partir de ahí añadir un cliente es escribir su `customer_id` en el
panel. No hay nada que montar por cliente.

El `customer_id` de cada cuenta vive en la configuración del cliente
(`cuentas[] → {plataforma: "Google", id: "580-642-2100"}`) y se pone desde el panel de
administración, que admite **varias cuentas de Google por cliente**.

## Los pasos, en orden

**1 · Developer token.** Google Ads → la cuenta **MCC** → Herramientas → Configuración
→ **API Center**. Ahí se solicita y ahí se ve. Hace falta verificación en dos pasos en
la cuenta de Google para poder mostrarlo; si la cuenta acaba de cambiar su seguridad,
Google impone una espera. Salida rápida: hacerlo desde **otra cuenta de Google que ya
tenga 2FA** y que sea administradora de la MCC.

Niveles de acceso: **Explorer** (se aprueba solo y permite cuentas reales con un tope
diario de operaciones) basta — la extracción diaria son un puñado de consultas por
cuenta. **Test Account** NO sirve: solo lee cuentas de prueba.

**2 · Proyecto de Google Cloud.** console.cloud.google.com → crear proyecto →
APIs y servicios → **habilitar "Google Ads API"**.

**3 · Pantalla de consentimiento.** APIs y servicios → Pantalla de consentimiento OAuth.
Añadir el permiso `https://www.googleapis.com/auth/adwords`.

> **Esto es lo que rompe el trabajo nocturno una semana después.** Con tipo
> **External** y estado **Testing**, Google emite refresh tokens que **caducan a los 7
> días** (está en su documentación). Se monta, funciona, y el martes siguiente a las 3
> de la mañana deja de funcionar sin que nadie haya tocado nada. Con tipo **Internal**
> (posible si el dominio está en Google Workspace) no aplica: la regla que Google
> enuncia acota los 7 días a "external". La otra salida es publicar la app.

**4 · ID de cliente OAuth.** Credenciales → Crear credenciales → ID de cliente OAuth →
tipo **Aplicación web** → en URIs de redireccionamiento autorizados añadir
`https://developers.google.com/oauthplayground`. De ahí salen `client_id` y
`client_secret`.

(Tipo *Aplicación web* y no *Aplicación de escritorio* solo para poder usar el
Playground del paso 5 sin instalar nada en local.)

**5 · Refresh token.** developers.google.com/oauthplayground → rueda de ajustes →
marcar **"Use your own OAuth credentials"** → pegar `client_id` y `client_secret` →
en el campo de permisos escribir `https://www.googleapis.com/auth/adwords` →
*Authorize APIs* → iniciar sesión con la cuenta que tiene acceso a las cuentas de
Google Ads → *Exchange authorization code for tokens*. El **refresh token** es el que
se guarda.

**6 · Railway.** Servicio `extractor-google` → Variables → las cinco de arriba.
Ninguna se pega en un chat: se escriben directamente ahí.

## Mientras falten

El extractor **falla en rojo y nombra la variable que falta**, en vez de callarse:

```
ValueError: Faltan credenciales de Google Ads: GOOGLE_DEVELOPER_TOKEN, ...
```

Eso es a propósito. Si devolviera un trozo vacío, el snapshot se publicaría con el
gasto de Google a **cero** y el CPL mezclado saldría mejor de lo real sin que nadie se
enterara. `pruebas/test_google.py` fija ese comportamiento para que no se "arregle"
por error más adelante.

Y el reporte no se queda roto: si un cliente no tiene trozo de Google, `reportes`
construye con lo que hay. Pero **ojo con publicar así**: en Aesthetics by Cliff, Google
son ~4.073 de 17.000, el **24% del gasto**. Un reporte sin eso enseña un CPL mezclado
un 24% mejor que el real.

## Lo que produce

```json
{"gastoDiario": [
  {"fecha": "2026-08-30", "campana_id": "23003134547", "campana": "...",
   "red": "Google", "spend": 11.2574, "impressions": 323, "clicks": 6,
   "conversiones": 0.9964}
]}
```

Una fila por día y campaña (`segments.date`), nunca un bloque de varios días: el
servicio rechaza con 422 una fila cuyo rango cubra más de una jornada. La ventana se
calcula en la zona horaria del negocio con `comun.fechas.ventana`, igual que en Meta.

## Trampas de esta API

- **Los enteros llegan como texto.** El JSON de protobuf serializa int64 entre comillas:
  `costMicros`, `impressions`, `clicks` e `id`. Sin convertir, el gasto se concatena.
- **El gasto viene en micros.** 11.257.400 micros son 11,2574. Dividir por 100 en vez
  de por un millón da cifras *verosímiles* y equivocadas, que es peor que un absurdo
  evidente porque nadie lo mira dos veces.
- **Las conversiones son decimales**: Google reparte una conversión entre varios clics,
  así que un día puede traer 0,9964. No se redondea por día.
- **El `customer_id` va sin guiones** en la URL y en `login-customer-id`. Se guarda con
  guiones (así lo lee una persona) y se limpia al usarlo.
