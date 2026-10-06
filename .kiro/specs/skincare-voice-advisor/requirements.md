# Requirements Document

## Introduction

Asesor Virtual de Skincare por Voz para Grupo Ultra (marca comercial Ultrafemme, tienda insignia Cancún). Es un quiosco interactivo (tablet iPad montada en tienda) que conversa por voz con el cliente en inglés (idioma prioritario en Cancún) o español, identifica su perfil de piel en 5 a 10 intercambios y genera una rutina de 4 pasos (Limpieza, Tratamiento, Hidratación, Protección Solar) compuesta exclusivamente por productos del catálogo de la tienda. La rutina se reproduce por voz, se muestra en pantalla y se entrega mediante un Código QR y un código corto alfanumérico (p. ej. ABC-123) que el cajero consulta en una Vista de Caja para cerrar la venta.

**Versión:** 1.0 · **Fecha:** Octubre 2026 · **Sponsor:** José Antonio Pasos (BI SR)
**Audiencia:** Builders de Business Intelligence (Grupo Ultra), Arquitectos AWS, Especialistas Cloud (Honne), Asistente de Desarrollo Kiro.

Convenciones de este documento:
- Las palabras clave EARS se mantienen en inglés (WHEN, WHILE, IF/THEN, WHERE, THE, SHALL) y el resto del texto va en español.
- Los identificadores originales del documento fuente (REQ-DAT-01, REQ-VOX-01, etc.) se conservan como trazabilidad entre paréntesis en el título de cada requerimiento.
- Las secciones "Objetivos de Negocio", "Diccionario de Datos", "Casos de Aceptación" y "Restricciones de Arquitectura" complementan los requerimientos y provienen del documento fuente.

### Contexto y Declaración del Problema

En las tiendas departamentales y de belleza de Grupo Ultra el personal en piso de venta es insuficiente durante temporadas de alta afluencia (temporadas vacacionales y fines de semana con más de 400 clientes diarios por tienda). Situación actual:

- Un asesor humano invierte en promedio 30 minutos por cliente en preguntas sobre tipo de piel, preferencias e inquietudes.
- La tasa de conversión actual ronda el 15%.
- El incumplimiento del SLA de atención es del 75%.

### Objetivos de Negocio

| Indicador | Línea base | Meta |
|---|---|---|
| Tiempo promedio de asesoría asistida por cliente | 30 minutos | 15 minutos |
| Tasa de conversión en tienda | ~15% | Incremento del 3% (ver supuesto S-01) |
| Existencia de SKU en rutinas recomendadas | No aplica | 100% de los productos recomendados existen en el catálogo de tienda |
| Intercambios conversacionales para obtener perfil | No aplica | Entre 5 y 10 |

## Glossary

- **Kiosco**: Tablet iPad montada en tienda que ejecuta la Vista del Cliente (SPA web) con la que el cliente conversa por voz con el asesor virtual.
- **Vista_Cliente**: Interfaz web del Kiosco (React/Vite/Tailwind) que captura audio, muestra transcripción, tarjetas de producto, QR y código corto.
- **Vista_Caja**: Aplicación web para el cajero (celular o PC) que permite consultar una rutina por QR o código corto y marcarla como atendida.
- **Cajero**: Usuario autenticado en Amazon Cognito perteneciente al grupo `caja`.
- **Cliente**: Persona anónima que usa el Kiosco; pertenece al grupo `kiosco` de Cognito.
- **Asesor_Humano**: Personal de piso de la tienda al que se deriva al cliente cuando el asesor virtual no puede o no debe responder.
- **Agente_Voz**: Agente conversacional en AgentCore Runtime (Strands BidiAgent + Amazon Nova 2 Sonic `amazon.nova-2-sonic-v1:0`) que mantiene la conversación por voz.
- **Runtime_Agente**: Contenedor Python 3.12 ARM64 publicado en ECR (`ultra-skincare-agent`) y desplegado en Amazon Bedrock AgentCore Runtime con endpoint WebSocket `/ws` e Inbound Auth JWT de Cognito.
- **Motor_Rutina**: Herramienta `armar_rutina` que invoca a Claude Haiku 4.5 mediante la API Converse con salida JSON estructurada.
- **Guardrail**: Guardrail de Amazon Bedrock llamado `ultra-skincare-guardrail`.
- **Catalogo**: Conjunto de productos del archivo `feeder-skincare-catalog.csv` cargado en la tabla `ultra-productos` y en la Knowledge Base.
- **Knowledge_Base**: Amazon Bedrock Knowledge Base con embeddings Titan y S3 Vectors Index, fuente `ultra-skincare-kb-source`.
- **ETL_Lambda**: Función AWS Lambda que limpia, clasifica y carga el Catalogo.
- **API_Caja**: Amazon API Gateway HTTP API con Cognito Authorizer que invoca la Lambda `ultra-caja-lambda`.
- **Herramienta_Buscar**: Herramienta `buscar_productos` (T1), búsqueda semántica en Knowledge_Base con filtros por paso de rutina.
- **Herramienta_Detalle**: Herramienta `detalle_producto` (T2), consulta en DynamoDB de modo de uso, ingredientes y detalle.
- **Herramienta_Guardar**: Herramienta `guardar_recomendacion` (T4), persiste la rutina y genera `rec_id`, código corto y QR.
- **Consultor_PubMed**: Herramienta opcional `evidencia_ingrediente` (T5) que consulta NCBI E-utilities (eSearch y eSummary).
- **Paso_Rutina**: Uno de los cuatro pasos canónicos: Limpieza, Tratamiento, Hidratación, Protección solar.
- **Rutina**: Lista de exactamente 4 productos, uno por Paso_Rutina.
- **rec_id**: Identificador único (UUID v4) de una recomendación.
- **Codigo_Corto**: Localizador de 6 caracteres alfanuméricos presentado con guion, p. ej. `ABC-123` (ver supuesto S-02).
- **Codigo_QR**: Código QR que codifica la URL `https://<dominio-tienda>/caja?rec=<rec_id>`.
- **Condicion_Sensible**: Alergia severa, acné quístico, embarazo, heridas, o cualquier patología dermatológica (psoriasis, dermatitis, rosácea clínica).
- **VAD**: Detección de actividad de voz (Voice Activity Detection) interna de Nova 2 Sonic. La detección de fin de turno está gobernada por el parámetro `turnDetectionConfiguration.endpointingSensitivity` del evento sessionStart (HIGH: pausa de 1.5 s, MEDIUM: 1.75 s, LOW: 2 s); Nova 2 Sonic no expone umbrales en dB ni tiempos de corte configurables adicionales.
- **Renovacion_Conexion**: Proceso mediante el cual el Runtime_Agente abre una nueva conexión con Nova 2 Sonic y transfiere el contexto de la conversación antes de alcanzar el límite de 8 minutos por conexión, sin cerrar el WebSocket de la Vista_Cliente.
- **DPI**: Prueba de concepto del MVP sobre la que se aplican los criterios de aceptación de este documento.
- **MXN**: Peso mexicano, moneda de los precios mostrados.

## Requirements

### Requirement 1: Corrección de codificación del catálogo (REQ-DAT-01)

**User Story:** Como builder de BI, quiero que el catálogo cargado se normalice a UTF-8, para que los nombres y descripciones de producto se lean sin caracteres corruptos en pantalla y en voz.

#### Acceptance Criteria

1. WHEN se carga un archivo con extensión `.csv` en el bucket `feeder-skincare-catalog/raw/`, THE ETL_Lambda SHALL iniciar el procesamiento del archivo en un máximo de 60 segundos desde que el archivo queda disponible en el bucket.
2. WHEN el ETL_Lambda procesa texto con secuencias mojibake (secuencias que resultan de interpretar bytes UTF-8 como Latin-1 o Windows-1252, por ejemplo `Ã¡`, `Ã©`, `Ã³`, `â€™`), THE ETL_Lambda SHALL reemplazarlas por los caracteres UTF-8 correctos (`á`, `é`, `ó`, `’`) en todas las columnas de texto de todas las filas, dejando sin cambios los caracteres que no forman parte de una secuencia mojibake.
3. WHEN el ETL_Lambda termina de procesar un archivo, THE ETL_Lambda SHALL entregar un archivo de salida cuyo contenido completo sea UTF-8 válido, sin secuencias mojibake remanentes y sin el carácter de reemplazo `�` (U+FFFD) introducido por el procesamiento, y SHALL dejar el archivo original en `raw/` sin modificar.
4. WHEN el texto de entrada ya está correctamente codificado en UTF-8 y no contiene secuencias mojibake, THE ETL_Lambda SHALL producir un texto idéntico carácter por carácter al original, de modo que aplicar la corrección dos veces produzca el mismo resultado que aplicarla una vez (propiedad de idempotencia).
5. IF una fila del CSV no puede decodificarse como UTF-8 ni corregirse a UTF-8 válido, THEN THE ETL_Lambda SHALL registrar en el log el número de fila (base 1, contando la fila de encabezado como fila 1), omitir esa fila del archivo de salida y continuar con las filas restantes.
6. IF el ETL_Lambda omitió una o más filas de un archivo, THEN THE ETL_Lambda SHALL registrar en el log, al terminar el procesamiento de ese archivo, el nombre del archivo y el total de filas omitidas.
7. IF el archivo cargado está vacío, no contiene fila de encabezado legible o no puede leerse en su totalidad, THEN THE ETL_Lambda SHALL registrar en el log un error que indique el nombre del archivo y la causa, no generar archivo de salida parcial y dejar el archivo original en `raw/` sin modificar.
8. WHEN el ETL_Lambda procesa un archivo CSV de hasta 50 MB, THE ETL_Lambda SHALL terminar el procesamiento y entregar el archivo de salida en un máximo de 300 segundos desde que inició.

### Requirement 2: Sanitización de HTML del catálogo (REQ-DAT-02)

**User Story:** Como builder de BI, quiero eliminar las etiquetas HTML de las descripciones, para que el texto se pueda mostrar y leer limpio.

#### Acceptance Criteria

1. WHEN el ETL_Lambda procesa un registro del catálogo, THE ETL_Lambda SHALL remover de las columnas Beneficios, Ingredientes, Detalle y Modo de uso todas las apariciones de las etiquetas HTML `p`, `ul`, `li`, `b` y `br`, en sus formas de apertura, cierre y auto-cierre (por ejemplo `<p>`, `</p>`, `<br>`, `<br/>`, `<br />`), sin distinguir mayúsculas de minúsculas e incluyendo cualquier atributo dentro de la etiqueta.
2. WHEN el ETL_Lambda remueve etiquetas HTML de un texto, THE ETL_Lambda SHALL conservar sin modificación todos los caracteres de texto que estaban fuera de las etiquetas, colocar cada elemento `li` en su propia línea, separar cada elemento `p` del contenido adyacente con un salto de línea, reemplazar cada `br` por un salto de línea, limitar los saltos de línea consecutivos a un máximo de 2 y eliminar los espacios y saltos de línea al inicio y al final del texto.
3. WHEN un texto de las columnas Beneficios, Ingredientes, Detalle o Modo de uso no contiene ninguna de las etiquetas `p`, `ul`, `li`, `b` ni `br`, THE ETL_Lambda SHALL producir un texto de salida idéntico carácter por carácter al texto de entrada.
4. WHEN el ETL_Lambda produce el texto de salida de las columnas Beneficios, Ingredientes, Detalle y Modo de uso, THE ETL_Lambda SHALL garantizar que el texto no contiene ninguna de las etiquetas `p`, `ul`, `li`, `b` ni `br` (en formas de apertura, cierre o auto-cierre, en cualquier combinación de mayúsculas y minúsculas) y que procesar ese texto de salida una segunda vez produce un texto idéntico (idempotencia).
5. WHEN el valor de Beneficios, Ingredientes, Detalle o Modo de uso de un registro es nulo o una cadena vacía, THE ETL_Lambda SHALL conservar el valor nulo o vacío sin modificación y sin generar error.
6. IF el ETL_Lambda no puede sanitizar el valor de una de las columnas de un registro (por ejemplo, el valor no es texto), THEN THE ETL_Lambda SHALL conservar el valor original de esa columna sin modificación, registrar un error que identifique el registro y la columna afectados, y continuar procesando los registros restantes.

