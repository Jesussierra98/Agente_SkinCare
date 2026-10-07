<#
.SYNOPSIS
  Despliega los 5 stacks de ultra-skincare, en orden: storage, auth, bedrock, compute, frontend.

.DESCRIPTION
  ANTES de tocar AWS muestra la cuenta y lo que se va a crear, y pide confirmacion (omitir con -Yes).
  Se detiene en el primer stack que falle, nombrando el stack y la causa.
  NO despliega el contenedor del agente (AgentCore): ver el mensaje final.

  NO HA SIDO EJECUTADO contra una cuenta real. Pruebalo primero en una cuenta de desarrollo.
  (Este archivo es solo ASCII a proposito: PowerShell 5 lee mal los acentos si no hay marca BOM.)

.EXAMPLE
  .\scripts\deploy.ps1 -BudgetAlertEmail finanzas@tienda.com -HandoffEmail piso@tienda.com
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$BudgetAlertEmail,
    [int]$BudgetLimitUsd = 100,
    [string]$HandoffEmail = "",
    [string]$ImageHosts = "",
    [string]$CustomDomainName = "",
    [string]$CertificateArn = "",
    [string]$AwsProfile = "",
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$Region = "us-east-1"
$Root = Split-Path -Parent $PSScriptRoot
$Templates = Join-Path $Root "cloudformation"
$Python = if ($env:SKINCARE_PYTHON) { $env:SKINCARE_PYTHON } else { Join-Path $env:LOCALAPPDATA "skincare-venv\Scripts\python.exe" }
$ProfileArgs = if ($AwsProfile) { @("--profile", $AwsProfile) } else { @() }

function Invoke-Aws {
    # Ejecuta `aws` y falla si el codigo de salida no es 0.
    $output = & aws @args @ProfileArgs --region $Region 2>&1
    if ($LASTEXITCODE -ne 0) { throw "aws $($args -join ' ') fallo:`n$output" }
    return $output
}

function Get-StackOutput([string]$Stack, [string]$Key) {
    $value = Invoke-Aws cloudformation describe-stacks --stack-name $Stack --query "Stacks[0].Outputs[?OutputKey=='$Key'].OutputValue" --output text
    return ($value | Out-String).Trim()
}

