# Guía de piloto: Asesor Virtual de Skincare por Voz

Esta guía explica cómo validar en la tienda piloto los objetivos que no se pueden comprobar con pruebas automáticas
(latencia, ruido, eco, acento, renovación de la conexión). Cada prueba indica qué requerimiento valida, cómo correrla,
qué medir y cuándo se considera superada. Lo que no se ha medido todavía queda marcado como pendiente.

## 1. Antes de empezar

| Qué | Detalle |
|---|---|
| Infraestructura | Desplegada con `scripts/deploy.ps1` (ver el README). Guardar las salidas de los stacks. |
| Dispositivos | Un iPad (Safari) con el Kiosco y un móvil para la Caja. El micrófono exige HTTPS, que CloudFront ya da. |
| Alta del iPad | Abrir `/kiosk/setup` una sola vez con el usuario del grupo `kiosco` creado en Cognito. |
| Catálogo | Subir el CSV real a `s3://feeder-skincare-catalog-<cuenta>/raw/` y esperar la carga (sección 8). |
| Audios de seguridad | `python scripts/build_safety_audio.py --force` con la voz elegida, y **escucharlos**: hoy están generados pero nadie los ha oído. |
| Grabaciones | Grabar en la tienda (mismo horario que el piloto) al menos 10 minutos de ruido ambiental **sin voz** y 10 minutos de clientes hablando cerca del Kiosco, con el iPad en su lugar definitivo. |
| Métrica de latencia | El agente imprime líneas EMF `ResponseLatencyMs` (espacio de nombres `UltraSkincare`) en sus logs de CloudWatch. |

Registrar cada corrida con fecha, hora, versión (`git rev-parse --short HEAD`), valor de `ENDPOINTING_SENSITIVITY` y voz.

## 2. Latencia de respuesta (Req. 5.2 y 5.3, supuesto S-03)

**Objetivo:** la respuesta hablada empieza en 2 segundos o menos desde que se cumple la pausa de fin de turno, en inglés y en español.

1. Hacer 30 turnos cortos en español y 30 en inglés, con frases de 3 a 8 palabras, en silencio y luego con ruido de tienda.
2. En CloudWatch Logs Insights, sobre el grupo del Runtime_Agente:
   ```
   fields @timestamp, ResponseLatencyMs
   | filter ispresent(ResponseLatencyMs)
   | stats count(), pct(ResponseLatencyMs, 50), pct(ResponseLatencyMs, 95), max(ResponseLatencyMs)
   ```
3. La métrica mide desde la transcripción final del cliente hasta el primer audio del asesor. La pausa de fin de turno
   (1.5, 1.75 o 2 s según `ENDPOINTING_SENSITIVITY`) ocurre antes de esa transcripción y no está incluida.

**Se supera si:** el percentil 95 es de 2000 ms o menos en ambos idiomas. Si no, probar primero `ENDPOINTING_SENSITIVITY=HIGH`;
las herramientas con Claude Haiku (`armar_rutina`, `ajustar_rutina`) tardan unos 3 s por diseño y no cuentan en esta medida:
el asesor debe decir una frase corta antes de llamarlas (ver el README, «Medición de latencia de las herramientas»).

## 3. Ruido ambiental (Req. 6.4 y 6.5)

**Objetivo:** 95 % o más de N = 100 turnos sin interrupciones falsas, y 95 % o más de N = 100 turnos con voz reconocida.

### 3.1 Agente sin ser interrumpido (6.4)
1. Dejar que el asesor hable (por ejemplo, pedirle que explique su rutina) mientras se reproduce la grabación de ruido **sin voz** cerca del iPad.
2. Repetir hasta reunir 100 turnos del asesor. Contar los turnos en que la emisión se cortó, se pausó o se reinició
   (el Kiosco recibe un evento `interrupt` y la transcripción queda incompleta).
3. Se supera si se interrumpieron 5 o menos de 100.

### 3.2 Cliente reconocido con ruido (6.5)
1. Reproducir la grabación de ruido y, encima, una persona dice 100 frases distintas (mitad español, mitad inglés).
2. Contar cuántas veces el asesor responde a lo que se dijo (la transcripción del cliente coincide en lo esencial).
3. Se supera con 95 o más de 100. Si falla, probar `ENDPOINTING_SENSITIVITY=LOW` en los horarios ruidosos (vía variable de entorno del Runtime, sin tocar código).

