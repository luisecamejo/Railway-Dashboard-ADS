# ROASLytics · identidad visual

ROASLytics es el nombre de marca de los dashboards de adquisición de Sentinel Marketing
(el servicio `reportes`). Vive dentro de Sentinel Suite en `/reportes`, pero tiene nombre y
marca propios: los clientes lo ven empotrado en su GoHighLevel y conviene que suene a producto,
no a «panel interno».

## Marca

- `marca.svg`: el símbolo solo (cuadrado redondeado con degradado y, en blanco, tres barras
  que suben con la línea de retorno por encima). Sirve de favicon, avatar e icono de la
  tarjeta en la suite (clave `roaslytics` en `public/icons.js` de la suite, versión de trazo).
- `logo.svg`: símbolo + nombre. «ROAS» en negrita y «Lytics» en peso medio y color menta.

El nombre se escribe siempre **ROASLytics** (ROAS en mayúsculas, L mayúscula, el resto en
minúsculas). Nunca «Roaslytics» ni «ROASlytics».

## Colores

| Uso | Color | Dónde |
|---|---|---|
| Menta (acento de marca) | `#2DD4A8` | «Lytics», icono y color de la tarjeta en la suite, botones primarios del panel |
| Violeta (acento de datos) | `#7C3AED` / claro `#A855F7` | Lo que ya usaba el dashboard: KPIs, selección, enlaces. Se mantiene |
| Degradado de la marca | violeta → menta, de abajo-izquierda a arriba-derecha | Solo en el símbolo |
| Fondo | `#0A0A0F` (dashboard) · `#0B0F14` (panel) | Como hasta ahora |
| Texto | `#EDEDF5` | Como hasta ahora |

## Tipografía

- Titulares y wordmark: **Montserrat** 800 (ya cargada en el dashboard).
- Datos y etiquetas pequeñas: **JetBrains Mono** (ya cargada).

## Dónde se aplica

- Sentinel Suite: nombre, icono, color y descripción de la herramienta `reportes` (migración
  en `src/db.ts`); la tarjeta de la portada y la barra común lo enseñan solos.
- Dashboard (`web/visor/partes/01.part`): cabecera con símbolo + wordmark y «por Sentinel
  Marketing» (o la agencia del snapshot); favicon en línea.
- Panel (`web/app/plantillas/panel/00.part`): título y cabecera.
- Páginas de aviso (`_pagina` en `web/app/main.py`): mismo favicon.