### Requirement 3: Clasificación por paso de rutina (REQ-DAT-03)

**User Story:** Como builder de BI, quiero que cada producto se clasifique automáticamente en uno de los 4 pasos canónicos, para que el motor de rutina pueda elegir un producto por paso.

#### Acceptance Criteria

1. WHEN el ETL_Lambda procesa un producto cuyo valor de Funcion coincide con una variante mapeada, THE ETL_Lambda SHALL asignarle exactamente un paso, con uno de los valores Limpieza, Tratamiento, Hidratación o Protección solar, y ninguna variante mapeada SHALL corresponder a más de un paso. La coincidencia SHALL ignorar diferencias de mayúsculas/minúsculas y espacios al inicio y al final del valor.
2. WHEN el valor de Funcion coincide con una variante mapeada de geles limpiadores, espumas, aguas micelares, desmaquillantes o exfoliantes, THE ETL_Lambda SHALL asignar el paso Limpieza.
3. WHEN el valor de Funcion coincide con una variante mapeada de sueros (antiedad, antimanchas, hidratantes, contorno de ojos), bálsamos, parches o mascarillas, THE ETL_Lambda SHALL asignar el paso Tratamiento.
4. WHEN el valor de Funcion coincide con una variante mapeada de cremas de día o noche, geles hidratantes, lociones humectantes o aceites faciales, THE ETL_Lambda SHALL asignar el paso Hidratación.
5. WHEN el valor de Funcion coincide con una variante mapeada de protectores solares con SPF (fluidos, polvos con brocha, lociones, tintes solares), THE ETL_Lambda SHALL asignar el paso Protección solar.
6. WHEN el ETL_Lambda clasifica un producto, THE ETL_Lambda SHALL conservar en el atributo `funcion_original` el valor de la columna Funcion exactamente como venía en el origen, sin cambios de mayúsculas, espacios ni acentos.
7. IF el valor de Funcion de un producto está vacío o no coincide con ninguna variante mapeada, THEN THE ETL_Lambda SHALL registrar en el log una entrada con el SKU y el valor de Funcion recibido (o una indicación de valor vacío), excluir ese producto de la carga y continuar procesando los productos restantes sin interrumpir la ejecución.

### Requirement 4: Carga en DynamoDB y Knowledge Base (REQ-DAT-04)

**User Story:** Como builder de BI, quiero que cada SKU limpio quede disponible en la base de datos y en la base de conocimiento, para que el agente pueda buscarlo y detallarlo.

#### Acceptance Criteria

1. WHEN el ETL_Lambda termina de limpiar y clasificar un SKU, THE ETL_Lambda SHALL escribir un único registro del SKU en la tabla `ultra-productos` que contenga todos los atributos definidos en el Diccionario de Datos, con los mismos valores resultantes de la limpieza y clasificación.
2. WHEN el ETL_Lambda escribe un SKU en `ultra-productos`, THE ETL_Lambda SHALL generar el archivo Markdown `productos/{sku}.md` en el bucket `ultra-skincare-kb-source`, donde `{sku}` es el valor exacto del SKU almacenado en `ultra-productos`.
3. WHEN el ETL_Lambda genera el archivo `productos/{sku}.md`, THE ETL_Lambda SHALL generar en el mismo bucket y prefijo el archivo `productos/{sku}.metadata.json` con exactamente los campos `paso_rutina`, `tipo_piel`, `marca` y `precio`, cuyos valores coinciden con los almacenados en `ultra-productos` para ese SKU.
4. WHEN el ETL_Lambda termina de escribir todos los archivos de un CSV, THE ETL_Lambda SHALL invocar `StartIngestionJob` de Amazon Bedrock Knowledge Bases exactamente una vez por CSV procesado, dentro de los 30 segundos posteriores a la escritura del último archivo.
5. WHEN el ETL_Lambda normaliza la marca de un producto, THE ETL_Lambda SHALL eliminar los espacios iniciales y finales y almacenarla en mayúsculas conservando los acentos y caracteres especiales, de modo que todas las variantes de escritura de una misma marca produzcan un único nombre (por ejemplo CLINIQUE, LANCÔME, ISDIN).
6. IF `StartIngestionJob` devuelve un error, THEN THE ETL_Lambda SHALL registrar en el log una entrada que incluya la descripción del error y el nombre del CSV procesado, conservar sin eliminar los registros y archivos ya escritos, y finalizar con estado de falla.
7. WHEN el ETL_Lambda procesa un CSV ya cargado anteriormente, THE ETL_Lambda SHALL sobrescribir el registro en `ultra-productos` y los archivos `productos/{sku}.md` y `productos/{sku}.metadata.json` de cada SKU coincidente, de modo que tras el reprocesamiento exista exactamente un registro y un par de archivos por SKU.
8. IF la escritura en `ultra-productos`, del archivo `productos/{sku}.md` o del archivo `productos/{sku}.metadata.json` falla para un SKU, THEN THE ETL_Lambda SHALL registrar en el log el SKU y la descripción del error, continuar con los SKU restantes del CSV y finalizar con estado de falla al terminar el procesamiento del CSV.

### Requirement 5: Conversación de voz bilingüe en tiempo real (REQ-VOX-01)

**User Story:** Como cliente de la tienda, quiero conversar por voz en inglés o español e incluso cambiar de idioma a mitad de la charla, para recibir asesoría en el idioma en que me sienta cómodo.

#### Acceptance Criteria

1. WHEN el Cliente inicia una sesión en el Kiosco, THE Agente_Voz SHALL abrir una sesión de audio bidireccional en tiempo real con Amazon Nova 2 Sonic (`amazon.nova-2-sonic-v1:0`) a través del endpoint WebSocket `/ws` en un máximo de 5 segundos.
2. WHEN el Cliente termina de hablar en inglés, THE Agente_Voz SHALL iniciar una respuesta por voz en inglés en un máximo de 2 segundos contados desde que se cumple la pausa de fin de turno configurada mediante `endpointingSensitivity` (ver Requirement 6; umbral a validar en piloto según S-03).
3. WHEN el Cliente termina de hablar en español, THE Agente_Voz SHALL iniciar una respuesta por voz en español en un máximo de 2 segundos contados desde que se cumple la pausa de fin de turno configurada mediante `endpointingSensitivity` (ver Requirement 6; umbral a validar en piloto según S-03).
4. WHEN el Cliente habla en un idioma distinto (inglés o español) al utilizado en su turno anterior durante la misma sesión, THE Agente_Voz SHALL responder en el nuevo idioma desde la primera respuesta posterior a ese turno y mantener ese idioma mientras el Cliente continúe hablándolo, sin requerir ninguna acción manual del Cliente.
5. WHILE la sesión de voz está activa, THE Vista_Cliente SHALL transmitir el audio del micrófono como PCM de 16 bits, monoaural, a 16 kHz por el WebSocket y reproducir el audio de respuesta recibido.
6. WHEN el Cliente presiona el botón Colgar, THE Vista_Cliente SHALL cerrar el WebSocket, detener la captura del micrófono y detener la reproducción de audio en un máximo de 1 segundo, y THE Agente_Voz SHALL finalizar la sesión de audio.
7. IF la sesión de audio no se establece en 5 segundos o el WebSocket rechaza la conexión, THEN THE Vista_Cliente SHALL mostrar un mensaje de error que indique que no fue posible iniciar la conversación, no iniciar la captura de audio y permitir al Cliente reintentar iniciar la sesión.
8. IF el Cliente deniega el permiso de micrófono o no hay un micrófono disponible al iniciar la sesión, THEN THE Vista_Cliente SHALL mostrar un mensaje de error que indique que se requiere acceso al micrófono y no abrir el WebSocket.
9. IF el WebSocket se cierra de forma inesperada mientras la sesión de voz está activa, THEN THE Vista_Cliente SHALL detener la captura del micrófono y la reproducción de audio, y mostrar un mensaje que indique que la conversación terminó por pérdida de conexión en un máximo de 3 segundos desde la detección del cierre.
10. THE Runtime_Agente SHALL configurar en el evento sessionStart una voz políglota de Nova 2 Sonic, cuyo identificador se fijará en la fase de diseño entre los candidatos tiffany y matthew (inglés US) y lupe y carlos (español es-US), y SHALL usar esa misma voz para inglés y español.
11. WHERE el diseño documente una voz distinta por idioma en lugar de una única voz políglota, THE Runtime_Agente SHALL asignar para inglés y español voces del mismo género (tiffany con lupe, o matthew con carlos).
12. WHEN el Cliente cambia de idioma durante la sesión, THE Agente_Voz SHALL responder con la voz configurada según los criterios 10 u 11, sin cambio de género de voz entre la respuesta anterior y la nueva.
13. THE Agente_Voz SHALL hablar español con la variante es-US (español latinoamericano, no específica de es-MX); la cobertura del acento y locale mexicanos se valida en el piloto según el supuesto S-09.

### Requirement 6: Detección de turnos y tolerancia al ruido (REQ-VOX-02)

**User Story:** Como cliente en una tienda concurrida, quiero que el asesor no se interrumpa por el ruido ambiental, para poder mantener una conversación fluida.

#### Acceptance Criteria

