# Referencia de diseño de interfaz (fuente de verdad visual)

Estas 6 pantallas las entregó el usuario como diseño definitivo. Se implementan con fidelidad. Si algo entra en conflicto con `requirements.md` o `design.md`, NO se decide solo: se anota en "Diferencias por resolver" y se pregunta al usuario.

Las imágenes originales están en `.kiro/specs/skincare-voice-advisor/design/` (a 1x; el Kiosco mide 1194×834 y la Caja 390×844):

- `design/1 · Inicio@1x.png` (pantalla 1)
- `design/2 · Conversación@1x.png` (pantalla 2)
- `design/3 · Rutina y QR@1x.png` (pantalla 3)
- `design/4 · Caja acceso@1x.png` (pantalla 4)
- `design/5 · Caja leer QR@1x.png` (pantalla 5)
- `design/6 · Caja lista de productos@1x.png` (pantalla 6)

Al implementar, abrir la imagen de la pantalla correspondiente y compararla. Detalles visibles en las imágenes:

- En la pantalla 1 el numeral `4` se ve más fino que los demás: es la forma del glifo en una serif de alto contraste (diagonal en trazo capilar), no un estilo distinto. Se logra usando la tipografía serif, sin tratamiento especial.
- En la pantalla 3, el encabezado `4 PROTECCIÓN SOLAR` ocupa dos líneas y desplaza hacia abajo el contenido de esa tarjeta respecto a las otras tres. Es como aparece en el diseño; al implementar conviene preguntar si se quiere alinear.
- El QR y el código de las imágenes son de ejemplo (`[ABC-123]`, con el código en monoespaciada y mayúsculas con interletrado)..png`

## Estilo común

- Fondo gris verdoso muy claro (aprox. `#EEF0EC`), tarjetas blancas sin sombra marcada, separadores de 1 px.
- Verde oscuro de acción (aprox. `#1E4A3E`), casi negro verdoso para textos y botón secundario sólido (aprox. `#14211C`), rojo apagado para "Colgar" (aprox. `#9B2C2C`), gris verdoso para placeholders de imagen (aprox. `#DCE2DC`).
- Esquinas rectas (sin radio) en botones, tarjetas y campos.
- Titulares en serif de alto contraste tipo Didot/Bodoni, tamaño grande, interlineado ajustado. Texto de cuerpo en sans geométrica neutra.
- Rótulos pequeños en MAYÚSCULAS con interletrado amplio (ej. `ASESORÍA POR VOZ`, `CÓMO FUNCIONA`, `TRANSCRIPCIÓN`).
- Encabezado: `GRUPO ULTRA` en negrita con interletrado, seguido de `· ASESOR DE SKINCARE` (Kiosco) o `· CAJA` (Caja) en gris.
- Los valores de color y las fuentes son aproximados a partir de las imágenes. Si existen los tokens reales (Figma, guía de marca) o los archivos de fuente, deben usarse en su lugar.

## Kiosco (horizontal, tablet)

### Pantalla 1: Inicio (estado `idle`)

- Encabezado con selector de idioma `ES | EN` arriba a la derecha (el activo en fondo oscuro, texto blanco).
- Columna izquierda: rótulo `ASESORÍA POR VOZ`; titular `Cuéntanos qué busca tu piel.`; texto `Conversa con nuestro asesor y recibe una rutina con productos de la tienda, lista para llevar a caja.`
- Botón ancho verde `Iniciar conversación` con icono de micrófono, abajo a la izquierda.
- Debajo del botón, aviso pequeño: `La conversación es anónima: no pedimos tu nombre. [AVISO DE USO DE DATOS DE GRUPO ULTRA]` (el texto entre corchetes es un marcador pendiente del aviso real).
- Columna derecha: rótulo `CÓMO FUNCIONA` y 4 tarjetas blancas en cuadrícula 2×2, cada una con numeral grande en serif (1 a 4), icono lineal arriba a la derecha, título en negrita y descripción:
  1. `Toca «Iniciar conversación»` / `Permite el uso del micrófono cuando la tablet lo pida.`
  2. `Habla con el asesor` / `En español o en inglés. Te hará unas preguntas sobre tu piel.`
  3. `Recibe tu rutina y un código QR` / `Aparecen en esta pantalla, con un producto por cada paso.`
  4. `Toma foto del QR y llévala a caja` / `Al terminar toca «Colgar» para cerrar la conversación.`

