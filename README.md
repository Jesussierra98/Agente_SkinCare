# Asesor Virtual de Skincare por Voz (Grupo Ultra)

Spec: `.kiro/specs/skincare-voice-advisor/` (`requirements.md`, `design.md`, `tasks.md`, `ui-reference.md`).
Diseños de pantalla: `.kiro/specs/skincare-voice-advisor/design/`.

## Estructura

```
cloudformation/   Stacks de infraestructura (pendiente)
src/etl/          ETL del catálogo (pendiente)
src/agent/        Agente de voz (pendiente)
src/caja_api/     Lambda de Caja (pendiente)
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

Variables opcionales del agente: `NOVA_VOICE_ID` (`tiffany` o `matthew`), `ENDPOINTING_SENSITIVITY`
(`HIGH`, `MEDIUM`, `LOW`), `MIN_EXCHANGES` (mínimo de intercambios antes de proponer, 5 por defecto),
`ROUTINE_MODEL_ID`, `CATALOG_PATH`.

Pruebas manuales sin micrófono: `python scripts/smoke_nova.py` (conexión con Nova), `python scripts/try_engine.py`
(Motor_Rutina con Haiku) y `python scripts/ws_smoke.py 60 "Tengo la piel seca"` (cliente WebSocket por texto).

## Orden de despliegue (cuando exista la infraestructura)

`ultra-skincare-storage` → `ultra-skincare-auth` → `ultra-skincare-bedrock` → `ultra-skincare-compute` → `ultra-skincare-frontend`.
