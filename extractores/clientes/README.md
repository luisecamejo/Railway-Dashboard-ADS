# clientes/

Un fichero por cliente: `SLUG.json`. Es el **valor inicial** de su configuración de
construcción, no la configuración viva.

La viva la guarda el servicio `reportes` y se puede cambiar sin desplegar. Es decir: un
fichero de aquí puede quedarse desactualizado sin que nada se rompa. Para saber qué hay
de verdad en el servicio:

```bash
python operar.py config SLUG        # lo que el servicio tiene guardado
python operar.py estado             # todos los clientes, y a cuál le falta
```

Y para dejar el fichero en el servicio:

```bash
python operar.py config SLUG clientes/SLUG.json
```

## Campos

| Campo | Quién lo consume | Si falta |
|---|---|---|
| `slug`, `nombre` | el reporte | el reporte sale sin nombre de negocio |
| `tz` | los tres extractores y `construir.py` | **el cliente se salta**: sin zona horaria las fechas no cuadran con el CRM |
| `tzFuente` | nadie, es una nota | nada; sirve para saber de dónde salió la zona |
| `ghlLocationId` | `extractor-ghl` | ese cliente se salta en el CRM |
| `cuentas[]` | `extractor-meta` y `extractor-google` | ese extractor no tiene nada que hacer con él (no es un error) |
| `productos[]`, `productosPorTag` | `construir.py` | los leads salen sin producto atribuido |
| `roles` | `construir.py` | el Call Report sale sin rol por persona |
| `sop` | `construir.py` | el dashboard pierde tiempo límite y cadencia por etapa |

`desde` y `hasta` NO van aquí: los calcula cada extractor en la zona del negocio, en
cada ejecución. Dejarlos escritos invita a que un día se queden congelados y el reporte
muestre una ventana que ya no es la de los datos.

## Dar de alta un cliente nuevo

1. Copia el JSON de otro cliente y cambia `slug`, `nombre`, `tz`, `ghlLocationId` y
   `cuentas`. Los `productos`, `roles` y `sop` son decisiones de negocio: no se heredan.
2. `POST /admin/clientes` para crearlo en el servicio (o el panel de administración).
3. `python operar.py config SLUG clientes/SLUG.json`
4. Deja que corran los extractores, o corre `python operar.py construir SLUG --ensayo`
   y mira el resumen antes de publicar nada.