### Pantalla 2: Conversación (estado `active`)

- Encabezado: a la derecha, punto verde + `Conversación en curso · Español` (el idioma vigente).
- Enlace pequeño subrayado arriba a la derecha de la transcripción: `Demo: ver rutina →` (solo para el prototipo; se oculta en producción).
- Columna izquierda: rótulo `TE ESTAMOS ESCUCHANDO`; tarjeta blanca con onda de audio en barras verticales verdes; titular serif `Habla con naturalidad, como con un asesor en tienda.`
- Botones abajo: `Silenciar` (contorno, icono de micrófono tachado) y `Colgar` (rojo sólido, icono de teléfono). Debajo: `Al colgar se cierra la conversación y la pantalla vuelve al inicio.`
- Columna derecha: tarjeta `TRANSCRIPCIÓN` con mensajes. `ASESOR` en rótulo verde y texto alineado a la izquierda. `TÚ` en rótulo gris y texto con sangría. El mensaje en curso del usuario se muestra en gris claro con puntos suspensivos.
- Pie de la tarjeta: `¿Hay mucho ruido? También puedes escribir`, campo `Escribe tu mensaje` y botón oscuro `Enviar`.

### Pantalla 3: Tu rutina

- Encabezado: a la derecha, punto verde + `El asesor sigue escuchando` y botón rojo `Colgar`.
- Título serif `Tu rutina` y, a la derecha, `Un producto del catálogo por cada paso`.
- 4 tarjetas blancas en fila, cada una con numeral serif + paso en mayúsculas (`1 LIMPIEZA`, `2 TRATAMIENTO`, `3 HIDRATACIÓN`, `4 PROTECCIÓN SOLAR`), separador, imagen del producto (placeholder gris con el texto `Imagen del producto (columna «Imagen» del CSV)`), `[MARCA]` en mayúsculas pequeñas, nombre del producto en negrita, razón tomada del catálogo, `SKU [000000]` y precio `$[precio]` en la misma línea, y botón de contorno `Ver modo de uso`.
- Columna derecha, tarjeta oscura: `PARA LA CAJA`, QR sobre fondo blanco, `Código` y `[ABC-123]` grande en monoespaciada, `Toma una foto de este código y muéstrala en caja.`
- Debajo, tarjeta blanca opcional: `OPCIONAL · LECTURAS SOBRE [INGREDIENTE]` con 2 títulos de artículo y la leyenda `Fuente: PubMed (NCBI). Información general, no es consejo médico.`
- Pie: `Si quieres saber cómo combinar los productos, consulta a un asesor de la tienda. Puedes seguir preguntando por voz antes de colgar.`

## Caja (vertical, móvil)

### Pantalla 4: Acceso de caja

- Encabezado `GRUPO ULTRA · CAJA`. Titular serif `Acceso de caja`; texto `Entra con el usuario del dispositivo para consultar las rutinas recomendadas.`
- Campos `Usuario` y `Contraseña` (puntos), botón verde ancho `Entrar`.
- Pie: `Uso interno. Si no puedes entrar, pide apoyo a [RESPONSABLE DEL PILOTO].` (marcador pendiente).

### Pantalla 5: Lee el QR del cliente

- Encabezado con enlace subrayado `Salir` a la derecha. Titular serif `Lee el QR del cliente`.
- Visor oscuro con recuadro punteado centrado y la leyenda `Vista de la cámara · apunta al código`.
- Botón verde `Escanear con la cámara` (icono de cámara); botón de contorno `Subir la foto del QR` (icono de subida).
- Al fondo: `O escribe el código que aparece bajo el QR`, campo con placeholder `ABC-123` y botón oscuro `Buscar`.

### Pantalla 6: Recomendación