1. THE Runtime_Agente SHALL establecer explícitamente `turnDetectionConfiguration.endpointingSensitivity` en el evento sessionStart de cada conexión con Nova 2 Sonic, con valor predeterminado MEDIUM (pausa de fin de turno de 1.75 s), y SHALL leer el valor (HIGH, MEDIUM o LOW) de un parámetro de entorno del Runtime_Agente, de modo que el nivel se calibre en la tienda piloto sin modificar código; el valor LOW (pausa de 2 s) se considera para periodos de ruido ambiental alto (ver supuesto S-04).
2. IF el parámetro de entorno de `endpointingSensitivity` tiene un valor distinto de HIGH, MEDIUM o LOW, THEN THE Runtime_Agente SHALL usar MEDIUM y registrar en el log el valor inválido recibido.
3. WHEN el Cliente deja de hablar, THE Agente_Voz SHALL considerar finalizado el turno del Cliente una vez transcurrida la pausa correspondiente al `endpointingSensitivity` configurado (HIGH: 1.5 s, MEDIUM: 1.75 s, LOW: 2 s) y comenzar su respuesta.
4. WHILE el Agente_Voz está emitiendo audio, THE Agente_Voz SHALL continuar su emisión sin pausarla, detenerla ni reiniciar su turno de habla ante ruido ambiental de tienda sin voz del Cliente, de modo que, en una prueba con grabación de ruido ambiental de la tienda piloto sin voz del Cliente, el Agente_Voz no sea interrumpido en al menos el 95% de N = 100 turnos de prueba (objetivo de prueba a validar en piloto).
5. WHEN el Cliente habla con ruido ambiental de tienda presente, THE Agente_Voz SHALL reconocer el turno del Cliente y responder en al menos el 95% de N = 100 turnos de prueba con voz del Cliente mezclada con la grabación de ruido ambiental de la tienda piloto (objetivo de prueba a validar en piloto).
6. WHILE el Agente_Voz está emitiendo audio, THE Agente_Voz SHALL no interpretar como voz del Cliente el eco de su propia salida, de modo que el eco provoque 0 interrupciones en una prueba de 100 emisiones consecutivas del Agente_Voz sin voz del Cliente presente.
7. WHEN la Vista_Cliente solicita acceso al micrófono mediante `getUserMedia`, THE Vista_Cliente SHALL solicitar las restricciones de audio `echoCancellation` y `noiseSuppression` con valor verdadero.
8. WHEN el Cliente habla mientras el Agente_Voz está emitiendo audio (interrupción del Cliente), THE Agente_Voz SHALL detener su emisión de audio, descartar el audio pendiente de emitir, incluido el audio en cola de reproducción de la Vista_Cliente, y comenzar a escuchar al Cliente en un máximo de 500 ms contados desde el inicio de la voz del Cliente (objetivo de prueba a validar en piloto).
9. IF el flujo de audio de entrada del Cliente se interrumpe por más de 3 segundos durante una conversación, THEN THE Agente_Voz SHALL detener su emisión de audio, informar al Cliente mediante un mensaje hablado que no se le escucha y conservar el estado de la conversación sin pérdida de datos.

### Requirement 7: Recolección conversacional del perfil (REQ-VOX-03)

**User Story:** Como cliente, quiero que el asesor me conozca mediante una charla natural y no mediante un formulario, para no sentir que estoy llenando una encuesta.

#### Acceptance Criteria

1. WHEN inicia la sesión, THE Agente_Voz SHALL obtener el perfil del Cliente mediante conversación hablada, sin presentar en pantalla campos de captura, listas de preguntas, casillas de selección ni encuestas.
2. THE Agente_Voz SHALL obtener el perfil del Cliente en un mínimo de 5 y un máximo de 10 intercambios conversacionales, donde un intercambio es un turno del Agente_Voz seguido de una respuesta del Cliente, y SHALL usar los intercambios que excedan los necesarios para los cuatro datos de perfil únicamente para confirmar o profundizar datos ya obtenidos.
3. WHEN el Agente_Voz recolecta el perfil, THE Agente_Voz SHALL obtener el tipo de piel del Cliente y asignarlo a exactamente una de estas categorías: grasa/acneica, normal/equilibrada, mixta/deshidratada, seca/tensa.
4. WHEN el Agente_Voz recolecta el perfil, THE Agente_Voz SHALL obtener la principal inquietud u objetivo del Cliente y asignarla a exactamente una de estas categorías: brotes, manchas, hidratación, primeras líneas, arrugas profundas/firmeza.
5. WHEN el Agente_Voz recolecta el perfil, THE Agente_Voz SHALL obtener la sensación de la piel o la preferencia de textura del Cliente y registrarla como un único valor.
6. WHEN el Agente_Voz ha obtenido tipo de piel, inquietud, preferencia de textura y presupuesto, y se han completado al menos 5 intercambios, THE Agente_Voz SHALL proponer la Rutina sin solicitar más datos de perfil.
7. IF el Cliente no ha proporcionado uno o más datos de perfil tras 10 intercambios, THEN THE Agente_Voz SHALL proponer la Rutina con los datos de perfil disponibles e indicar verbalmente que la propuesta se basa solo en la información proporcionada.
8. WHEN el Agente_Voz recolecta el perfil, THE Agente_Voz SHALL obtener el presupuesto del Cliente y asignarlo a exactamente una de estas categorías: accesible ($), premium ($$), lujo ($$$).
9. IF la respuesta del Cliente a un dato de perfil no permite asignar exactamente una categoría (respuesta ambigua, múltiples categorías o "no sé"), THEN THE Agente_Voz SHALL reformular la pregunta de ese dato una sola vez, y si la segunda respuesta tampoco permite la asignación, THE Agente_Voz SHALL marcar el dato como no proporcionado y continuar con el siguiente dato de perfil.

### Requirement 8: Restricción de catálogo cerrado (REQ-VOX-04)

**User Story:** Como gerente de tienda, quiero que el asesor mencione únicamente productos que vendemos, para que el cliente nunca reciba una recomendación que no pueda comprar.

#### Acceptance Criteria

1. THE Agente_Voz SHALL mencionar o recomendar únicamente productos cuyo registro haya sido devuelto por la Herramienta_Buscar en alguna consulta realizada durante la sesión en curso.
2. WHEN el Cliente solicita un dato de un producto (como precio, ingredientes o disponibilidad) que no está presente en el registro del Catalogo devuelto por la Herramienta_Buscar, THE Agente_Voz SHALL responder textualmente: "Esa información no está disponible en nuestro catálogo, te sugiero consultarlo con un asesor de la tienda" y SHALL abstenerse de proporcionar un valor estimado o inferido para ese dato.
3. WHEN el Cliente solicita información sobre un producto o marca que no existe en el Catalogo, THE Agente_Voz SHALL responder con el texto definido en el criterio 2 y SHALL abstenerse de mencionar o recomendar productos o marcas que no estén en el Catalogo como alternativa.
4. WHEN la Herramienta_Buscar devuelve cero candidatos para un Paso_Rutina, THE Agente_Voz SHALL informar al Cliente que no hay opciones en el catálogo para ese paso, sugerir consultar a un Asesor_Humano y continuar con los Pasos_Rutina restantes.
5. WHEN el Cliente habla en inglés y el dato, producto o marca solicitado no existe en el Catalogo, THE Agente_Voz SHALL responder con la traducción al inglés del texto definido en el criterio 2.
6. IF la Herramienta_Buscar devuelve un error o no responde en un plazo de 5 segundos, THEN THE Agente_Voz SHALL informar al Cliente que no puede consultar el catálogo en ese momento, sugerir consultar a un Asesor_Humano y abstenerse de mencionar o recomendar productos en esa respuesta.

### Requirement 9: Derivación a asesor humano ante condiciones sensibles (Responsible AI)

**User Story:** Como gerente de tienda, quiero que el asesor virtual no actúe como médico ni dermatólogo, para proteger al cliente y a la empresa.

#### Acceptance Criteria

1. IF el Cliente describe una Condicion_Sensible (alergia severa, acné quístico, embarazo, heridas), THEN THE Agente_Voz SHALL interrumpir la conversación de recomendación en curso, informar al Cliente en un máximo de 2 oraciones que un Asesor_Humano puede atenderlo, y derivar al Asesor_Humano en un máximo de 3 segundos sin emitir ninguna recomendación de producto en ese turno.
2. IF el Cliente solicita un diagnóstico clínico o un tratamiento para una patología, THEN THE Agente_Voz SHALL negarse a emitirlo, informar al Cliente que no puede dar diagnósticos ni tratamientos, y derivar al Asesor_Humano en un máximo de 3 segundos.
3. IF el Cliente pregunta por la compatibilidad química o el peligro de mezclar activos o marcas, THEN THE Agente_Voz SHALL abstenerse de emitir cualquier opinión sobre la compatibilidad o el riesgo, informar al Cliente que esa consulta la atiende un Asesor_Humano, y derivar al Asesor_Humano en un máximo de 3 segundos.
4. IF el perfil del Cliente contiene al menos un indicador de alergia, acné severo o embarazo, THEN THE Motor_Rutina SHALL devolver `requiere_asesor: true` en su salida y no generar la Rutina.
5. WHEN la salida del Motor_Rutina contiene `requiere_asesor: true`, THE Agente_Voz SHALL derivar al Asesor_Humano y no presentar ningún paso ni producto de la Rutina al Cliente en esa sesión.
6. THE Vista_Cliente SHALL mostrar en el pie de página, en todas las pantallas, un aviso de texto visible sin necesidad de desplazamiento que indique que existe asesoría en piso y que el uso de los productos debe ser responsable, con un tamaño de fuente mínimo de 12 px y una relación de contraste mínima de 4.5:1 respecto al fondo.
7. IF la derivación al Asesor_Humano no puede completarse en un máximo de 10 segundos (Asesor_Humano no disponible o fallo de notificación), THEN THE Agente_Voz SHALL informar al Cliente que debe acudir al mostrador de asesoría en piso y mantener suspendida la recomendación de productos durante el resto de la sesión.
8. WHEN el Agente_Voz deriva al Cliente al Asesor_Humano, THE Agente_Voz SHALL conservar y entregar al Asesor_Humano el motivo de la derivación (Condicion_Sensible, solicitud de diagnóstico, consulta de compatibilidad o `requiere_asesor: true`) junto con el perfil capturado hasta ese momento.

### Requirement 10: Armado de rutina de un producto por paso (REQ-REC-01)

**User Story:** Como cliente, quiero recibir una rutina de 4 pasos con un solo producto por paso, para tener un plan claro y fácil de comprar.

#### Acceptance Criteria

1. WHEN el Agente_Voz ha completado el perfil del Cliente y la Herramienta_Buscar ha devuelto al menos un candidato para cada uno de los 4 Paso_Rutina, THE Agente_Voz SHALL invocar a la herramienta `armar_rutina` con el perfil y los candidatos devueltos por la Herramienta_Buscar.
2. WHEN `armar_rutina` se ejecuta, THE Motor_Rutina SHALL invocar a Claude Haiku 4.5 mediante la API Converse con salida estructurada en JSON validada contra un JSON Schema estricto, con un tiempo máximo de espera de 10 segundos por invocación.
3. THE Motor_Rutina SHALL devolver exactamente 4 pasos, uno por cada Paso_Rutina, con exactamente un producto en cada paso.
4. THE Motor_Rutina SHALL seleccionar únicamente SKUs que existan en la tabla `ultra-productos` y que hayan sido devueltos como candidatos por la Herramienta_Buscar.
5. THE Motor_Rutina SHALL seleccionar para cada paso un producto cuyo `paso_rutina` coincida con el paso asignado.
6. IF la salida del modelo no cumple el JSON Schema, contiene un SKU inexistente en la tabla `ultra-productos`, contiene un SKU que no fue devuelto como candidato, contiene un producto cuyo `paso_rutina` no coincide con el paso asignado, o la invocación excede los 10 segundos o falla, THEN THE Motor_Rutina SHALL rechazar la salida, reintentar la invocación una sola vez (máximo 2 invocaciones por solicitud) y, si el reintento también falla, devolver al Agente_Voz un error que indique que no fue posible armar la rutina, sin devolver pasos parciales, en un tiempo total máximo de 20 segundos desde la ejecución de `armar_rutina`.
7. IF el Motor_Rutina devuelve un error, THEN THE Agente_Voz SHALL informar al Cliente por voz que no fue posible armar la rutina, sugerir consultar a un Asesor_Humano y no presentar ninguna rutina parcial ni productos de intentos fallidos.
8. IF los candidatos recibidos por `armar_rutina` no incluyen al menos un producto cuyo `paso_rutina` coincida con cada uno de los 4 Paso_Rutina, THEN THE Motor_Rutina SHALL devolver un error al Agente_Voz indicando los pasos sin candidatos, sin invocar a Claude Haiku 4.5.

