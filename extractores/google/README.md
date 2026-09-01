# extractor-google

Todavía no existe `extraer.py`. Lo que falta no es código: son credenciales.

## Las cuatro piezas, y cuál sirve para todos los clientes

| Pieza | Ámbito | Dónde se saca |
|---|---|---|
| **Developer token** | **UNO para todos los clientes** | API Center de una cuenta MCC (manager) de Google Ads |
| OAuth client id + secret | uno para todos | un proyecto de Google Cloud, con la Google Ads API activada |
| Refresh token | uno por usuario de Google | consentimiento OAuth una sola vez, con el usuario que tenga acceso a las cuentas |
| `customer_id` de cada cuenta | por cliente | ya está en `clientes/SLUG.json` → `cuentas[].id` |

Esto es lo importante de la tabla: **la espera del token no se multiplica por
cartera.** Se pide una vez, sobre una MCC, y a partir de ahí añadir un cliente es
añadir su `customer_id` a su fichero de configuración. No hay nada que montar por
cliente.

Al leer una cuenta a través de la MCC hay que mandar la cabecera
`login-customer-id` con el id de la MCC, sin guiones. Un `customer_id` con guiones
(`580-642-2100`) se manda sin ellos (`5806422100`); el extractor lo normalizará.

## Niveles de acceso del developer token

| Nivel | Qué permite |
|---|---|
| Test Account | solo cuentas de prueba. No sirve: hay que leer cuentas reales. |
| Explorer | cuentas reales, con tope diario de operaciones. Suele aprobarse solo. |
| Basic / Standard | topes altos o sin tope. Requiere solicitud y revisión. |

Explorer basta para esto: la extracción diaria son un puñado de consultas GAQL por
cuenta, no miles.

## Mientras el token no se pueda ver

Google exige verificación en dos pasos para mostrar el token en el API Center, y
recién cambiada la seguridad de la cuenta impone una espera. Tres salidas, de mejor
a peor:

1. **Otra cuenta de Google que ya tenga 2FA.** Se le da acceso de administrador a la
   MCC (o crea una MCC nueva y se le vincula la cuenta del cliente) y saca el token
   desde ahí. Es la única que resuelve el problema de raíz, sirve para todos los
   clientes y no deja nada que desmontar después.
2. **Verificación en dos pasos con app de autenticación** en vez de passkey, si la
   cuenta lo admite. Son dos minutos y puede que levante el bloqueo hoy mismo; hay
   que probarlo, no se puede dar por hecho.
3. **Informe programado de Google Ads a Google Sheets**, y leer la hoja. Funciona,
   pero es la peor: hay que montarlo **cuenta por cuenta**, se rompe si alguien
   toca la hoja, y el trabajo crece con la cartera en vez de resolverse una vez.
   Solo como puente de unos días.

Mientras tanto el reporte no se queda roto: si un cliente no tiene trozo de Google,
`reportes` construye con lo que hay y el gasto de Google sale a cero, con aviso.

## Lo que tendrá que devolver

El mismo trozo que ya consume `construir.py`, con `red: "Google"`:

```json
{
  "gastoDiario": [
    {"fecha": "2026-08-30", "red": "Google", "campana_id": "...",
     "campana": "...", "spend": 12.34, "impressions": 100, "clicks": 5,
     "conversiones": 1}
  ]
}
```

Una fila por día y campaña (`segments.date`), nunca un bloque de varios días: el
servicio rechaza con 422 una fila cuyo `hasta` no sea igual a su `fecha`. Y la
ventana se calcula en la zona horaria del negocio, con `comun.fechas.ventana`, igual
que en Meta.
