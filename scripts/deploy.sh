#!/usr/bin/env bash
# Despliega el Asesor Virtual de Skincare en AWS (us-east-1).
#
# Ejecuta `aws cloudformation deploy` en el orden storage -> auth -> bedrock -> compute -> frontend y después
# publica la imagen, crea el Runtime_Agente, sube la SPA y lanza la prueba de humo. La lógica vive en
# scripts/deploy.py (misma para PowerShell y bash). Antes de tocar la cuenta muestra los recursos y pide confirmación.
#
#   ./scripts/deploy.sh --plan --budget-email ti@ejemplo.com
#   ./scripts/deploy.sh --budget-email ti@ejemplo.com --store-domain tienda.ejemplo.com
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON:-python3}"

exec "$python_bin" "$root/scripts/deploy.py" "$@"