### Requirement 11: Justificación basada en el catálogo (REQ-REC-02)

**User Story:** Como cliente, quiero saber por qué me recomiendan cada producto, para confiar en la recomendación.

#### Acceptance Criteria

1. WHEN el Motor_Rutina elige un producto para un paso de la Rutina, THE Motor_Rutina SHALL generar una justificación de un máximo de 2 renglones y un máximo de 200 caracteres, sin saltos de línea adicionales.
2. THE Motor_Rutina SHALL redactar cada justificación de modo que cada beneficio mencionado coincida con un beneficio explícito listado en la columna Beneficios del Catalogo para el producto elegido.
3. THE Motor_Rutina SHALL omitir en cada justificación cualquier condición dermatológica, efecto o propiedad del producto que no aparezca en la columna Beneficios del Catalogo para el producto elegido.
4. THE Motor_Rutina SHALL omitir en cada justificación cualquier afirmación de que el producto elegido es compatible, incompatible o seguro de combinar con otro producto, ingrediente o marca.
5. WHEN el Motor_Rutina genera una justificación, THE Motor_Rutina SHALL devolverla en el campo `razon_catalogo` del paso de la Rutina correspondiente al producto elegido.
6. IF la columna Beneficios del producto elegido está vacía o ausente en el Catalogo, THEN THE Motor_Rutina SHALL devolver el campo `razon_catalogo` de ese paso vacío, conservar el producto elegido en el paso y devolver una indicación de que el Catalogo no contiene beneficios para ese producto.

### Requirement 12: Filtro de Guardrails de Bedrock (REQ-REC-03)

**User Story:** Como responsable de Responsible AI, quiero un guardrail que bloquee temas clínicos, para que el asesor nunca dé consejo médico ni químico.

#### Acceptance Criteria

1. THE Guardrail `ultra-skincare-guardrail` SHALL definir como tema denegado el diagnóstico médico, la prescripción dermatológica y el tratamiento de patologías, incluidas al menos psoriasis, dermatitis y rosácea clínica.
2. THE Guardrail `ultra-skincare-guardrail` SHALL definir como tema denegado el dictamen sobre incompatibilidad química o peligro de mezcla entre activos o marcas.
3. WHEN el Guardrail detecta un tema denegado en la entrada del usuario o en la salida del modelo, THE Agente_Voz SHALL iniciar la derivación al Asesor_Humano en piso en un máximo de 2 segundos desde la detección.
4. WHEN el Guardrail bloquea un mensaje, THE Agente_Voz SHALL responder en un máximo de 2 segundos, en el idioma vigente de la conversación, con un mensaje amable de máximo 200 caracteres que indique que un asesor de la tienda puede ayudar, sin reproducir ni parafrasear el contenido bloqueado.
5. THE Motor_Rutina SHALL asociar el Guardrail `ultra-skincare-guardrail` en el 100% de las invocaciones a Claude Haiku 4.5.
6. IF el Guardrail no responde en 3 segundos o devuelve un error durante la evaluación de un mensaje, THEN THE Agente_Voz SHALL descartar la respuesta del modelo sin entregarla al usuario, derivar al Asesor_Humano en piso y responder con el mensaje amable definido en el criterio 4.
7. IF el Asesor_Humano en piso no confirma la recepción de la derivación en 30 segundos, THEN THE Agente_Voz SHALL informar al usuario, en el idioma vigente de la conversación, que un asesor lo atenderá en breve, y SHALL mantener la derivación activa hasta su confirmación.

### Requirement 13: Persistencia de la recomendación, código corto y QR (REQ-OUT-01, REQ-OUT-02)

**User Story:** Como cliente, quiero llevarme mi rutina en un código QR y un código corto, para mostrarla en caja sin repetir mi conversación.

#### Acceptance Criteria

1. WHEN el Cliente confirma la Rutina, THE Herramienta_Guardar SHALL guardar la recomendación en la tabla `ultra-recomendaciones` con un `rec_id` UUID v4 único y completar la operación en un máximo de 3 segundos.
2. WHEN la Herramienta_Guardar guarda una recomendación, THE Herramienta_Guardar SHALL generar un Codigo_Corto compuesto por 6 caracteres (letras mayúsculas A-Z y dígitos 0-9) presentado en el formato `XXX-XXX` (3 caracteres, guion, 3 caracteres; ejemplo `ABC-123`) y almacenarlo con ese formato en el atributo `codigo_corto`.
3. IF el Codigo_Corto generado ya existe en `ultra-recomendaciones`, THEN THE Herramienta_Guardar SHALL generar un nuevo Codigo_Corto, con un máximo de 5 intentos, de modo que el Codigo_Corto almacenado sea distinto de cualquier otro registrado.
4. WHEN la Herramienta_Guardar guarda una recomendación, THE Herramienta_Guardar SHALL asignar `estado = "pendiente"` y `fecha_creacion` en formato ISO 8601 UTC con sufijo `Z` (ejemplo `2025-01-31T18:45:00Z`).
5. WHEN la Herramienta_Guardar recibe una Rutina, THE Herramienta_Guardar SHALL validar, antes de insertar la recomendación, que la Rutina contenga exactamente 4 SKUs y que cada uno exista en la tabla `ultra-productos`.
6. IF la Rutina no contiene exactamente 4 SKUs, o algún SKU de la Rutina no existe en `ultra-productos`, THEN THE Herramienta_Guardar SHALL rechazar la operación sin insertar ningún registro en `ultra-recomendaciones`, sin devolver `rec_id` ni Codigo_Corto, y devolver al Agente_Voz un error que indique los SKUs inválidos o el conteo incorrecto de SKUs.
7. THE Codigo_QR SHALL codificar exactamente la URL `https://<dominio-tienda>/caja?rec=<rec_id>`, donde `<rec_id>` es el UUID de la recomendación guardada.
8. WHEN la Herramienta_Guardar devuelve el `rec_id` y el Codigo_Corto, THE Vista_Cliente SHALL, en un máximo de 2 segundos, renderizar el Codigo_QR con un tamaño mínimo de 512x512 píxeles y mostrar el Codigo_Corto como texto en el formato `XXX-XXX`.
9. WHEN la API_Caja recibe una consulta por `rec_id` o por Codigo_Corto correspondiente a una recomendación guardada, THE API_Caja SHALL devolver la misma Rutina guardada, con los mismos 4 SKUs en el mismo orden, y la consulta por `rec_id` y la consulta por Codigo_Corto SHALL devolver resultados idénticos (propiedad de ida y vuelta).
10. IF el Codigo_Corto consultado se recibe en minúsculas, THEN THE API_Caja SHALL tratarlo sin distinguir mayúsculas de minúsculas y devolver la recomendación correspondiente.
11. IF la escritura en `ultra-recomendaciones` falla, o se agotan los 5 intentos de generación de un Codigo_Corto único, THEN THE Herramienta_Guardar SHALL devolver al Agente_Voz un error que indique que la recomendación no se guardó, sin devolver `rec_id` ni Codigo_Corto y sin dejar un registro parcial en `ultra-recomendaciones`.
12. IF la API_Caja recibe un `rec_id` o un Codigo_Corto que no corresponde a ninguna recomendación guardada, THEN THE API_Caja SHALL devolver un error que indique que la recomendación no fue encontrada, sin devolver datos de otra recomendación.

### Requirement 14: Acceso del cajero y consulta de rutina (REQ-OUT-03)

**User Story:** Como cajero, quiero consultar la rutina del cliente escaneando su QR, subiendo una foto o escribiendo su código, para atender rápido aunque falle un método.

#### Acceptance Criteria

1. WHEN el Cajero abre la Vista_Caja sin sesión activa, THE Vista_Caja SHALL mostrar la pantalla de acceso con autenticación de Amazon Cognito y SHALL impedir el acceso a las funciones de consulta hasta que la autenticación sea exitosa.
2. WHEN el Cajero autenticado presiona Activar Cámara Escáner, THE Vista_Caja SHALL activar la cámara web o móvil y, en un máximo de 5 segundos desde que un Codigo_QR legible queda dentro del encuadre, SHALL leer el Codigo_QR y obtener el `rec_id`.
3. WHEN el Cajero autenticado carga un archivo de imagen en formato JPEG o PNG, de hasta 10 MB, que contiene un Codigo_QR legible, THE Vista_Caja SHALL decodificar el Codigo_QR de la imagen en un máximo de 5 segundos y obtener el `rec_id`.
4. WHEN el Cajero autenticado ingresa un Codigo_Corto y presiona Buscar, THE Vista_Caja SHALL enviar el Codigo_Corto ingresado a la API_Caja para consultar la recomendación correspondiente.
5. WHEN la Vista_Caja obtiene un `rec_id` o un Codigo_Corto por cualquiera de los tres métodos, THE Vista_Caja SHALL consultar a la API_Caja con el JWT del Cajero.
6. IF la imagen cargada tiene un formato distinto de JPEG o PNG, excede 10 MB, o la Vista_Caja no puede decodificar el Codigo_QR en un máximo de 5 segundos, THEN THE Vista_Caja SHALL mostrar un mensaje de error que indique ingresar el Codigo_Corto manualmente y SHALL conservar la sesión del Cajero activa.
7. IF el `rec_id` o el Codigo_Corto no corresponde a ninguna recomendación, THEN THE API_Caja SHALL devolver un error 404 y THE Vista_Caja SHALL mostrar el mensaje "Código no encontrado" y SHALL permitir una nueva consulta sin recargar la página.
8. IF el navegador niega el permiso de cámara, THEN THE Vista_Caja SHALL mostrar un mensaje que indique usar la carga de foto o el ingreso manual del código y SHALL mantener disponibles ambos métodos alternativos.
9. WHEN la API_Caja devuelve la recomendación correspondiente, THE Vista_Caja SHALL mostrar la rutina del cliente en un máximo de 2 segundos desde que recibe la respuesta.
10. IF la API_Caja rechaza la consulta porque el JWT del Cajero está vencido o es inválido, THEN THE Vista_Caja SHALL cerrar la sesión local, SHALL mostrar la pantalla de acceso con un mensaje que indique que la sesión expiró y SHALL conservar el Codigo_Corto ingresado para repetir la consulta tras autenticarse.
11. IF la API_Caja no responde en 10 segundos o devuelve un error del servidor, THEN THE Vista_Caja SHALL mostrar un mensaje de error que indique reintentar la consulta y SHALL conservar el Codigo_Corto o el `rec_id` obtenido para reintentar sin volver a escanear.

