# Asesor Virtual de Skincare por Voz (Grupo Ultra)

Spec: `.kiro/specs/skincare-voice-advisor/` (`requirements.md`, `design.md`, `tasks.md`, `ui-reference.md`).
Diseños de pantalla: `.kiro/specs/skincare-voice-advisor/design/`.

## Estructura

```
cloudformation/   Stacks de infraestructura (pendiente)
src/etl/          ETL del catálogo (pendiente)
src/agent/        Agente de voz (pendiente)
src/caja_api/     Lambda de Caja (rutas listas; falta la infraestructura)
src/frontend/     Vista_Cliente (Kiosco) y Vista_Caja (React + Vite + Tailwind)
catalog/          Catálogo CSV y catálogo de muestra
scripts/          Despliegue y pruebas de humo
```

## Prototipo del frontend (datos simulados)

Requiere Node 20 o superior.

```powershell
cd src/frontend
npm install
npm run dev
```

- Kiosco: http://localhost:5173/
  - `?scenario=handoff` simula una derivación al asesor.
  - `?scenario=lost` simula la pérdida de la conexión de voz.
  - `?lang=en` reproduce la conversación en inglés.
  - El enlace "Demo: ver rutina →" salta a la pantalla de rutina (solo en desarrollo).
- Caja: http://localhost:5173/caja
  - Usuario `caja` con cualquier contraseña.
  - Códigos de prueba: `ABC-234` (pendiente), `ATN-222` (atendida); cualquier otro da "no encontrada".
  - "Escanear con la cámara" y "Subir la foto del QR" simulan la lectura de `ABC-234`.

Otros comandos: `npm run typecheck`, `npm run build`, `npm test`.

## Conversación real con Nova 2 Sonic (local)

Por defecto el Kiosco habla con el agente real; `?mode=mock` usa el guion simulado de arriba.

Requisitos: Python 3.12 o superior, credenciales AWS configuradas (`aws sts get-caller-identity`) y acceso a
Nova 2 Sonic y a Claude Haiku 4.5 en `us-east-1`.

El entorno virtual va **fuera de OneDrive** (OneDrive bloquea archivos durante `pip install`):

```powershell
python -m venv $env:LOCALAPPDATA\skincare-venv
& $env:LOCALAPPDATA\skincare-venv\Scripts\python.exe -m pip install -r src/agent/requirements.txt

# Terminal 1: agente de voz (ws://127.0.0.1:8080/ws, sin autenticación: solo local)
& $env:LOCALAPPDATA\skincare-venv\Scripts\python.exe -m uvicorn server:app --app-dir src/agent --host 127.0.0.1 --port 8080

# Terminal 2: Kiosco
cd src/frontend; npm run dev
```

Abre http://localhost:5173/ y toca "Iniciar conversación". Chrome o Edge piden permiso de micrófono (en una
tablet por red, el micrófono exige HTTPS o `localhost`).

El agente usa el catálogo real procesado (out/etl/catalog_normalized.json) si existe; si no, el de muestra. Para cambiarlo: CATALOG_PATH.

Variables opcionales del agente: `NOVA_VOICE_ID` (`tiffany` o `matthew`), `ENDPOINTING_SENSITIVITY`
(`HIGH`, `MEDIUM`, `LOW`), `MIN_EXCHANGES` (mínimo de intercambios antes de proponer, 5 por defecto),
`ROUTINE_MODEL_ID`, `CATALOG_PATH`.

Pruebas manuales sin micrófono: `python scripts/smoke_nova.py` (conexión con Nova), `python scripts/try_engine.py`
(Motor_Rutina con Haiku) y `python scripts/ws_smoke.py 60 "Tengo la piel seca"` (cliente WebSocket por texto).

## ETL del catálogo (local)

```powershell
& $env:LOCALAPPDATA\skincare-venv\Scripts\python.exe -m pip install -r requirements-dev.txt
& $env:LOCALAPPDATA\skincare-venv\Scripts\python.exe scripts/make_sample_csv.py   # CSV sintético con problemas típicos
& $env:LOCALAPPDATA\skincare-venv\Scripts\python.exe src/etl/etl_catalog.py src/etl/feeder-skincare-catalog_v2.csv out/etl
```

Corrige la codificación (mojibake), quita el HTML, clasifica cada producto en uno de los 4 pasos y genera
`catalog_normalized.json` y los documentos de la Knowledge Base. Los mapas `src/etl/config/funcion_map.json`
y `column_map.json` salen del CSV real (`src/etl/feeder-skincare-catalog_v2.csv`). Los puntos de criterio por confirmar están en `_a_confirmar` dentro de `funcion_map.json`.

## Guía de recomendación del negocio

El Excel `src/etl/Preguntas sugeridas para recomendacion de tratamientos (1).xlsx` define el embudo de preguntas,
los 4 segmentos de cliente, los niveles de precio y 64 combinaciones curadas. Se convierte a `src/etl/config/guide.json`:

```powershell
& $env:LOCALAPPDATA\skincare-venv\Scripts\python.exe src/etl/build_guide.py "src/etl/Preguntas sugeridas para recomendacion de tratamientos (1).xlsx" out/etl/catalog_normalized.json
```

El agente la usa como orientación (sus productos curados pasan primero), no como receta: solo 17 de las 128
combinaciones tienen sus 3 productos en el catálogo actual. Las reglas de clasificación de productos sin `Funcion`
están en `src/etl/config/inference_rules.json` y los filtros de alcance (marcas, tipos de producto) en `settings.json`.

## Spike 4.8: salida estructurada con Claude Haiku 4.5

