# Comprobar las credenciales de Google Ads por separado

## Una variable vacía es lo mismo que no estar

Pasó el 2 de septiembre de 2026: se creó `GOOGLE_DEVELOPER_TOKEN` en Railway pero con
el valor **vacío**. Desde fuera parece puesta, porque la lista de variables de Railway
(y el propio panel) muestran **nombres**, no valores.

El extractor lo dice claro, porque comprueba el valor y no la existencia:

```
ValueError: Faltan credenciales de Google Ads: GOOGLE_DEVELOPER_TOKEN.
```

Así que **el nombre en la lista no prueba nada**. Lo que prueba algo es el log.

## Las otras tres se pueden validar sin el developer token

El canje de refresh token por access token va contra `oauth2.googleapis.com` y **no usa
el developer token**. O sea que se puede comprobar que `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET` y `GOOGLE_REFRESH_TOKEN` son correctos aunque falte la cuarta —
y así, cuando llegue, se sabe que no hay nada más roto.

Comando de inicio temporal para el servicio `extractor-google` (no imprime ningún
secreto: solo longitudes):

```python
python -c "
import os, sys
os.environ['GOOGLE_DEVELOPER_TOKEN'] = os.environ.get('GOOGLE_DEVELOPER_TOKEN') or 'PENDIENTE'
sys.path.insert(0, '/app')
from google.extraer import Credenciales
c = Credenciales()
print('secret  ', len(c.client_secret), 'caracteres' if c.client_secret else 'VACIO')
print('refresh ', len(c.refresh_token), 'caracteres' if c.refresh_token else 'VACIO')
try:
    t = c.access_token()
    print('OAUTH OK: access token de', len(t), 'caracteres')
except Exception as ex:
    print('OAUTH FALLA:', ex)
"
```

Después hay que devolver el comando de inicio a `python -m google.extraer`.

## Qué significa cada fallo

| En el log | Qué pasa |
|---|---|
| `Faltan credenciales de Google Ads: X` | esa variable está vacía o no existe |
| `invalid_grant` | el refresh token ya no vale: se revocó, o se emitió con la pantalla de consentimiento en *External + Testing* y **caducó a los 7 días**. Hay que sacar otro |
| `invalid_client` | el client id o el secret no cuadran, o el cliente OAuth es demasiado nuevo (tarda entre 5 min y unas horas en propagarse) |
| `DEVELOPER_TOKEN_NOT_APPROVED` | el token existe pero su nivel no permite cuentas reales. Hace falta **Explorer Access** como mínimo; *Test Account* no sirve |
| `USER_PERMISSION_DENIED` | el usuario del refresh token no tiene acceso a esa cuenta, o falta `login-customer-id` con el id de la MCC |
| `CUSTOMER_NOT_ENABLED` | la cuenta está cancelada o suspendida (ojo con la 774-938-2990) |

## En el OAuth Playground, Step 3 no se toca

Step 3 («Configure request to API») es para lanzar llamadas de prueba a mano. Lo que se
necesita está en **Step 2**: el **Refresh token**, que empieza por `1//`. El *access
token* de al lado dura una hora y no sirve para esto.