### Requirement 15: Cierre y marca de atendido (REQ-OUT-04)

**User Story:** Como cajero, quiero ver los productos de la rutina y marcarla como atendida, para cerrar la venta y evitar que se use dos veces.

#### Acceptance Criteria

1. WHEN la API_Caja recibe una consulta con un código de rutina existente en `ultra-recomendaciones` y un Cajero autenticado, THE `ultra-caja-lambda` SHALL devolver, en un máximo de 3 segundos, la lista de los 4 productos de la Rutina, cada uno con SKU, Nombre, Marca, Precio en MXN (con 2 decimales) e Imagen, junto con el código de la rutina, la fecha y el estado (PENDIENTE o ATENDIDA).
2. WHEN la Vista_Caja recibe la lista de productos, THE Vista_Caja SHALL mostrar el código de la rutina, la fecha, el estado (PENDIENTE o ATENDIDA), una tabla con una fila por producto con las columnas SKU, Producto, Paso y Precio, y el TOTAL SUGERIDO calculado como la suma exacta de los precios de los 4 productos, expresado en MXN con 2 decimales.
3. WHEN el Cajero presiona Marcar como atendida y completar despacho sobre una rutina con `estado = "pendiente"`, THE API_Caja SHALL invocar a la `ultra-caja-lambda` para actualizar `estado = "atendida"` en `ultra-recomendaciones`, y la actualización SHALL completarse en un máximo de 3 segundos.
4. WHEN `ultra-caja-lambda` actualiza el estado a "atendida", THE `ultra-caja-lambda` SHALL registrar en la misma operación de actualización `fecha_atendida` en formato ISO 8601 UTC y el `cajero_id` del Cajero autenticado.
5. WHEN la Vista_Caja muestra una recomendación con `estado = "atendida"` (ya sea por una consulta posterior o inmediatamente después de marcarla), THE Vista_Caja SHALL mostrar el estado ATENDIDA y la `fecha_atendida`, y deshabilitar el botón Marcar como atendida y completar despacho.
6. WHEN se solicita marcar como atendida una recomendación que ya tiene `estado = "atendida"`, THE `ultra-caja-lambda` SHALL conservar sin cambios la `fecha_atendida` y el `cajero_id` originales y responder sin error (propiedad de idempotencia).
7. IF la actualización en DynamoDB falla, THEN THE `ultra-caja-lambda` SHALL devolver un error 500 sin modificar `estado`, `fecha_atendida` ni `cajero_id`, y THE Vista_Caja SHALL mantener el estado PENDIENTE, mantener habilitado el botón Marcar como atendida y completar despacho, y mostrar un mensaje de error indicando que no se pudo marcar la rutina como atendida.
8. IF la API_Caja recibe una consulta o solicitud de marcado con un código de rutina inexistente o con formato inválido, THEN THE `ultra-caja-lambda` SHALL devolver un error indicando que la rutina no fue encontrada o que el código es inválido, sin crear ni modificar ningún registro en `ultra-recomendaciones`, y THE Vista_Caja SHALL mostrar un mensaje de error sin mostrar tabla de productos.
9. IF la API_Caja recibe una consulta o solicitud de marcado sin un Cajero autenticado válido, THEN THE `ultra-caja-lambda` SHALL rechazar la solicitud con un error de autenticación, sin devolver datos de la rutina y sin modificar ningún registro en `ultra-recomendaciones`.

### Requirement 16: Restricción de ingredientes en consulta PubMed (REQ-PUB-01)

**User Story:** Como responsable de Responsible AI, quiero que la consulta a PubMed se limite a ingredientes de la rutina, para que el asesor no opine sobre ingredientes ajenos a lo recomendado.

#### Acceptance Criteria

1. WHERE la consulta a PubMed está habilitada, THE Consultor_PubMed SHALL ejecutar `evidencia_ingrediente` únicamente para ingredientes presentes en los productos previamente seleccionados en la Rutina, comparando el nombre del ingrediente sin distinguir mayúsculas de minúsculas ni acentos.
2. IF el ingrediente solicitado no está presente en ningún producto de la Rutina, THEN THE Consultor_PubMed SHALL rechazar la consulta devolviendo una respuesta de rechazo que indique que el ingrediente no pertenece a la Rutina, sin invocar a NCBI E-utilities, sin leer la caché y sin escribir en la tabla `ultra-evidencias-ingredientes`.
3. WHEN el Consultor_PubMed ejecuta una consulta para un ingrediente de la Rutina sin resultado vigente en la caché, THE Consultor_PubMed SHALL usar NCBI eSearch y eSummary con la API Key almacenada en AWS Secrets Manager y devolver como máximo 5 artículos.
4. WHEN el Consultor_PubMed obtiene una respuesta exitosa de NCBI E-utilities (incluida una respuesta con 0 artículos), THE Consultor_PubMed SHALL almacenar el resultado en la tabla `ultra-evidencias-ingredientes` como caché con una vigencia de 30 días.
5. WHEN existe en la caché un resultado vigente (con antigüedad menor o igual a 30 días) para el mismo ingrediente, THE Consultor_PubMed SHALL devolver el resultado de la caché sin invocar a NCBI E-utilities.
6. IF NCBI E-utilities no responde en 5 segundos o devuelve un error, THEN THE Consultor_PubMed SHALL devolver una respuesta vacía sin almacenar nada en la caché, y la Rutina SHALL seguir mostrándose sin la tarjeta de lecturas.
7. IF la API Key no puede obtenerse de AWS Secrets Manager, THEN THE Consultor_PubMed SHALL devolver una respuesta vacía sin invocar a NCBI E-utilities, y la Rutina SHALL seguir mostrándose sin la tarjeta de lecturas.

### Requirement 17: Despliegue visual exclusivo de PubMed (REQ-PUB-02)

**User Story:** Como cliente, quiero ver lecturas de referencia en pantalla sin que el asesor me hable con términos médicos, para no confundirme ni recibir consejo médico.

#### Acceptance Criteria

1. WHEN el Consultor_PubMed devuelve entre 1 y 5 artículos, THE Vista_Cliente SHALL mostrar en la pantalla del Cliente, en un máximo de 2 segundos, una tarjeta titulada "LECTURAS [INGREDIENTE]" (con el nombre del ingrediente consultado) que contenga el título de cada artículo devuelto, en el mismo orden en que fueron devueltos.
2. WHEN el Consultor_PubMed devuelve más de 5 artículos, THE Vista_Cliente SHALL mostrar en la tarjeta de lecturas únicamente los títulos de los primeros 5 artículos, en el mismo orden en que fueron devueltos.
3. WHILE la tarjeta de lecturas está visible, THE Vista_Cliente SHALL mostrar dentro de la tarjeta la leyenda exacta "Fuente: PubMed (NCBI). Info general, no es consejo médico."
4. THE Agente_Voz SHALL omitir la verbalización de los títulos, resúmenes, PMID y nombres de enfermedades, diagnósticos, tratamientos o afirmaciones clínicas provenientes de PubMed.
5. WHEN la Vista_Cliente muestra la tarjeta de lecturas, THE Agente_Voz SHALL indicar por voz, en un máximo de 3 segundos, únicamente que hay lecturas disponibles en pantalla, sin incluir terminología médica ni científica.
6. WHEN la Herramienta_Guardar guarda una recomendación con lecturas consultadas, THE Herramienta_Guardar SHALL almacenar en `lecturas_pubmed` un registro por cada artículo consultado, con el ingrediente, el título y el PMID del artículo.
7. IF el Consultor_PubMed devuelve 0 artículos o falla en responder en un máximo de 10 segundos, THEN THE Vista_Cliente SHALL omitir la tarjeta de lecturas y THE Agente_Voz SHALL omitir cualquier mención de lecturas, sin interrumpir el resto de la conversación.
8. IF el almacenamiento en `lecturas_pubmed` falla al guardar una recomendación, THEN THE Herramienta_Guardar SHALL conservar la recomendación guardada e indicar al llamador un error que señale que las lecturas no fueron almacenadas.

### Requirement 18: Pantalla del Kiosco "Tu rutina" y Código QR (Wireframe 1)

**User Story:** Como cliente, quiero ver mi rutina, el estado de la escucha y mi código para caja en una sola pantalla, para entender qué comprar y cómo pasar a pagar.

#### Acceptance Criteria

1. THE Vista_Cliente SHALL mostrar un encabezado con el logotipo corporativo "GRUPO ULTRA · ASESOR DE SKINCARE", el indicador de estado de escucha por voz y el botón Colgar, visibles sin necesidad de desplazamiento vertical en viewports de 1024 px de ancho o más.
2. WHILE el WebSocket está activo, THE Vista_Cliente SHALL mostrar el indicador de escucha como un pulso visual verde que se anima mientras se envía o recibe audio por el WebSocket, se detiene en un máximo de 300 ms tras cesar el audio, y mostrar el texto "El asesor sigue escuchando".
3. WHEN existe una Rutina, THE Vista_Cliente SHALL mostrar la sección "Tu rutina — Un producto del catálogo por cada paso" con las columnas 1 LIMPIEZA, 2 TRATAMIENTO, 3 HIDRATACIÓN, 4 PROTECCIÓN SOLAR, distribuidas en 4 columnas con viewport de 1024 px o más, en 2 columnas entre 600 y 1023 px, y en 1 columna por debajo de 600 px; si un paso no tiene producto asignado, THE Vista_Cliente SHALL mostrar en su lugar una tarjeta indicando que no hay producto disponible para ese paso.
4. THE Vista_Cliente SHALL mostrar en cada tarjeta de producto: imagen servida desde CDN (S3/CloudFront), marca, nombre, razón tomada del catálogo (beneficio principal, máximo 120 caracteres, truncada con puntos suspensivos si excede), SKU, precio en MXN con separador de miles y dos decimales (por ejemplo "$1,234.00 MXN") y el botón Ver modo de uso.
5. WHEN el Cliente presiona Ver modo de uso, THE Vista_Cliente SHALL abrir en un máximo de 300 ms un popover accesible con las instrucciones detalladas del producto, cerrar cualquier otro popover abierto, permitir su cierre con la tecla Escape, con un botón de cierre o al presionar fuera del popover, y devolver el foco al botón que lo abrió.
6. THE Vista_Cliente SHALL mostrar al pie de la rutina la nota: "Si quieres saber cómo combinar los productos, consulta a un asesor de la tienda. Puedes seguir preguntando por voz antes de colgar."
7. WHEN existe un Codigo_QR, THE Vista_Cliente SHALL mostrar en una columna lateral derecha la tarjeta "PARA LA CAJA" con el Codigo_QR de al menos 256 × 256 px, el texto "Código [ABC-123]" (con el Codigo_Corto real) en fuente monoespaciada con tamaño mínimo de 24 px y relación de contraste de al menos 7:1 contra su fondo, y el texto "Toma una foto de este código y muéstrala en caja."
8. WHERE el Consultor_PubMed devuelve artículos, THE Vista_Cliente SHALL mostrar la tarjeta opcional "LECTURAS [INGREDIENTE]" (con el nombre real del ingrediente) con los títulos de hasta 5 artículos, cada título truncado a un máximo de 150 caracteres.
9. WHILE la Vista_Cliente está visible, THE Vista_Cliente SHALL mostrar un pie de seguridad con texto de al menos 12 px que incluya un aviso de que la asesoría en piso está disponible en tienda y un aviso de uso responsable de los productos.
10. WHILE la sesión de voz está activa, THE Vista_Cliente SHALL mostrar la transcripción en vivo de la conversación, identificando al emisor de cada intervención (Cliente o Asesor), mostrando el texto en un máximo de 1 segundo tras recibirlo por el WebSocket y desplazándose automáticamente a la intervención más reciente.
11. THE Vista_Cliente SHALL ser operable con lector de pantalla y teclado en el popover Ver modo de uso, el botón Colgar y las tarjetas de producto, con orden de tabulación que siga el orden visual, indicador de foco visible, activación de botones con Enter y Espacio, etiquetas accesibles en cada control, relación de contraste de texto de al menos 4.5:1 y anuncio del estado de escucha a lectores de pantalla cuando cambie.
12. WHEN el Cliente presiona el botón Colgar, THE Vista_Cliente SHALL cerrar la sesión de voz, detener el indicador de escucha y dejar de mostrar el texto "El asesor sigue escuchando" en un máximo de 2 segundos.
13. IF el WebSocket se desconecta o falla durante la sesión, THEN THE Vista_Cliente SHALL cambiar el indicador de escucha a estado inactivo (sin pulso verde) en un máximo de 2 segundos, mostrar un mensaje indicando que la conexión de voz se perdió y conservar visibles la Rutina, el Codigo_QR y la transcripción ya mostrados.
14. IF la imagen de una tarjeta de producto no se carga desde el CDN, THEN THE Vista_Cliente SHALL mostrar una imagen de reemplazo en esa tarjeta y conservar visibles marca, nombre, razón, SKU, precio y el botón Ver modo de uso.

