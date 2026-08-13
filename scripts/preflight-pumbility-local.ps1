[CmdletBinding()]
param(
  [string]$DatabaseUrl = $env:PUMBILITY_DATABASE_URL,
  [string]$SupabaseRepo = "",
  [int]$MinimumFreeSpaceGb = 15
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Check {
  param(
    [Parameter(Mandatory = $true)][ValidateSet("PASS", "BLOCKED")][string]$Status,
    [Parameter(Mandatory = $true)][string]$Message
  )

  Write-Host ("[{0}] {1}" -f $Status, $Message)
}

function Test-LoopbackDatabaseUrl {
  param([Parameter(Mandatory = $true)][string]$Value)

  try {
    $uri = [System.Uri]$Value
  } catch {
    throw "PUMBILITY_DATABASE_URL is not a valid PostgreSQL URL. Its value was not printed."
  }

  $normalizedHost = $uri.Host.Trim([char[]]"[]")
  $parsedAddress = $null
  $isExactLoopback = $normalizedHost -eq "localhost"
  if ([System.Net.IPAddress]::TryParse($normalizedHost, [ref]$parsedAddress)) {
    $isExactLoopback = $parsedAddress.Equals([System.Net.IPAddress]::Loopback) -or
      $parsedAddress.Equals([System.Net.IPAddress]::IPv6Loopback)
  }

  if ($uri.Scheme -notin @("postgres", "postgresql") -or -not $isExactLoopback) {
    throw "Refusing a non-loopback PUMBILITY_DATABASE_URL. Only localhost, 127.0.0.1, or ::1 is allowed."
  }

  return $uri
}

function Find-SupabaseCommand {
  param([Parameter(Mandatory = $true)][string]$SiblingRepo)

  $globalCommand = Get-Command supabase -ErrorAction SilentlyContinue
  if ($globalCommand) {
    return $globalCommand.Source
  }

  $localCandidates = @(
    (Join-Path $SiblingRepo "node_modules\.bin\supabase.cmd"),
    (Join-Path $SiblingRepo "node_modules\.bin\supabase.ps1")
  )
  return $localCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $SupabaseRepo) {
  $SupabaseRepo = Join-Path (Split-Path -Parent $repoRoot) "bite-open-card-draw"
}

$blocked = [System.Collections.Generic.List[string]]::new()

if (-not $DatabaseUrl) {
  $blocked.Add("Set PUMBILITY_DATABASE_URL to the local Supabase PostgreSQL URL. The preflight never reads or edits .env.local.")
} else {
  try {
    $databaseUri = Test-LoopbackDatabaseUrl -Value $DatabaseUrl
    Write-Check PASS "Database target is loopback-only (value hidden)."
  } catch {
    [Console]::Error.WriteLine(("[BLOCKED] {0}" -f $_.Exception.Message))
    exit 2
  }
}

if (-not (Test-Path -LiteralPath $SupabaseRepo -PathType Container)) {
  $blocked.Add("Sibling Supabase repository was not found at the expected path: $SupabaseRepo")
} else {
  $configPath = Join-Path $SupabaseRepo "supabase\config.toml"
  if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    $blocked.Add("The sibling repository has no supabase\config.toml.")
  } else {
    $config = Get-Content -Raw -LiteralPath $configPath
    $projectMatch = [regex]::Match($config, '(?m)^project_id\s*=\s*"([a-zA-Z0-9_-]+)"')
    $portMatch = [regex]::Match($config, '(?ms)^\[db\].*?^port\s*=\s*(\d+)')
    if ($projectMatch.Groups[1].Value -ne "bite-open-card-draw") {
      $blocked.Add("The sibling Supabase project_id is not bite-open-card-draw.")
    } elseif (-not $portMatch.Success) {
      $blocked.Add("The sibling Supabase database port could not be read from config.toml.")
    } elseif ($DatabaseUrl -and $databaseUri.Port -ne [int]$portMatch.Groups[1].Value) {
      $blocked.Add("PUMBILITY_DATABASE_URL does not use the sibling project's configured local database port.")
    } else {
      Write-Check PASS "Sibling bite-open-card-draw Supabase configuration is present and matches the local target."
    }
  }
}

$systemDrive = Get-PSDrive -Name C -ErrorAction SilentlyContinue
if (-not $systemDrive) {
  $blocked.Add("The preflight could not determine free space on C:.")
} else {
  $freeGb = [math]::Floor($systemDrive.Free / 1GB)
  if ($systemDrive.Free -lt ($MinimumFreeSpaceGb * 1GB)) {
    $blocked.Add(
      "C: has approximately $freeGb GB free; at least $MinimumFreeSpaceGb GB is required before local Supabase setup. " +
      "Relocate Docker Desktop's disk image/data to D: or free 15-20 GB, then rerun this preflight."
    )
  } else {
    Write-Check PASS "C: has at least $MinimumFreeSpaceGb GB free."
  }
}

$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCommand) {
  $blocked.Add("Docker CLI is not installed. Do not install it until the free-space check passes.")
} else {
  $previousErrorActionPreference = $ErrorActionPreference
  try {
    # Windows PowerShell can promote native stderr to an ErrorRecord under Stop. Suppress both
    # streams so daemon diagnostics cannot interrupt the remaining read-only preflight checks.
    $ErrorActionPreference = "SilentlyContinue"
    & $dockerCommand.Source info --format "{{.ServerVersion}}" 2>&1 | Out-Null
    $dockerExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }

  if ($dockerExitCode -ne 0) {
    $blocked.Add("Docker Desktop's Linux daemon is not running. Start it only after the free-space check passes.")
  } else {
    Write-Check PASS "Docker Desktop's Linux daemon is running."
  }
}

$supabaseCommand = Find-SupabaseCommand -SiblingRepo $SupabaseRepo
if (-not $supabaseCommand) {
  $blocked.Add("Supabase CLI is unavailable. Install a pinned CLI only after the free-space check passes; this script never installs software.")
} else {
  Write-Check PASS "Supabase CLI is available."
}

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
  $versionText = (& $venvPython --version 2>&1 | Out-String).Trim()
  if ($LASTEXITCODE -ne 0 -or $versionText -notmatch '^Python 3\.12(?:\.|$)') {
    $blocked.Add("The project .venv does not provide Python 3.12.")
  } else {
    Write-Check PASS "Project .venv provides Python 3.12."
  }
} elseif (Get-Command uv -ErrorAction SilentlyContinue) {
  Write-Check PASS "uv is available; use 'uv run python', never bare 'python'."
} else {
  $blocked.Add("Neither .venv\Scripts\python.exe nor uv is available.")
}

$snapshotManifests = @(
  (Join-Path $repoRoot ".local-data\piu-scores\phoenix1\current\snapshot_manifest.json"),
  (Join-Path $repoRoot ".local-data\piu-scores\phoenix2\current\snapshot_manifest.json")
)
$missingManifests = @($snapshotManifests | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
if ($missingManifests.Count -gt 0) {
  $blocked.Add("One or more private local snapshot manifests are missing. Paths and contents were not printed.")
} else {
  Write-Check PASS "Phoenix 1 and Phoenix 2 private snapshot manifests are present."
}

if ($blocked.Count -gt 0) {
  Write-Host ""
  Write-Host "Local Pumbility Supabase setup is BLOCKED. Nothing was installed, started, reset, or written."
  foreach ($reason in $blocked) {
    Write-Check BLOCKED $reason
  }
  exit 1
}

Write-Host ""
Write-Host "Preflight passed. No services were started and no files or environment files were modified."
Write-Host "Continue with docs\pumbility-migration\local-testing.md."