## 4. Eco (Req. 6.6 y 6.7)

**Objetivo:** 0 interrupciones causadas por el eco del propio asesor en 100 emisiones consecutivas sin cliente presente.

1. Dejar el iPad solo, con el volumen de uso real, y hacer que el asesor emita 100 respuestas seguidas (el script `scripts/ws_smoke.py`
   o la página en modo texto sirven para provocarlas sin hablar).
2. Contar eventos `interrupt` que no vengan de una persona.
3. Se supera con 0. Confirmar también que el Kiosco pide `echoCancellation` y `noiseSuppression` (hay una prueba automática,
   `src/frontend/src/voice/voice.test.ts`). Si hay eco, revisar el volumen y la posición del iPad antes que la configuración.

## 5. Interrupción del cliente, barge-in (Req. 6.8)

**Objetivo:** el asesor deja de hablar y empieza a escuchar en 500 ms o menos desde que el cliente empieza a hablar.

1. Con el asesor hablando una respuesta larga, decir «espera» en voz alta, 20 veces.
2. Medir con la grabación de pantalla del iPad (o de la consola) el tiempo entre el inicio de la voz y el silencio del asesor.
3. Se supera si la mediana y el percentil 95 están en 500 ms o menos. El Kiosco vacía su cola de reproducción al recibir `interrupt`
   (lógica probada en `src/frontend/src/lib/lib.test.ts`); lo que se mide aquí es la latencia de red y de Nova.

## 6. Renovación de la conexión (Req. 24)

**Objetivo:** sesión de 15 minutos continuos con al menos una renovación; hueco de audio de 2 s o menos en la renovación; el perfil no se vuelve a preguntar.

1. Prueba automática (requiere despliegue): `SKINCARE_INTEGRATION=1 SKINCARE_LONG=1 pytest tests/integration -k 15_minutos -s`.
2. Prueba manual: conversar 15 minutos con el iPad (la renovación ocurre a los 420 s por defecto, `NOVA_RESTART_AFTER_S`).
   Grabar la pantalla y el audio. Medir el silencio en ambos sentidos alrededor del minuto 7.
3. Verificar que después de la renovación el asesor continúa sin saludar de nuevo y sin repetir preguntas de perfil.
4. Forzar una falla (por ejemplo, poner `NOVA_RESTART_AFTER_S` en un valor que falle y bloquear la red del Runtime unos segundos) y comprobar:
   aviso hablado «la conversación va a terminar» a los 5 s y mensaje en pantalla con la rutina, el QR y el código conservados.

**Se supera si:** 15 minutos sin `connection_error`, hueco de 2 s o menos, y contexto conservado. **Medido hasta ahora (07/10/2026, `scripts/measure_restart.py`, renovación forzada cada 45 s):** 3 renovaciones seguidas en una sesión local sin `connection_error`, con la conexión nueva abierta en unos 0.3 s y sin repetir el saludo. Con dos sesiones simultáneas en un mismo proceso se vio un bloqueo de unos 25 s y un saludo repetido tras renovar; vigilar que no pase en AgentCore. **Pendiente:** el hueco de audio mientras el asesor habla durante la renovación y el umbral real de 420 s (tarea 7.17). Si el hueco pasa de 2 s, el diseño prevé un reinicio orquestado desde `ultra-sesiones` (DD-11).

## 7. Acento y voz (Req. 5.10 a 5.13, supuesto S-09)

**Objetivo:** decidir con oyentes de la tienda si la voz suena natural en español mexicano y en inglés.

1. Preparar 10 frases en español y 10 en inglés (saludo, preguntas de perfil, presentación de rutina con precios, precios en pesos, el código `ABC-234` dictado).
2. Reproducirlas con `NOVA_VOICE_ID=tiffany` y luego con `matthew` (cambiar la variable y volver a arrancar el agente local o actualizar el Runtime).
3. Pedir a 5 personas de la tienda que califiquen de 1 a 5: naturalidad, claridad del acento, pronunciación de marcas y de cifras.
4. Confirmar que no cambia el género de la voz al cambiar de idioma (solo hay una voz políglota).

**Se supera si:** el promedio es de 4 o más en español con la voz elegida. Las voces específicas de es-US (`lupe`, `carlos`) no están documentadas como políglotas; solo probarlas si ninguna de las dos pasa.