- Encabezado con `Salir`. Rótulo `Recomendación`, código en serif grande `[ABC-123]` y, a la derecha, etiqueta de contorno `PENDIENTE`.
- Línea `4 productos · generada [fecha y hora]`.
- Lista en tarjeta blanca de 4 filas: miniatura de imagen, paso en mayúsculas pequeñas (`LIMPIEZA`, `TRATAMIENTO`, `HIDRATACIÓN`, `PROTECCIÓN SOLAR`), nombre en negrita, `SKU [000000]` y precio `$[precio]` a la derecha.
- Botón verde ancho `Marcar como atendida` y botón de contorno `Leer otro QR`.

## Diferencias por resolver con el usuario

Los diseños agregan o cambian cosas que no están en los requerimientos. Hasta que el usuario decida, se implementa el diseño tal cual y se anota aquí.

| # | Diseño | Requerimiento o diseño técnico | Propuesta / Decisión |
|---|---|---|---|
| 1 | Pantalla de inicio con selector `ES \| EN` y 4 pasos (pantalla 1). | No hay pantalla `idle` definida. Cierra la pregunta abierta de rótulos bilingües. | Agregar la pantalla. El selector cambia el idioma de los rótulos; el idioma de la voz lo sigue decidiendo el último turno del cliente (Req. 5.4). Requiere textos EN de todas las pantallas. |
| 2 | Campo `Escribe tu mensaje` y botón `Enviar` en la conversación (pantalla 2). | No hay entrada de texto en el protocolo. Req. 7.1 pide que no haya formularios. | Agregar un mensaje `text` cliente→servidor. El texto escrito debe pasar por Guardrail y detector sensible igual que la voz. Decidir si se acepta. |
| 3 | Botón `Silenciar` (pantalla 2). | No está en los requerimientos. | Silencia solo el micrófono local (deja de enviar audio) sin colgar. Debe convivir con el watchdog de 3 s de Req. 6.9 (no disparar `no te escucho` cuando el silencio sea voluntario). |
| 4 | Enlace `Demo: ver rutina →` (pantalla 2). | No aplica. | Solo en el prototipo; oculto en producción. |
| 5 | Precio `$[precio]` sin `MXN`. | Req. 18.4 y 20.5 piden `$#,###.## MXN`. | Confirmar si se agrega `MXN` o se respeta el diseño. |
| 6 | Pantalla 6 sin `TOTAL SUGERIDO`, con botones `Marcar como atendida` y `Leer otro QR`. | Req. 20 pide `TOTAL SUGERIDO` y el texto largo `MARCAR COMO ATENDIDA Y COMPLETAR DESPACHO`. | Confirmar si se omite el total y se acorta el botón, o se agrega el total debajo de la lista. |
| 7 | Encabezado de la recomendación: código + etiqueta `PENDIENTE` + `generada [fecha y hora]`. | Req. 20.2 pide `Rutina Identificada: #<Codigo_Corto> \| <DD/MM/AAAA> \| Estado: <ESTADO>`. | Respetar el diseño; mostrar la fecha y hora. Confirmar el formato de fecha. |
| 8 | Caja en formato móvil vertical. | El diseño técnico no fija el dispositivo. | Diseñar la Caja mobile-first. Se ve bien también en escritorio centrada. |
| 9 | Tarjetas de producto con botón `Ver modo de uso`. | Igual que Req. 18. | Sin diferencia. |

## Decisiones del usuario

- **Idioma (fila 1):** el idioma se detecta por la conversación. NO hay selector `ES | EN`; se omite de la pantalla 1 y del resto de encabezados. Los rótulos de la interfaz siguen el idioma vigente que detecta `LanguageTracker` (inicialmente español; la pantalla de inicio todavía no tiene conversación, así que arranca en español). El encabezado de la pantalla 2 muestra `Conversación en curso · <idioma vigente>`.
- **Fila 5 (precio sin `MXN`) y fila 6 (Caja sin `TOTAL SUGERIDO`, botón `Marcar como atendida`):** aceptadas. Se respeta el diseño: precio como `$[precio]`, sin total y con los botones del diseño.
- **Filas 2 y 3 (campo para escribir y `Silenciar`):** no hubo instrucción distinta, se implementan como en el diseño. El envío de texto queda conectado solo al mock en el prototipo; en el backend requiere mensaje `text` y Guardrail (pendiente de la tarea 7).
## Diferencias detectadas al implementar (pendientes de decisión)

