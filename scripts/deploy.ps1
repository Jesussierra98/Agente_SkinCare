<#
.SYNOPSIS
  Despliega el Asesor Virtual de Skincare en AWS (us-east-1).
.DESCRIPTION
  Ejecuta `aws cloudformation deploy` en el orden storage -> auth -> bedrock -> compute -> frontend y después
  publica la imagen, crea el Runtime_Agente, sube la SPA y lanza la prueba de humo. La lógica vive en
  scripts/deploy.py (misma para PowerShell y bash). Antes de tocar la cuenta muestra los recursos y pide confirmación.
.EXAMPLE
  ./scripts/deploy.ps1 --plan --budget-email ti@ejemplo.com
.EXAMPLE
  ./scripts/deploy.ps1 --budget-email ti@ejemplo.com --store-domain tienda.ejemplo.com
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$venv = Join-Path $env:LOCALAPPDATA 'skincare-venv\Scripts\python.exe'
$python = if ($env:PYTHON) { $env:PYTHON } elseif (Test-Path $venv) { $venv } else { 'python' }

& $python (Join-Path $root 'scripts/deploy.py') @args
exit $LASTEXITCODE