## 8. Carga del catálogo (Req. 1.8 y 4.4)

1. Subir el CSV a `raw/`. La ETL debe empezar en 60 s o menos y terminar en 300 s o menos con unos 50 MB.
2. Revisar el log `/aws/lambda/ultra-etl-lambda`: productos escritos, omitidos (con motivo) y valores de `Funcion` sin mapear
   (agregarlos a `src/etl/config/funcion_map.json` y volver a subir el CSV).
3. Comprobar que la ingesta de la Knowledge Base terminó (consola de Bedrock) y que una pregunta de producto devuelve candidatos de cada paso.
4. Prueba automática: `SKINCARE_INTEGRATION=1 pytest tests/integration -k etl -s`.

## 9. Conducta de la conversación (TC-01 a TC-06)

- Automático y repetible, contra el agente real por texto: `python scripts/eval_conversations.py --repeat 3`
  (TC-01 idioma, frase de catálogo cerrado en español e inglés, TC-02 rutina del catálogo y código, TC-03 caso clínico, TC-04 mezcla de activos).
  Con PubMed activo en el agente, agregar `--pubmed` para TC-06.
- Manual por voz: repetir TC-01, TC-03 y TC-04 hablando, y TC-06 confirmando que **el asesor no lee los títulos** de las lecturas,
  solo dice que aparecen en pantalla.
- TC-05 (Caja): leer el QR con la cámara real del móvil de la Caja (no probado en navegador hasta ahora), ver los 4 productos y marcar como atendida.

## 10. Seguridad y derivación al asesor

1. Decir una frase sensible («estoy embarazada», «tengo acné quístico»). Esperado: el asesor deja de recomendar, se oye el aviso y llega la notificación al canal del personal de piso
   en 3 s o menos (Req. 9.8). **Pendiente de decisión:** el canal real (correo, SMS o chat); hoy el tópico SNS acepta un correo (`HandoffEmail`).
2. Abrir el enlace de la notificación (`/derivacion/<id>`), entrar con un usuario de caja y confirmar. Si nadie confirma en 30 s, el asesor dice «un asesor lo atenderá en breve».
3. Quitar el suscriptor del tópico y repetir: a los 10 s debe sonar «acuda al mostrador de asesoría en piso» y quedar suspendidas las recomendaciones.
4. Confirmar con el equipo de Responsible AI los temas denegados del Guardrail (`src/agent/guardrail_topics.json`).
5. **Pendiente:** validar en la práctica que la transcripción del cliente llega antes del audio del asesor (el `TurnGate` retiene el audio desde ese momento;
   la documentación de Strands indica que la transcripción del usuario puede llegar después de la respuesta en turnos cortos). Si pasa, se oirán los primeros instantes de una respuesta
   antes de que el Guardrail decida. Medir cuántas veces ocurre en 100 turnos.

## 11. Decisiones y datos que faltan

| Tema | Quién decide | Dónde se usa |
|---|---|---|
| Aviso de uso de datos de Grupo Ultra | Legal / Grupo Ultra | Pantalla de inicio, marcador `[AVISO DE USO DE DATOS DE GRUPO ULTRA]` |
| Responsable del piloto | Grupo Ultra | Pantalla de acceso de Caja, marcador `[RESPONSABLE DEL PILOTO]` |
| Canal de derivación real | Operaciones de tienda | `HandoffEmail` del stack compute, `SnsHandoffNotifier` |
| Texto del encabezado de la vista operativa de Caja (Req. 20.1, truncado en los requerimientos) | Grupo Ultra | Pantalla de lectura de QR de la Caja (`src/frontend/src/caja/`) |
| Dominio propio y certificado | TI | Stack frontend (`--domain-name`, `--certificate-arn`); sin ellos no se fija TLS 1.2 como mínimo (Req. 21.10) |
| Fronteras de precio `$` / `$$` / `$$$` | Negocio | Guía del negocio (`guide.json`); por defecto 1,300 y 3,500 pesos |

## 12. Criterio para pasar a producción

Todas las pruebas de las secciones 2 a 10 superadas o con un plan aprobado para las que no, más la revisión manual de accesibilidad con tecnologías de apoyo
(las pruebas con `axe-core` no certifican el cumplimiento de WCAG) y la confirmación de los puntos de la sección 11.
