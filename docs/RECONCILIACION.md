# Reconciliación dentro de ROASLytics

Desde el 3-nov-2026 la reconciliación de cada cliente (cruce de pagos y de llamadas de
Google contra el CRM) **no se entrega en un Google Sheet**: se registra en ROASLytics,
en la pestaña «Reconciliación» del cliente, y ahí se decide, se cierra y queda el historial.

## Qué hay

| Dónde | Qué |
|---|---|
| Dashboard → pestaña **Reconciliación** | Para admin y equipo. Pendientes, decididas sin cerrar, historial, Excel, invitar a un vendedor. |
| `/reportes/s/<slug>/reconciliacion/` | La misma vista como página sola. Es lo único que ve el rol **vendedor**. |
| Dashboard → **Ajustes** → Ficha del cliente | La Ficha de 4 pestañas (datos de atribución, números, sources, pipelines). Solo admin. |
| MCP de la suite | Claude registra, comprueba, decide, cierra y lee/guarda la ficha (ver abajo). |

Roles en la suite para la herramienta `reportes`:

- **admin**: todo (también borrar lotes e invitar vendedores).
- **equipo**: registra lotes, decide, cierra, edita la ficha.
- **vendedor**: solo la reconciliación de las subcuentas que se le asignen; marca SI / NO /
  DUDA y escribe notas. No cierra, no ve el dashboard ni el snapshot.

Para dar acceso a un vendedor: en la pestaña Reconciliación → «Dar acceso a un vendedor»
(correo + nombre), o en Suite → Administración → Personas: herramienta ROASLytics, rol
`vendedor`, «Solo estas» subcuentas.

## El modelo

**Lote** = una reconciliación: `titulo`, `periodo_desde`, `periodo_hasta`, `criterio`
(Ventas | Pagos), `plataforma` (Fresha, Stripe…), `cohorte`, `resumen`, `total_export`,
`numero_control`. Se cierra solo cuando no le queda ninguna fila pendiente.

**Item** = una fila a decidir. `tipo`:

| tipo | Qué es | ¿Se decide? |
|---|---|---|
| `valor` | Opp Value vs la plataforma (regla del 15 %) | Sí (SI/NO pre-rellenado) |
| `source` | Llamada de Google Ads que cambia el source | Sí |
| `cobro_sin_oportunidad` | Pago que no cruza con nadie | No (informativo) salvo que traiga `propuesta` |
| `bloqueado` | Solo nombre de pila: no se cruza | No |
| `factura_cero` | Factura en 0 | No |
| `llamada_sin_contacto` | Teléfono que llamó y no existe en el CRM | No |
| `fuera_periodo` | Contacto anterior al periodo | No |
| `otro` | | Según `propuesta` |

Campos: `contacto` (nombre en GHL), `contact_id`, `opp_id`, `cliente_plataforma`,
`telefono`, `email`, `opp_creada`, `contacto_alta`, `estado_opp` (open/won), `etapa`,
`valor_plataforma`, `valor_ghl`, `diferencia_pct` (lo calcula el servidor),
`source_actual`, `source_propuesto`, `campana`, `fecha_evento`, `propuesta` (acción en
positivo), `cambios` (p. ej. `{"monetaryValue": 520, "status": "won", "stage": "Won Deals"}`),
`evidencia`, `cruce_por`, `propuesta_decision`.

Estados: `abierto` → `decidido` (SI/NO/DUDA, nota, quién, cuándo) → `cerrado` (resultado,
quién, cuándo). Los informativos nacen cerrados.

**Lo ya reconciliado**: cada fila lleva `claves` (`opp:`, `contacto:`, `tel:` sin +1,
`email:`). Al registrar un lote, una fila del mismo `tipo` sobre la misma oportunidad o
contacto ya existente **se omite** (sale en `omitidos` con lo que se decidió entonces),
salvo `forzar: true`.

## API (por el gateway de la suite, `/reportes` delante)

```
GET    /s/<slug>/reconciliacion/api?estado=pendiente|abierto|decidido|cerrado|todos&lote=<id>
GET    /s/<slug>/reconciliacion/api/comprobar?opps=a,b&contactos=x&telefonos=..&emails=..
POST   /s/<slug>/reconciliacion/api/lotes            {lote:{…}, items:[…], forzar?}
POST   /s/<slug>/reconciliacion/api/items/<id>/decidir   {decision, nota?}
POST   /s/<slug>/reconciliacion/api/items/<id>/cerrar    {resultado?}
POST   /s/<slug>/reconciliacion/api/items/<id>/reabrir
POST   /s/<slug>/reconciliacion/api/lotes/<id>/cerrar    {resultado?, solo_decision?}
DELETE /s/<slug>/reconciliacion/api/lotes/<id>           (admin)
GET    /s/<slug>/reconciliacion/export.xlsx?lote=<id>
GET    /s/<slug>/api/ficha        PUT /s/<slug>/api/ficha  (la ficha entera)
```

## Cómo reconcilia Claude ahora (para la skill `atribucion-reconciliacion`)

Texto para pegar en la skill, sustituyendo el bloque «Cómo se entrega»:

> **El entregable ya no es un Google Sheet: es la pestaña Reconciliación del cliente en
> ROASLytics**, a la que se escribe con el MCP de la suite.
>
> 1. Lee la Ficha con `reportes_ficha` (criterio Ventas/Pagos, app de pagos, pipeline y
>    etapa de cierre). Si falta el criterio, pregunta antes de cruzar.
> 2. Antes de proponer nada, pasa los contactos/oportunidades por
>    `reconciliacion_comprobar`: lo que ya se reconcilió (con su decisión) no se vuelve a
>    proponer; dilo en el resumen.
> 3. Registra la reconciliación con `reconciliacion_registrar`: un lote (periodo,
>    criterio, plataforma, total del export, resumen con el cuadre) y una fila por
>    registro del export, cada una en su `tipo`. Las filas de valor llevan
>    `valor_plataforma`, `valor_ghl`, `opp_creada`, `estado_opp` y `evidencia`
>    (por qué campo cruzó y con qué fechas); el servidor calcula la diferencia y
>    pre-rellena SI/NO con la regla del 15 %. Las informativas (cobros sin oportunidad,
>    bloqueados, fuera del periodo…) también se registran: así el cuadre contra el total
>    del export sale exacto.
> 4. Reporta en el chat el resumen (dinero real, lo que hay que escribir, lo que no se
>    toca, lo que queda sin resolver, cuadre) y el enlace a la pestaña. **No escribas en
>    el CRM todavía.**
> 5. Quien decide (Luis o un vendedor con acceso) marca SI / NO / DUDA en la pestaña.
>    Cuando te lo pidan, lee lo decidido con `reconciliacion_pendientes`
>    (`estado: "decidido"`) y aplica en GoHighLevel **solo lo marcado SI** (simulación,
>    contraste, escritura real).
> 6. Cierra cada fila aplicada con `reconciliacion_cerrar` (o el lote entero con
>    `solo_decision: "SI"`), anotando en `resultado` qué se escribió. Lo marcado NO se
>    cierra con «sin cambios». Así queda el historial.
>
> El Excel sigue existiendo: «Descargar Excel» en la pestaña saca el libro con el formato
> de siempre, por si hay que mandárselo a alguien que no entra en la suite.

Y en `atribucion-onboarding`, paso 1.1: la Ficha se rellena con `reportes_ficha_guardar`
(leer primero con `reportes_ficha`; el PUT reemplaza la ficha entera) en vez de duplicar
el template de Drive. Los datos de identidad (nombre, locationId, zona horaria, cuentas de
anuncios) no se escriben ahí: vienen de la suite.