| # | Tema | Situación | Hoy en el prototipo |
|---|---|---|---|
| 10 | Tamaño del QR | El diseño muestra el QR en una caja blanca de 224 px. Req. 18.7 pide ≥256 px CSS. | Se respeta el diseño (224 px, QR de 200 px). |
| 11 | Estados sin diseño | No hay pantalla para error de micrófono/conexión, pérdida de la voz ni derivación al asesor. | Error: mensaje en rojo sobre el botón de inicio. Pérdida de voz: el estado del encabezado cambia a "La conversación por voz terminó" y se conserva todo lo mostrado. Derivación: el asesor lo dice en la transcripción. Confirmar si se diseñan. |
| 12 | Imagen del producto | El diseño muestra un placeholder con la leyenda "Imagen del producto (columna «Imagen» del CSV)". | Se usa esa leyenda cuando no hay imagen o falla la carga. Para producción conviene un texto más neutro (p. ej. "Imagen no disponible"). |
| 13 | SKU y precio en una línea | Con un SKU real de 9 dígitos y precios como `$1,249.50`, no caben en una línea en la tarjeta de 189 px. | El precio baja a la línea siguiente cuando no cabe. |
| 14 | Popover "Ver modo de uso" | El diseño no lo muestra abierto. | Panel blanco sobre la tarjeta con el texto y un botón "Cerrar". Cierra con Escape y clic fuera. |
| 15 | Fuentes | No se tienen los archivos originales. | Bodoni Moda (títulos, con eje de tamaño óptico) y Hanken Grotesk (texto) de Fontsource. Son aproximaciones. |
| 16 | Idioma de la pantalla de inicio | Sin selector, no hay conversación que detectar antes de iniciar. | Arranca en español; al empezar la sesión el idioma sigue al de la conversación. |

## Pantallas y textos agregados sin diseño (pendientes de revisión)

| # | Tema | Situación | Hoy |
|---|---|---|---|
| 17 | Tarjeta de un paso sin producto | Req. 18.3 pide una tarjeta que indique que no hay producto para ese paso; el diseño no la muestra. | Mismo encabezado que las demás (`N PASO`) y el texto «No hay producto disponible para este paso.» / «No product available for this step.». |
| 18 | Alta del dispositivo (`/kiosk/setup`) | DD-03 exige aprovisionar el iPad una vez con el usuario de servicio. Sin diseño. | Formulario mínimo «Configurar dispositivo» (Usuario, Contraseña, «Guardar dispositivo»), solo para el responsable de la tienda. |
| 19 | Confirmación de derivación (`/derivacion/<session_id>`) | Req. 12.7 y DD-14. Sin diseño. | En el marco de Caja: inicio de sesión y botón «Confirmar que lo atenderé». |
| 20 | Errores de configuración del dispositivo | Sin diseño. | Mensaje en rojo sobre el botón de inicio: «Este dispositivo todavía no está configurado…». |
| 21 | Encabezados de las tarjetas de paso | Para que el orden de títulos sea correcto para lectores de pantalla, los pasos pasaron de `h3` a `h2`. | Sin cambio visual. |
| 22 | «Tu rutina» por debajo de 1024 px | Req. 18.3 pide 2 columnas entre 600 y 1023 px y 1 por debajo de 600 px; el diseño solo existe a 1194 px. Con la columna lateral de 300 px fija, a 420 px las tarjetas quedaban tapadas por el QR. | Con 1024 px o más no cambia nada. Por debajo, la tarjeta «PARA LA CAJA» y las lecturas pasan debajo de las tarjetas y los márgenes se reducen. Sin diseño para esos anchos: confirmar o entregar uno. |
