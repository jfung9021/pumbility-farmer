[CmdletBinding()]
param(
  [string]$SupabaseRepo = "",
  [int]$FrontendPort = 3000,
  [int]$ApiPort = 3001
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $SupabaseRepo) {
  $SupabaseRepo = Join-Path (Split-Path -Parent $repoRoot) "bite-open-card-draw"
}
$configPath = Join-Path $SupabaseRepo "supabase\config.toml"
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
  throw "The sibling bite-open-card-draw Supabase configuration was not found."
}

$config = Get-Content -Raw -LiteralPath $configPath
$projectId = [regex]::Match(
  $config, '(?m)^project_id\s*=\s*"([a-zA-Z0-9_-]+)"'
).Groups[1].Value
$port = [regex]::Match($config, '(?ms)^\[db\].*?^port\s*=\s*(\d+)').Groups[1].Value
if ($projectId -ne "bite-open-card-draw" -or -not $port) {
  throw "The sibling repository is not the expected local Supabase project."
}

Push-Location $SupabaseRepo
try {
  $previousErrorActionPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "SilentlyContinue"
    $statusText = (& npx --yes supabase@2.114.0 status -o env 2>&1 | Out-String)
    $statusExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
  if ($statusExitCode -ne 0) {
    throw "The local Supabase stack is not running. Start Docker and the sibling stack first."
  }
} finally {
  Pop-Location
}

function Get-LocalValue {
  param([Parameter(Mandatory = $true)][string]$Name)

  $prefix = "$Name="
  $line = @($statusText -split "`r?`n" | Where-Object {
      $_.StartsWith($prefix)
    }) | Select-Object -First 1
  if (-not $line) {
    throw "Local Supabase status did not provide $Name."
  }
  return $line.Substring($prefix.Length).Trim('"')
}

function Test-ListeningPort {
  param([Parameter(Mandatory = $true)][int]$Port)

  return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

$logRoot = Join-Path $repoRoot ".local-data\pumbility-migration\local-self-review"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

$env:PUMBILITY_DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:$port/postgres"
$env:PUMBILITY_SUPABASE_URL = Get-LocalValue -Name "API_URL"
$env:PUMBILITY_SUPABASE_SERVICE_ROLE_KEY = Get-LocalValue -Name "SERVICE_ROLE_KEY"
$env:PUMBILITY_STORAGE_BUCKET = "pumbility-artifacts"

if (-not (Test-ListeningPort -Port $FrontendPort)) {
  Start-Process -FilePath "npm.cmd" -ArgumentList "run", "dev:local", "--", "-p", $FrontendPort `
    -WorkingDirectory $repoRoot -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logRoot "next-dev.stdout.log") `
    -RedirectStandardError (Join-Path $logRoot "next-dev.stderr.log") | Out-Null
}

if (-not (Test-ListeningPort -Port $ApiPort)) {
  Start-Process -FilePath "uv.exe" `
    -ArgumentList "run", "--frozen", "--with", "uvicorn", "uvicorn", "api_service:app", `
      "--host", "127.0.0.1", "--port", $ApiPort `
    -WorkingDirectory $repoRoot -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logRoot "fastapi.stdout.log") `
    -RedirectStandardError (Join-Path $logRoot "fastapi.stderr.log") | Out-Null
}

$checks = @(
  @{ Name = "frontend"; Uri = "http://127.0.0.1:$FrontendPort/" },
  @{ Name = "Supabase-backed API"; Uri = "http://127.0.0.1:$ApiPort/api/recommendations/players" }
)
foreach ($check in $checks) {
  $ready = $false
  for ($attempt = 0; $attempt -lt 60; $attempt++) {
    try {
      $response = Invoke-WebRequest -UseBasicParsing -Uri $check.Uri -TimeoutSec 3
      if ($response.StatusCode -eq 200) {
        $ready = $true
        break
      }
    } catch {
      # The bounded readiness loop reports one safe error after all attempts.
    }
    Start-Sleep -Seconds 1
  }
  if (-not $ready) {
    throw "$($check.Name) did not become ready; inspect the ignored local logs."
  }
}

Write-Output "Local Pumbility UI: http://localhost:$FrontendPort"
Write-Output "Supabase-backed API example: http://localhost:$ApiPort/api/recommendations/players"
Write-Output "Supabase Studio: http://localhost:54323"
Write-Output "No credentials or environment files were written."