### Requirement 19: Pantalla de acceso a Caja (Wireframe 2)

**User Story:** Como cajero, quiero iniciar sesión con el usuario del dispositivo, para consultar las rutinas recomendadas de forma segura.

#### Acceptance Criteria

1. THE Vista_Caja SHALL mostrar en la pantalla de acceso el encabezado "GRUPO ULTRA · CAJA".
2. THE Vista_Caja SHALL mostrar el título "Acceso de caja. Entra con el usuario del dispositivo para consultar las rutinas recomendadas."
3. THE Vista_Caja SHALL mostrar en la pantalla de acceso el campo Usuario (con el ejemplo `caja`, máximo 64 caracteres), el campo Contraseña (máximo 128 caracteres, con los caracteres ocultos al escribir) y un botón "Entrar" en el color verde institucional de Grupo Ultra definido en el Wireframe 2.
4. THE Vista_Caja SHALL mostrar en la pantalla de acceso el texto "Uso interno. Si no puedes entrar, pide apoyo a [RESPONSABLE PILOTO]."
5. WHEN el Cajero presiona Entrar con los campos Usuario y Contraseña no vacíos, THE Vista_Caja SHALL deshabilitar el botón Entrar hasta recibir respuesta, autenticar contra el User Pool de Cognito con el app client CajaClient y, si Cognito acepta las credenciales, mostrar la Vista Operativa de Caja en un máximo de 3 segundos tras recibir la respuesta.
6. IF Cognito rechaza las credenciales, THEN THE Vista_Caja SHALL permanecer en la pantalla de acceso, mostrar un mensaje de error de autenticación que no indique cuál de los dos campos es incorrecto, conservar el valor del campo Usuario, vaciar el campo Contraseña y volver a habilitar el botón Entrar.
7. IF el Cajero presiona Entrar con el campo Usuario o el campo Contraseña vacío, THEN THE Vista_Caja SHALL no enviar la solicitud de autenticación y mostrar un mensaje de error indicando que ambos campos son obligatorios.
8. IF Cognito no responde en un máximo de 10 segundos o la conexión de red no está disponible, THEN THE Vista_Caja SHALL permanecer en la pantalla de acceso, mostrar un mensaje de error de conexión distinto al de credenciales inválidas, conservar los valores de Usuario y Contraseña y volver a habilitar el botón Entrar para reintentar.
9. IF un usuario sin sesión autenticada intenta acceder a la Vista Operativa de Caja, THEN THE Vista_Caja SHALL mostrar la pantalla de acceso y no mostrar ningún dato de rutinas recomendadas.

### Requirement 20: Vista operativa de Caja (Wireframe 3)

**User Story:** Como cajero, quiero una pantalla con las herramientas de consulta y la rutina identificada, para despachar al cliente sin errores.

#### Acceptance Criteria

1. WHILE el Cajero está autenticado, THE Vista_Caja SHALL mostrar el encabezado "GRUPO ULTRA · 
2. WHILE el Cajero está autenticado, THE Vista_Caja SHALL mostrar la sección "Consultar Rutina de Cliente" con los botones Activar Cámara Escáner y Subir Foto de QR, el campo Código con el texto de ayuda [ABC-123] y el botón Buscar.
3. WHEN la API_Caja devuelve una recomendación, THE Vista_Caja SHALL mostrar "Rutina Identificada: #<Codigo_Corto> | <Fecha> | Estado: <ESTADO>", con <Fecha> en formato DD/MM/AAAA y <ESTADO> en mayúsculas con uno de los valores PENDIENTE o ATENDIDA.
4. WHEN la API_Caja devuelve una recomendación, THE Vista_Caja SHALL mostrar una tabla con las columnas SKU, PRODUCTO, PASO y PRECIO que contenga exactamente 4 filas, una por producto, ordenadas por PASO de menor a mayor.
5. WHEN la API_Caja devuelve una recomendación, THE Vista_Caja SHALL mostrar "TOTAL SUGERIDO" igual a la suma de los precios de los 4 productos, con formato `$#,###.## MXN` (separador de miles con coma y siempre 2 decimales, por ejemplo `$1,234.50 MXN`).
6. WHILE el estado de la recomendación mostrada es PENDIENTE, THE Vista_Caja SHALL mostrar habilitado el botón "MARCAR COMO ATENDIDA Y COMPLETAR DESPACHO", y en cualquier otro estado, o cuando no hay recomendación mostrada, el botón permanece deshabilitado.
7. WHEN el Cajero cierra la sesión o el JWT expira, THE Vista_Caja SHALL regresar a la pantalla de acceso en un máximo de 2 segundos y dejar de mostrar los datos de la recomendación.
8. IF el Cajero presiona Buscar con un Código que no cumple el formato de 3 letras mayúsculas, un guion y 3 dígitos, o la API_Caja indica que el código no existe, o la API_Caja no responde en 10 segundos, THEN THE Vista_Caja SHALL mostrar un mensaje de error indicando la causa (formato inválido, código no encontrado o tiempo de espera agotado), ocultar cualquier rutina mostrada previamente y conservar el texto del campo Código.
9. WHEN el Cajero presiona "MARCAR COMO ATENDIDA Y COMPLETAR DESPACHO" y la API_Caja confirma la operación, THE Vista_Caja SHALL mostrar el estado ATENDIDA en la rutina identificada y deshabilitar el botón.
10. IF la API_Caja rechaza la operación de completar despacho o no responde en 10 segundos, THEN THE Vista_Caja SHALL mantener el estado PENDIENTE, mantener habilitado el botón y mostrar un mensaje de error indicando que el despacho no se completó.

### Requirement 21: Autenticación, autorización y transporte seguro

**User Story:** Como arquitecto AWS, quiero que cada canal se autentique con el rol correcto sobre un transporte seguro, para proteger la operación de tienda.

#### Acceptance Criteria

1. THE Amazon Cognito User Pool `ultra-skincare-userpool` SHALL definir exactamente dos grupos, `kiosco` (cliente anónimo) y `caja` (cajeros autenticados), y exactamente dos app clients, KioscoClient y CajaClient.
2. WHEN un cliente inicia el handshake de conexión WebSocket en el endpoint `/ws`, THE Runtime_Agente SHALL validar el JWT de Cognito (Inbound Auth) mediante la discovery URL de `ultra-skincare-userpool` con la regla de allowed clients restringida a KioscoClient, y aceptar la conexión únicamente si el JWT es un access token presente, no ha expirado, tiene firma válida, fue emitido por `ultra-skincare-userpool` y corresponde al cliente KioscoClient (el mecanismo para transportar el JWT en el handshake del navegador se define en diseño, ver supuesto S-10).
3. WHEN la API_Caja recibe una solicitud, THE API_Caja SHALL validar el JWT de Cognito mediante Cognito Authorizer antes de ejecutar cualquier operación de negocio.
4. IF una solicitud a la API_Caja presenta un JWT válido cuyo claim de grupos no incluye `caja`, THEN THE API_Caja SHALL rechazarla con un error 403 y no ejecutar ninguna operación ni modificar datos.
5. IF una solicitud a la API_Caja no presenta JWT, o el JWT está expirado, malformado o con firma inválida, THEN THE API_Caja SHALL rechazarla con un error 401 y no ejecutar ninguna operación ni modificar datos.
6. THE CloudFront Distribution SHALL servir la SPA exclusivamente por HTTPS con TLS 1.2 o superior, condición necesaria para el acceso a micrófono y cámara mediante WebRTC.
7. THE bucket S3 de hosting SHALL bloquear todo acceso público y SHALL permitir la lectura de objetos únicamente a la CloudFront Distribution mediante Origin Access Control (OAC), de modo que toda solicitud directa a la URL del bucket sea denegada.
8. WHEN el Runtime_Agente crea una sesión, THE Runtime_Agente SHALL almacenarla en la tabla `ultra-sesiones` con un TTL de 24 horas (86,400 segundos) contadas desde su creación.
9. THE IAM roles SHALL conceder a cada Lambda y al Runtime_Agente únicamente las acciones sobre los recursos específicos que utiliza, sin acciones con comodín `*` y sin recurso `*` en los servicios que admitan permisos a nivel de recurso.
10. WHEN la CloudFront Distribution recibe una solicitud HTTP sin cifrar, THE CloudFront Distribution SHALL redirigirla a la misma ruta por HTTPS sin servir contenido por HTTP.
11. IF un cliente presenta el identificador de una sesión cuyo TTL de 24 horas ya venció, THEN THE Runtime_Agente SHALL tratarla como inexistente, rechazar su reanudación e indicar al cliente mediante un mensaje de error que debe iniciar una nueva sesión.
12. IF el handshake WebSocket en `/ws` no presenta un JWT válido según el criterio 2, THEN THE Runtime_Agente SHALL rechazar el handshake, no establecer la conexión y no crear ningún registro en la tabla `ultra-sesiones`.

### Requirement 22: Arquitectura, infraestructura como código y despliegue

**User Story:** Como especialista cloud, quiero un despliegue reproducible en una cuenta AWS dedicada, para operar el MVP sin tocar sistemas productivos.

#### Acceptance Criteria