function Deploy-Stack([string]$Stack, [string]$File, [string[]]$Overrides) {
    Write-Host "`n=== $Stack ===" -ForegroundColor Cyan
    $deployArgs = @("cloudformation", "deploy", "--stack-name", $Stack, "--template-file", (Join-Path $Templates $File),
        "--capabilities", "CAPABILITY_NAMED_IAM", "--no-fail-on-empty-changeset")
    if ($Overrides.Count -gt 0) { $deployArgs += @("--parameter-overrides") + $Overrides }
    try {
        Invoke-Aws @deployArgs | Out-Host
    } catch {
        Write-Host "`nFALLO el stack '$Stack'. Ultimos eventos con error:" -ForegroundColor Red
        & aws cloudformation describe-stack-events --stack-name $Stack @ProfileArgs --region $Region `
            --query "StackEvents[?contains(ResourceStatus,'FAILED')].[LogicalResourceId,ResourceStatusReason]" --output text 2>&1 | Select-Object -First 8 | Out-Host
        throw "Se detiene el despliegue: fallo el stack '$Stack'."
    }
}

# ---------------------------------------------------------------------------------------------- confirmacion
$identity = Invoke-Aws sts get-caller-identity --output json | ConvertFrom-Json
Write-Host "Cuenta AWS : $($identity.Account)"
Write-Host "Identidad  : $($identity.Arn)"
Write-Host "Region     : $Region"
Write-Host @"

Se crearan o actualizaran 5 stacks, en este orden:
  1. ultra-skincare-storage   4 buckets S3, 4 tablas DynamoDB, llave KMS, Budget de $BudgetLimitUsd USD (avisa a $BudgetAlertEmail)
  2. ultra-skincare-auth      User Pool, grupos kiosco y caja, 2 clientes
  3. ultra-skincare-bedrock   Guardrail, bucket e indice de S3 Vectors, Knowledge Base
  4. ultra-skincare-compute   2 Lambdas, API HTTP, topico SNS, repositorio ECR, secreto y 3 roles IAM
  5. ultra-skincare-frontend  CloudFront + OAC

Genera costos (Knowledge Base, CloudFront, DynamoDB, Lambda) y crea roles IAM con nombre. Los buckets, tablas, User Pool
y el repositorio ECR tienen DeletionPolicy Retain: borrar un stack no borra tus datos.
"@
if (-not $Yes) {
    $answer = Read-Host "Continuar? (escribe 'si' para confirmar)"
    if ($answer -ne "si") { Write-Host "Cancelado. No se toco AWS."; exit 1 }
}

# ------------------------------------------------------------------------------------------------- despliegue
Deploy-Stack "ultra-skincare-storage" "01-base-storage-db.yaml" @("BudgetAlertEmail=$BudgetAlertEmail", "BudgetLimitUsd=$BudgetLimitUsd")

Write-Host "`nEmpaquetando las Lambdas..." -ForegroundColor Cyan
$build = Join-Path $Root "out\build"
& $Python (Join-Path $PSScriptRoot "package_lambdas.py") $build
if ($LASTEXITCODE -ne 0) { throw "No se pudieron empaquetar las Lambdas." }
$hashes = Get-Content (Join-Path $build "hashes.txt") | ConvertFrom-StringData
$rawBucket = Get-StackOutput "ultra-skincare-storage" "RawBucketName"
$etlKey = "artifacts/etl-$($hashes.etl).zip"
$cajaKey = "artifacts/caja-$($hashes.caja).zip"
Invoke-Aws s3 cp (Join-Path $build "etl.zip") "s3://$rawBucket/$etlKey" | Out-Null
Invoke-Aws s3 cp (Join-Path $build "caja.zip") "s3://$rawBucket/$cajaKey" | Out-Null

Deploy-Stack "ultra-skincare-auth" "02-auth-cognito.yaml" @()
Deploy-Stack "ultra-skincare-bedrock" "03-bedrock-kb-guardrail.yaml" @()

# El dominio de CloudFront solo existe despues del stack frontend: si ya existe, se reutiliza para el CORS.
$origin = "https://localhost:5173"
try { $existing = Get-StackOutput "ultra-skincare-frontend" "FrontendOrigin"; if ($existing) { $origin = $existing } } catch { }
$compute = @("CodeBucket=$rawBucket", "EtlCodeKey=$etlKey", "CajaCodeKey=$cajaKey", "FrontendOrigin=$origin", "HandoffEmail=$HandoffEmail")
Deploy-Stack "ultra-skincare-compute" "04-api-and-lambdas.yaml" $compute

Deploy-Stack "ultra-skincare-frontend" "05-frontend-hosting.yaml" @("ImageHosts=$ImageHosts", "CustomDomainName=$CustomDomainName", "CertificateArn=$CertificateArn")

$newOrigin = Get-StackOutput "ultra-skincare-frontend" "FrontendOrigin"
if ($newOrigin -and $newOrigin -ne $origin) {
    Write-Host "`nActualizando el CORS de la API con el dominio real ($newOrigin)..." -ForegroundColor Cyan
    $compute[3] = "FrontendOrigin=$newOrigin"
    Deploy-Stack "ultra-skincare-compute" "04-api-and-lambdas.yaml" $compute
}

# ------------------------------------------------------------------------------------------------ resumen
Write-Host "`nListo. Datos para los siguientes pasos:" -ForegroundColor Green
Write-Host "  API de Caja        : $(Get-StackOutput 'ultra-skincare-compute' 'ApiUrl')"
Write-Host "  User Pool          : $(Get-StackOutput 'ultra-skincare-auth' 'UserPoolId')"
Write-Host "  Cliente de Caja    : $(Get-StackOutput 'ultra-skincare-auth' 'CajaClientId')"
Write-Host "  Cliente de Kiosco  : $(Get-StackOutput 'ultra-skincare-auth' 'KioscoClientId')"
Write-Host "  Issuer / Discovery : $(Get-StackOutput 'ultra-skincare-auth' 'DiscoveryUrl')"
Write-Host "  Guardrail          : $(Get-StackOutput 'ultra-skincare-bedrock' 'GuardrailId') (version $(Get-StackOutput 'ultra-skincare-bedrock' 'GuardrailVersion'))"
Write-Host "  Knowledge Base     : $(Get-StackOutput 'ultra-skincare-bedrock' 'KnowledgeBaseId')"
Write-Host "  Rol del agente     : $(Get-StackOutput 'ultra-skincare-compute' 'AgentRuntimeRoleArn')"
Write-Host "  Repositorio ECR    : $(Get-StackOutput 'ultra-skincare-compute' 'AgentRepositoryUri')"
Write-Host "  Sitio              : $newOrigin"
Write-Host @"

PENDIENTE (no automatizado todavia):
  1. Frontend: en src/frontend crea .env.local con VITE_CAJA_API_URL, VITE_COGNITO_USER_POOL_ID, VITE_COGNITO_CAJA_CLIENT_ID,
     luego 'npm run build' y 'aws s3 sync dist s3://<bucket de hosting>' (y una invalidacion de CloudFront).
  2. Usuarios: crea los usuarios de Cognito y asignalos a los grupos kiosco y caja.
  3. Catalogo: sube el CSV a s3://$rawBucket/raw/ para que el ETL cargue DynamoDB y la Knowledge Base.
  4. Agente: construye la imagen ARM64, subela al repositorio ECR y despliegala en AgentCore con el rol de arriba
     (tareas 7.15 a 7.17; pasa GUARDRAIL_ID, GUARDRAIL_VERSION, KB_ID y los nombres de tablas como variables de entorno).
  5. Confirma la suscripcion de correo de SNS si pasaste -HandoffEmail (llega un correo de confirmacion).
"@
