# extractor-google

`extraer.py` está hecho y probado (`pruebas/test_google.py`, 10 comprobaciones sin
credenciales ni red: reproduce las 204 filas y los 4.139,37 de gasto del trozo ya
validado).

## Lo que hay montado (2 sep 2026)

| Qué | Valor |
|---|---|
| Cuenta MCC | **Sentinel Marketing, LLC · `769-924-3841`** |
| Developer token | ya existía, con **Explorer Access** concedido |
| Cuenta de Google | `luis@sentinelmarketing.net` |
| Proyecto de Google Cloud | `servidor-railway-dashboard` |
| Cliente OAuth | «extractor-google (Railway)», tipo *Aplicación web* |
| Pantalla de consentimiento | tipo **Internal** |
| Redirect autorizado | `https://developers.google.com/oauthplayground` |

Las 10 cuentas de la cartera cuelgan de esa MCC, así que **un solo developer token
sirve para todas**. Lo único que cambia por cliente es su `customer_id`, que se escribe
en el panel de administración (que admite varias cuentas de Google por cliente).

| Cliente | `customer_id` |
|---|---|
| Aesthetics by Cliff | 580-642-2100 |
| BeVisionary | 484-287-4719 |
| Brightnest Services | 755-536-6682 |
| CPR Car Care | 806-243-8733 |
| Enroll America | 284-624-3843 |
| Garage Door Experts | 985-244-7343 |
| Golden Rose Wellness | 856-535-3498 |
| Oreamuno's Contractors | 228-015-4054 |
| Respira Libre | 120-114-2299 |
| Sentinel Marketing (cancelada) | 774-938-2990 |

## Variables

| Variable | Ámbito | Dónde sale |
|---|---|---|
| `GOOGLE_DEVELOPER_TOKEN` | **una para toda la agencia** | API Center de la MCC |
| `GOOGLE_CLIENT_ID` | una para todos | Cloud → Clients (es público, no es secreto) |
| `GOOGLE_CLIENT_SECRET` | una para todos | Cloud → Clients |
| `GOOGLE_REFRESH_TOKEN` | uno | OAuth Playground, una sola vez |
| `GOOGLE_LOGIN_CUSTOMER_ID` | `769-924-3841` | el id de la MCC, sin guiones al usarlo |
| `GOOGLE_API_VERSION` | `v25` | se sube cuando Google jubile la versión |

## Dos trampas que costaron media hora encontrar

**1 · El API Center solo existe en la MCC, y hay dos cuentas con el mismo nombre.**
Entrando en una cuenta hija sale *«The API Center is only available to manager
accounts»*, que es fácil de leer como «no tengo MCC» cuando sí la hay. Y en esta
agencia existen **dos** cuentas llamadas «Sentinel Marketing, LLC»: la `774-938-2990`
(cancelada, **no** es manager) y la `769-924-3841` (la manager). Además, la cuenta de
Gmail personal solo ve BeVisionary y no tiene ninguna MCC: hay que entrar con
`luis@sentinelmarketing.net`.

**2 · Para VER el token, Google exige passkey y no ofrece alternativa.** Su propio
diálogo da la salida buena:

> You won't be able to continue without confirming it's you, but you can:
> · Ask **another account user** to make the change

El token es de la **cuenta**, no de la persona: cualquier admin de la MCC ve el mismo.
En *Admin → Access and security → Users* hay una columna **Passkey status** que dice
quién puede hacerlo hoy.

## Renovar el refresh token, si alguna vez falla

1. [OAuth Playground](https://developers.google.com/oauthplayground) → rueda de ajustes
2. Marcar **Use your own OAuth credentials**, pegar Client ID y Client secret
3. Comprobar **Access type: Offline** y **Force prompt: Consent Screen** — sin eso
   Google devuelve solo un access token de una hora y no un refresh token
4. Scope: `https://www.googleapis.com/auth/adwords` → *Authorize APIs*
5. *Exchange authorization code for tokens* → el **Refresh token** es el que empieza
   por `1//`, no el access token

El código de autorización del paso 4 caduca en minutos: hay que hacer el canje seguido.
Y un cliente OAuth recién creado tarda entre 5 minutos y unas horas en propagarse; si
sale `redirect_uri_mismatch` o `invalid_client` recién creado, es eso y no un error de
configuración.

La pantalla de consentimiento es **Internal** a propósito: con **External** + estado
**Testing**, Google emite refresh tokens que **caducan a los 7 días** (está en su
documentación). Se montaría, funcionaría, y el martes siguiente a las 3 de la mañana
dejaría de funcionar sin que nadie hubiera tocado nada.

## Si faltan credenciales

El extractor **falla en rojo y nombra la variable que falta**, en vez de callarse:

```
ValueError: Faltan credenciales de Google Ads: GOOGLE_DEVELOPER_TOKEN, ...
```

Es a propósito. Si devolviera un trozo vacío, el snapshot se publicaría con el gasto de
Google a **cero** y el CPL mezclado saldría mejor de lo real sin que nadie se enterara.
En Aesthetics by Cliff, Google son ~4.073 de 17.000: el **24% del gasto**.
`pruebas/test_google.py` fija ese comportamiento para que no se «arregle» por error.

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