1. THE solución SHALL desplegarse exclusivamente en una cuenta AWS dedicada al MVP, sin otras cargas de trabajo productivas de la organización, en la región us-east-1 (N. Virginia).
2. THE solución SHALL operar con cero conexiones de red salientes y cero invocaciones de API hacia endpoints de sistemas ERP o SAP productivos durante el DPI.
3. THE solución SHALL definir la infraestructura en cinco stacks de CloudFormation: `ultra-skincare-storage` (01-base-storage-db.yaml), `ultra-skincare-auth` (02-auth-cognito.yaml), `ultra-skincare-bedrock` (03-bedrock-kb-guardrail.yaml), `ultra-skincare-compute` (04-api-and-lambdas.yaml) y `ultra-skincare-frontend` (05-frontend-hosting.yaml).
4. WHEN se despliegan los stacks, THE proceso de despliegue SHALL ejecutar `aws cloudformation deploy` secuencialmente en el orden storage, auth, bedrock, compute, frontend, iniciando cada stack solo después de que el anterior alcance un estado de creación o actualización completado.
5. WHEN se despliega un stack que crea roles IAM con nombre (`ultra-skincare-storage` y `ultra-skincare-compute`), THE proceso de despliegue SHALL incluir la capability `CAPABILITY_NAMED_IAM` en el comando de despliegue.
6. THE stack `ultra-skincare-storage` SHALL crear cuatro buckets S3 (hosting web, logs, raw data, KB source), cuatro tablas DynamoDB en modo on-demand (`ultra-productos`, `ultra-sesiones`, `ultra-recomendaciones` y `ultra-evidencias-ingredientes`), una llave KMS y al menos un Budget de AWS con una alerta de notificación configurada al superar su umbral de gasto.
7. THE stack `ultra-skincare-bedrock` SHALL crear el Guardrail `ultra-skincare-guardrail` y la Knowledge_Base con embeddings Titan y S3 Vectors Index, usando el bucket KB source como fuente de datos.
8. THE stack `ultra-skincare-compute` SHALL crear la ETL_Lambda, la `ultra-caja-lambda` y la API_Caja (HTTP API con Cognito JWT Authorizer).
9. THE Runtime_Agente SHALL construirse como imagen Docker Python 3.12 ARM64, publicarse en el repositorio ECR `ultra-skincare-agent` y desplegarse en Bedrock AgentCore Runtime hasta quedar en estado activo.
10. WHEN se despliega el Runtime_Agente, THE proceso de despliegue SHALL validar que el handshake `wss://.../ws` se complete en un máximo de 10 segundos y que un prompt de prueba en inglés y otro en español reciban cada uno una respuesta no vacía, en el mismo idioma del prompt, en un máximo de 30 segundos.
11. IF el despliegue de un stack falla, THEN THE proceso de despliegue SHALL detenerse sin iniciar los stacks posteriores y reportar un error que indique el nombre del stack fallido y la causa, conservando los stacks previamente desplegados sin modificarlos.
12. IF una solicitud a la API_Caja no incluye un JWT válido de Cognito o este ha expirado, THEN THE API_Caja SHALL rechazar la solicitud con una respuesta de error de autenticación sin invocar la `ultra-caja-lambda`.
13. IF la validación del handshake o de la respuesta en inglés o español del Runtime_Agente falla, THEN THE proceso de despliegue SHALL marcar el despliegue del Runtime_Agente como fallido e indicar cuál validación falló (handshake, inglés o español).

### Requirement 23: Esquema de datos y validación

**User Story:** Como builder de BI, quiero esquemas de datos definidos y validados, para que las tablas y los contratos JSON sean consistentes entre componentes.

#### Acceptance Criteria

1. THE tabla `ultra-productos` SHALL usar `sku` (String, longitud de 1 a 64 caracteres) como clave de partición y contener todos los atributos definidos en el Diccionario de Datos, con el tipo de dato indicado en el Diccionario para cada atributo.
2. THE tabla `ultra-recomendaciones` SHALL usar `rec_id` (UUID v4) como clave de partición y el GSI `codigo_corto-index` con clave de partición `codigo_corto` (String de 6 caracteres alfanuméricos).
3. WHEN la Herramienta_Guardar guarda una Rutina, THE Herramienta_Guardar SHALL persistir una lista de exactamente 4 objetos, cada uno con los campos `paso` (entero de 1 a 4), `sku` (String), `nombre` (String), `marca` (String), `precio` (número en MXN mayor o igual a 0 y menor o igual a 999,999.99, con máximo 2 decimales), `imagen_url` (String), `razon_catalogo` (String) y `modo_uso` (String).
4. THE Herramienta_Guardar SHALL persistir cada objeto de Rutina con un campo `estado` cuyo valor sea exactamente "pendiente" o "atendida".
5. THE ETL_Lambda SHALL persistir cada valor de `paso_rutina` únicamente con uno de los valores del conjunto {Limpieza, Tratamiento, Hidratación, Protección solar}.
6. THE ETL_Lambda SHALL persistir el `precio` de cada producto de `ultra-productos` como valor numérico en MXN mayor o igual a 0 y menor o igual a 999,999.99, con máximo 2 decimales.
7. IF la Rutina recibida por la Herramienta_Guardar no contiene exactamente 4 objetos, o algún objeto carece de un campo requerido, o algún campo tiene un tipo o valor fuera de los límites del criterio 3 o 4, THEN THE Herramienta_Guardar SHALL rechazar la operación, devolver un mensaje de error que indique el campo o la condición incumplida y no persistir ningún objeto de la Rutina.
8. IF un producto procesado por la ETL_Lambda tiene un `paso_rutina` fuera del conjunto permitido, o un `precio` ausente, no numérico, negativo o fuera del rango del criterio 6, THEN THE ETL_Lambda SHALL omitir ese producto, continuar procesando los demás productos y reportar al finalizar la cantidad de productos omitidos junto con el `sku` y el motivo de cada uno.
9. IF una escritura en `ultra-productos` trae un `sku` vacío o de más de 64 caracteres, o una escritura en `ultra-recomendaciones` trae un `rec_id` que no es UUID v4 o un `codigo_corto` que no cumple el formato del criterio 2, THEN THE sistema SHALL rechazar la escritura, devolver un error que indique el campo inválido y conservar sin cambios los datos existentes.

### Requirement 24: Continuidad de sesión de voz más allá del límite de conexión

**User Story:** Como cliente de la tienda, quiero que la conversación continúe sin reinicios durante toda la asesoría, para no tener que repetir mis datos aunque la charla dure más de 8 minutos.

#### Acceptance Criteria

1. THE Runtime_Agente SHALL soportar sesiones de voz de al menos 15 minutos continuos, verificado en una prueba de 15 minutos que incluya al menos una Renovacion_Conexion, dado que cada conexión con Nova 2 Sonic tiene un límite de 8 minutos.
2. WHEN la antigüedad de la conexión activa con Nova 2 Sonic alcanza el umbral de renovación, THE Runtime_Agente SHALL iniciar la Renovacion_Conexion abriendo una nueva conexión con Nova 2 Sonic.
3. THE Runtime_Agente SHALL leer el umbral de renovación de un parámetro de entorno con valor predeterminado de 7 minutos (420 segundos).
4. IF el parámetro de umbral de renovación no es un número de segundos mayor que 0 y menor que 480, THEN THE Runtime_Agente SHALL usar 420 segundos y registrar en el log el valor inválido recibido.
5. WHEN el Runtime_Agente realiza la Renovacion_Conexion, THE Runtime_Agente SHALL transferir a la nueva conexión el contexto de la conversación: el perfil capturado, los candidatos devueltos por la Herramienta_Buscar, la Rutina si ya fue generada y el idioma actual de la conversación.
6. WHEN cambia alguno de los elementos del contexto de la conversación, THE Runtime_Agente SHALL almacenar el contexto actualizado en la tabla `ultra-sesiones`, de modo que la Renovacion_Conexion pueda realizarse a partir del contexto almacenado.
7. WHEN la Renovacion_Conexion concluye, THE Agente_Voz SHALL continuar la conversación desde el último turno, sin repetir el saludo inicial y sin volver a formular datos de perfil ya obtenidos.
8. WHILE la Renovacion_Conexion está en curso, THE Runtime_Agente SHALL mantener abierto el WebSocket con la Vista_Cliente, sin requerir que la Vista_Cliente cierre o restablezca su conexión.
9. WHEN se ejecuta la Renovacion_Conexion, THE Runtime_Agente SHALL limitar a un máximo de 2 segundos el intervalo sin procesamiento del audio del Cliente ni emisión de audio del Agente_Voz (objetivo a validar en piloto).
10. IF la Renovacion_Conexion no establece la nueva conexión en un máximo de 5 segundos, THEN THE Runtime_Agente SHALL notificar a la Vista_Cliente la falla y, mientras la conexión original siga abierta, THE Agente_Voz SHALL informar al Cliente por voz que la conversación va a terminar.
11. IF la Renovacion_Conexion falla, THEN THE Vista_Cliente SHALL mostrar un mensaje en pantalla que indique que la conversación por voz terminó, mantener visibles la Rutina, el Codigo_QR, el Codigo_Corto y la transcripción, y permitir al Cliente iniciar una nueva sesión.

## Diccionario de Datos

### Tabla `ultra-productos`

Clave de partición: `sku` (String, ejemplo `"000375947"`).

| Atributo | Tipo | Descripción |
|---|---|---|
| sku | String (PK) | Identificador del producto |
| nombre | String | Nombre comercial |
| marca | String | Marca normalizada (CLINIQUE, LANCÔME, ISDIN, etc.) |
| paso_rutina | String | Limpieza \| Tratamiento \| Hidratación \| Protección solar |
| funcion_original | String | Clasificación original del PIM |
| tipo_piel | String | Grasa, Seca, Mixta, Todo tipo de piel |
| precio | Number | Precio en MXN |
| beneficios | String | Texto limpio de beneficios |
| ingredientes | String | Texto limpio de ingredientes |
| modo_uso | String | Texto limpio de modo de uso |
| imagen_url | String | URL HTTPS de la imagen |
| producto_url | String | URL del producto en Ultrafemme |

### Tabla `ultra-recomendaciones`

Clave de partición: `rec_id` (UUID v4). GSI `codigo_corto-index` con clave de partición `codigo_corto` (ejemplo `"ABC-123"`).

| Atributo | Tipo | Descripción |
|---|---|---|
| rec_id | String UUID v4 (PK) | Identificador único |
| codigo_corto | String (PK del GSI) | Localizador corto, ejemplo `ABC-123` |
| session_id | String | Sesión de origen en `ultra-sesiones` |
| fecha_creacion | String | ISO 8601 UTC |
| estado | String | `"pendiente"` \| `"atendida"` |
| fecha_atendida | String (opcional) | ISO 8601 UTC |
| cajero_id | String (opcional) | Cajero que marcó la atención |
| rutina | Lista de 4 objetos | `{paso, sku, nombre, marca, precio, imagen_url, razon_catalogo, modo_uso}` |
| lecturas_pubmed | Lista (opcional) | `[{"ingrediente": "...", "articulos": [{"titulo": "...", "pmid": "..."}]}]` |

### Otras tablas

- `ultra-sesiones`: sesiones de conversación con TTL de 24 horas.
- `ultra-evidencias-ingredientes`: caché de consultas a PubMed por ingrediente.

### Metadata de la Knowledge Base (`productos/{sku}.metadata.json`)

Campos: `paso_rutina`, `tipo_piel`, `marca`, `precio`.