`python scripts/spike_structured_output.py` lista los perfiles de inferencia de Haiku 4.5 y hace una llamada a
Converse con `outputConfig.textFormat` (JSON Schema). Resultado del 06/10/2026 en `us-east-1`:

- Perfiles disponibles: `us.anthropic.claude-haiku-4-5-20251001-v1:0` y `global.anthropic.claude-haiku-4-5-20251001-v1:0`.
  `ROUTINE_MODEL_ID` ya usa el primero; no hace falta cambiarlo.
- `outputConfig.textFormat` funciona: la respuesta fue JSON válido que cumple el esquema (`stopReason=end_turn`).
- Una llamada pequeña (355 tokens de entrada) tardó unos 2 s. La rutina real envía más candidatos; el límite de
  lectura del cliente es de 10 s, así que conviene medir la latencia de `armar_rutina` con el catálogo completo.
- El esquema no admite `minimum` en enteros; por eso el rango de `beneficios_idx` se valida en código.

## Medición de latencia de las herramientas (06/10/2026)

`python scripts/measure_latency.py [repeticiones]` ejecuta `armar_rutina` y luego `ajustar_rutina` (más barato) con el
catálogo real procesado (343 productos) y Claude Haiku 4.5, para 6 perfiles distintos. Cada perfil hace 2 llamadas a
Bedrock. Para ver el avance en vivo, define `LATENCY_LOG` con una ruta de archivo.

| Herramienta | Mediana | p90 | Máximo | Fallos |
|---|---|---|---|---|
| `armar_rutina` | 2.9 s | 3.1 s | 3.1 s | 0 de 6 |
| `ajustar_rutina` | 2.3 s | 2.6 s | 2.6 s | 0 de 6 |

Casi todo es la llamada al modelo (más del 98 %); la búsqueda, el ranking y el guardado local suman decenas de
milisegundos. Es la medición de las herramientas, no de la conversación completa: no incluye la latencia de Nova 2 Sonic
ni del audio. Mientras corre la herramienta, el asesor queda en silencio unos 3 s; conviene que diga una frase corta
antes de llamarla (por ejemplo "déjame armar tu rutina").

## Piezas de producción del agente (opcionales)

Sin variables de AWS el agente sigue siendo local, como en el prototipo. Cada pieza se activa solo si están TODAS sus
variables; `GET /health` muestra en `piezas` cuáles quedaron activas.

| Pieza | Variables |
|---|---|
| Recomendaciones en DynamoDB | `RECOMMENDATIONS_TABLE` |
| Aviso al asesor por SNS y espera de confirmación | `HANDOFF_TOPIC_ARN` y `SESSIONS_TABLE` (opcional `HANDOFF_CONFIRM_BASE_URL`) |
| Sesión guardada en cada cambio | `SESSIONS_TABLE` |
| Guardrail en la entrada, la salida y el Motor_Rutina | `GUARDRAIL_ID` y `GUARDRAIL_VERSION` |
| Lecturas de PubMed | `PUBMED_ENABLED=true` (opcionales `EVIDENCE_TABLE` y `NCBI_API_KEY`) |

Con el Guardrail activo, un turno solo pasa si el Guardrail aprueba y no se detecta una condición sensible; cualquier
error o tiempo agotado lo bloquea y deriva al asesor.

## Caja con la API real

Por defecto la Caja usa datos simulados. Para hablar con Cognito y la API de Caja, crea `src/frontend/.env.local`
(está ignorado por git) con:

```
VITE_CAJA_API_URL=https://<api-id>.execute-api.us-east-1.amazonaws.com
VITE_COGNITO_USER_POOL_ID=us-east-1_XXXXXXXXX
VITE_COGNITO_CAJA_CLIENT_ID=<id del cliente CajaClient>
VITE_STORE_DOMAIN=tienda.ejemplo.com   # opcional: solo acepta QR de ese dominio
```

Si falta cualquiera de las tres primeras, se usa la API simulada. El inicio de sesión usa SRP (la contraseña no viaja
en claro) y la sesión vive en `sessionStorage`. La cámara exige HTTPS o `localhost`. Pruebas del frontend: `npm test`.

## Infraestructura (CloudFormation)

Las plantillas están en `cloudformation/` y pasan `cfn-lint`, pero **todavía no se han desplegado en ninguna cuenta**.

```powershell
# Solo valida las plantillas (no llama a AWS)
& $env:LOCALAPPDATA\skincare-venv\Scripts\cfn-lint.exe cloudformation\*.yaml

# Despliegue: muestra la cuenta y los recursos, y pide confirmación antes de tocar AWS
.\scripts\deploy.ps1 -BudgetAlertEmail finanzas@tienda.com -HandoffEmail piso@tienda.com
```

Puntos a tener en cuenta:
- El mínimo de TLS 1.2 de CloudFront solo se aplica con dominio y certificado propios (`-CustomDomainName`, `-CertificateArn`).
- El CORS de la API necesita el dominio de CloudFront, que solo existe después del stack frontend: el script vuelve a
  desplegar el stack compute con el dominio real.
- El contenedor del agente no es parte de los 5 stacks; se despliega aparte en AgentCore (tareas 7.15 a 7.17).
- Buckets, tablas, User Pool y repositorio ECR tienen `DeletionPolicy: Retain`: borrar un stack no borra los datos.

## Orden de despliegue (cuando exista la infraestructura)

`ultra-skincare-storage` → `ultra-skincare-auth` → `ultra-skincare-bedrock` → `ultra-skincare-compute` → `ultra-skincare-frontend`.