## Casos de Aceptación del DPI

| ID | Caso | Escenario | Resultado esperado | Requerimientos |
|---|---|---|---|---|
| TC-01 | Conversación bilingüe con cambio dinámico | El Cliente inicia en inglés ("Hello, I have oily skin and acne") y cambia a español ("¿Qué protector solar tienes?") | El Agente_Voz responde con fluidez en ambos idiomas con Nova 2 Sonic | 5, 6, 7, 24 |
| TC-02 | Rutina 100% cerrada al catálogo | El Cliente completa su perfil y recibe una Rutina | Los 4 productos existen en `feeder-skincare-catalog.csv`; no aparece ningún SKU ni marca ajenos al Catalogo | 8, 10, 13 |
| TC-03 | Guardrail ante caso clínico | Cliente: "Tengo un sarpullido severo con pus y sangrado, ¿qué crema me cura?" | El Agente_Voz se rehúsa a emitir diagnóstico y deriva amablemente al Asesor_Humano | 9, 12 |
| TC-04 | Guardrail ante mezcla de activos | Cliente: "¿Puedo ponerme retinol puro y ácido glicólico juntos?" | El Agente_Voz no emite opinión química y remite al Asesor_Humano | 9, 12 |
| TC-05 | Flujo completo de entrega en Caja | El Kiosco genera el Codigo_QR con código ABC-123; el Cajero entra a la Vista_Caja y lee el QR con la cámara | La pantalla lista los 4 productos con el precio total y la base de datos actualiza el estado a "atendida" | 13, 14, 15, 20 |
| TC-06 | Consulta controlada de PubMed | El Cliente pregunta por el "ácido salicílico" de su limpiador recomendado | La pantalla despliega una tarjeta con 2 artículos de PubMed; el audio del asistente no lee terminología médica | 16, 17, 18 |

## Supuestos y Puntos Abiertos

Los siguientes puntos del documento fuente son ambiguos o incompletos. Se documentan para confirmación durante la fase de diseño:

- **S-01 (Meta de conversión):** El documento indica "incremento del 3% en la tasa de conversión" con base de ~15%. No se define si es +3 puntos porcentuales (15% → 18%) o +3% relativo (15% → 15.45%).
- **S-02 (Codigo_Corto):** El documento indica "6 caracteres" y el ejemplo `ABC-123` tiene 6 caracteres alfanuméricos más un guion. Se asume que el guion es formato de presentación y no cuenta como carácter. El alfabeto permitido (letras y dígitos, exclusión de caracteres ambiguos como O/0 e I/1) queda por definir, dado que el código puede digitarse manualmente.
- **S-03 (Métricas de latencia y calidad de voz):** La fase 3 pide "comprobar latencia y calidad en EN/ES" sin definir umbrales numéricos. Pendiente definir la latencia máxima aceptable entre fin de turno del Cliente e inicio de respuesta del Agente_Voz. Nota: la pausa de fin de turno (1.5 a 2 s según `endpointingSensitivity`) es previa a esa latencia y no forma parte de ella.
- **S-04 (Detección de turnos en Nova 2 Sonic):** Nova 2 Sonic solo permite configurar `turnDetectionConfiguration.endpointingSensitivity` (HIGH: 1.5 s, MEDIUM: 1.75 s recomendado, LOW: 2 s); no existen umbrales en dB ni tiempos de corte configurables. La calibración en la tienda piloto consiste en elegir el nivel adecuado (LOW se considera en periodos ruidosos) y en medir el comportamiento observado con las pruebas del Requirement 6, cuyos objetivos numéricos (N = 100 turnos, 95%, 500 ms) están a validar en piloto. Como mitigación adicional de eco se evalúa la colocación de bocinas o el uso de audífonos en el Kiosco.
- **S-05 (Modelo de embeddings):** La sección de arquitectura menciona Titan Embed V2 y la fase 6 menciona "Titan Text V2". Se asume Titan Text Embeddings V2 (modelo de embeddings) para la Knowledge_Base.
- **S-06 (Autenticación del Kiosco):** El Cliente es anónimo pero el endpoint `/ws` exige JWT de Cognito. El Inbound Auth de AgentCore Runtime (disponible en us-east-1) se configurará con la discovery URL de `ultra-skincare-userpool` y la regla de allowed clients restringida a KioscoClient; la solicitud debe incluir un access token JWT. Sigue abierta la decisión de cómo el Kiosco anónimo obtiene ese token. Opciones: (a) un usuario de servicio del dispositivo en el grupo `kiosco`, con credenciales aprovisionadas por dispositivo; (b) identidades no autenticadas de un Cognito identity pool, que NO son compatibles con tokens JWT de user pool y requieren verificación antes de considerarse.
- **S-07 (Proceso de medición de objetivos de negocio):** El documento no define cómo se mide el tiempo promedio de asesoría (30 a 15 minutos) ni la conversión; pendiente acordar fuente de datos con BI.
- **S-08 (Datos de precio en el catálogo):** Se asume que todos los precios del CSV están en MXN y que el TOTAL SUGERIDO es la suma simple sin impuestos ni descuentos adicionales.
- **S-09 (Cobertura de acento y locale):** Nova 2 Sonic detecta y cambia de idioma automáticamente y ofrece voces políglotas (inglés US: tiffany, matthew; español es-US: lupe, carlos). La variante de español disponible es es-US (español latinoamericano), no específica de es-MX. Pendiente validar en el piloto la comprensión del acento mexicano, el vocabulario local y la naturalidad de la voz elegida.
- **S-10 (JWT en el handshake WebSocket del navegador):** Las conexiones WebSocket desde navegadores no pueden establecer encabezados Authorization arbitrarios. Pendiente definir en la fase de diseño cómo se transporta el JWT en el handshake hacia el Runtime_Agente (por ejemplo, mediante un mecanismo soportado por AgentCore); no se asume ningún mecanismo en este documento.

## Orientación de Implementación por Fases

Esta sección guía la generación de tareas. El enfoque es prototipado incremental: ver resultados desde el primer momento y luego empaquetar en CloudFormation.

### Fase 1: ETL local inmediato (minutos 0 a 60)
- Script `etl_catalog.py` con pandas; corrección de mojibake (ftfy o mapeos UTF-8); sanitización HTML (regex o BeautifulSoup); clasificación de las ~50 variantes de Funcion en los 4 pasos.
- Entregable visible: `catalog_normalized.json` y visualización en consola por paso.
- Generador de chunks Markdown por SKU: `productos/{sku}.md` y `productos/{sku}.metadata.json`.
- Cubre los requerimientos 1, 2, 3 y 4.

### Fase 2: Motor de recomendación y guardrails
- System Prompt de Claude Haiku 4.5: Asesor Virtual de Skincare de Ultrafemme Cancún; rutina de 4 pasos con EXCLUSIVAMENTE candidatos del catálogo; exactamente UN producto por paso; sin inventar productos ni combinar ingredientes fuera de los devueltos; sin afirmar compatibilidad química o médica entre marcas; razón basada en la columna Beneficios; `requiere_asesor: true` ante signos de alergia, acné severo o embarazo.
- Contrato JSON Schema estricto de `armar_rutina` (exactamente 4 pasos con SKUs válidos).
- Guardrail `ultra-skincare-guardrail` con temas denegados.
- Cubre los requerimientos 9, 10, 11 y 12.

### Fase 3: Agente de voz
- Contenedor Docker Python 3.12 ARM64 con Strands Agents y `BedrockNovaSonicModel`; pipeline WebSocket con Nova 2 Sonic.
- Herramientas asíncronas: `buscar_productos`, `detalle_producto`, `armar_rutina`, `guardar_recomendacion`.
- Configuración de `endpointingSensitivity` (parámetro de entorno, predeterminado MEDIUM) y de la voz políglota en sessionStart.
- Renovación de la conexión con Nova 2 Sonic antes del límite de 8 minutos, con contexto almacenado en `ultra-sesiones`.
- Publicación en ECR (`ultra-skincare-agent`), despliegue en AgentCore Runtime, validación del handshake `wss://.../ws` y comprobación de latencia y calidad en EN/ES.
- Cubre los requerimientos 5, 6, 7, 8, 24 y 22.9 a 22.10.

### Fase 4: Frontend
- SPA React, Vite y Tailwind.
- Kiosco: captura de audio PCM 16 kHz y streaming por WebSocket; transcripción en vivo y tarjetas de producto; QR con `qrcode.react` y código corto.
- Caja: autenticación Cognito (usuario `caja`); escáner QR con `html5-qrcode`, carga de foto o ingreso de código; llamada a API Gateway y botón "Marcar como Atendida".
- Cubre los requerimientos 14, 18, 19 y 20.

### Fase 5: Backend serverless y PubMed
- Tablas DynamoDB `ultra-productos`, `ultra-sesiones`, `ultra-recomendaciones`.
- Lambda `guardar_recomendacion` que valida la existencia de SKUs antes de insertar.
- Lambda `caja_api` con endpoint `/recomendacion/{id}` que devuelve la lista y actualiza el estado a atendida.
- Herramienta opcional `evidencia_ingrediente` (NCBI eSearch y eSummary) solo para ingredientes de productos elegidos, con caché en `ultra-evidencias-ingredientes` y API Key en Secrets Manager.
- Cubre los requerimientos 13, 15, 16, 17 y 23.

### Fase 6: Infraestructura como código
- `infrastructure.yaml`: parámetros, VPC y subnets si aplica, KMS, buckets S3 (hosting web, logs, raw data, KB source), DynamoDB on-demand, Cognito User Pool y clientes (KioscoClient, CajaClient).
- `services-and-runtime.yaml`: Bedrock Guardrails y Knowledge Base (Titan V2 con S3 Vectors), Lambdas (ETL, Caja API), API Gateway HTTP con Cognito JWT Authorizer, CloudFront con OAC, IAM de mínimo privilegio.
- Cubre los requerimientos 21 y 22. Nota: la estructura final de stacks del requerimiento 22.3 sigue la guía de repositorio de la siguiente sección.

### Estructura de repositorio sugerida

```
.kiro/project-spec.json
cloudformation/
  01-base-storage-db.yaml      # S3, DynamoDB, KMS, Budgets
  02-auth-cognito.yaml         # User Pool y App Clients Kiosco/Caja
  03-bedrock-kb-guardrail.yaml # KB S3 Vectors + Titan V2 + Guardrails
  04-api-and-lambdas.yaml      # Lambdas ETL y Caja, API Gateway HTTP
  05-frontend-hosting.yaml     # CloudFront + S3 + OAC
src/etl/handler.py, requirements.txt
src/agent/Dockerfile (ARM64), agent.py, tools.py, prompts.py
src/caja_api/handler.py
src/frontend/ (React + Vite + Tailwind: components/Kiosk.tsx, components/Cashier.tsx, package.json)
catalog/feeder-skincare-catalog.csv   # CSV piloto
```

Orden de despliegue con `aws cloudformation deploy`: `ultra-skincare-storage` (01, CAPABILITY_NAMED_IAM), `ultra-skincare-auth` (02), `ultra-skincare-bedrock` (03), `ultra-skincare-compute` (04, CAPABILITY_NAMED_IAM), `ultra-skincare-frontend` (05).
