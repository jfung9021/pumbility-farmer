[CmdletBinding()]
param(
    [ValidateSet("analysis", "tier-list", "recommendation-players", "recommendation-player", "job-status")]
    [string[]]$Domains = @(),
    [string]$DomainCsv = "",
    [ValidateRange(1, 100000)]
    [int]$Samples = 100,
    [ValidateRange(0, 1000)]
    [int]$WarmupSamples = 3,
    [ValidateRange(0, 1440)]
    [double]$WindowMinutes = 15,
    [switch]$SkipP99,
    [switch]$ExpectCanaryTelemetry,
    [switch]$SuppressBaseHost,
    [string]$JobId = "",
    [uri]$BaseUrl = "https://pumbility-farmer.vercel.app",
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"

if (-not [string]::IsNullOrWhiteSpace($DomainCsv)) {
    if ($Domains.Count -gt 0) {
        throw "Specify either Domains or DomainCsv, not both."
    }
    $Domains = @($DomainCsv.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}
$allowedDomains = @("analysis", "tier-list", "recommendation-players", "recommendation-player", "job-status")
if ($Domains.Count -eq 0 -or @($Domains | Where-Object { $_ -notin $allowedDomains }).Count -gt 0) {
    throw "At least one supported probe domain is required."
}
if (-not $SkipP99 -and $Samples -lt 100) {
    throw "P99 scoring requires at least 100 scored samples per domain. Use -SkipP99 only for a non-gating smoke run."
}
if (@($Domains | Select-Object -Unique).Count -ne $Domains.Count) {
    throw "Each probe domain may be specified only once."
}
if ($Domains -contains "job-status" -and [string]::IsNullOrWhiteSpace($JobId)) {
    throw "The job-status domain requires -JobId for a current supervised job."
}
if ($BaseUrl.Scheme -ne "https" -and -not $BaseUrl.IsLoopback) {
    throw "The probe accepts HTTPS endpoints or an HTTP loopback endpoint used by focused tests."
}

$curlCommand = Get-Command curl.exe -ErrorAction SilentlyContinue
if ($null -eq $curlCommand) {
    $curlCommand = Get-Command curl -ErrorAction SilentlyContinue
}
if ($null -eq $curlCommand) {
    throw "curl is required so TTFB and download time can be measured separately."
}

$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $repositoryRoot ".local-data\pumbility-latency-probes"
}
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$null = New-Item -ItemType Directory -Path $OutputDirectory -Force

$runId = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ", [Globalization.CultureInfo]::InvariantCulture)
$rawSamplesPath = Join-Path $OutputDirectory ("pumbility-latency-{0}-samples.jsonl" -f $runId)
$summaryPath = Join-Path $OutputDirectory ("pumbility-latency-{0}-summary.json" -f $runId)
$baseAddress = $BaseUrl.AbsoluteUri.TrimEnd("/")
$utf8WithoutBom = [Text.UTF8Encoding]::new($false)
$allRecords = [Collections.Generic.List[object]]::new()

$telemetryEventsPerSuccessfulRequest = @{
    "analysis" = 1
    "tier-list" = 1
    "recommendation-players" = 1
    # This endpoint canary-reads the player index and then the selected-player artifact.
    "recommendation-player" = 2
    "job-status" = 1
}

function Convert-InvariantDouble([string]$Value) {
    return [double]::Parse($Value, [Globalization.CultureInfo]::InvariantCulture)
}

function Get-SafeResponseHeader([string]$HeaderText, [string]$Name) {
    $matches = [regex]::Matches(
        $HeaderText,
        ("(?im)^{0}:\s*([^\r\n]+)" -f [regex]::Escape($Name))
    )
    if ($matches.Count -eq 0) {
        return $null
    }
    return $matches[$matches.Count - 1].Groups[1].Value.Trim()
}

function Add-RawRecord([object]$Record) {
    $allRecords.Add($Record)
    $line = $Record | ConvertTo-Json -Depth 5 -Compress
    [IO.File]::AppendAllText($rawSamplesPath, $line + [Environment]::NewLine, $utf8WithoutBom)
}

function Invoke-ProbeRequest(
    [string]$Domain,
    [string]$Path,
    [string]$Phase,
    [int]$SampleIndex,
    [bool]$CandidateTelemetryExpected
) {
    $bodyPath = [IO.Path]::GetTempFileName()
    $headerPath = [IO.Path]::GetTempFileName()
    $errorPath = [IO.Path]::GetTempFileName()
    $payload = $null
    try {
        $separator = if ($Path.Contains("?")) { "&" } else { "?" }
        $nonce = [Guid]::NewGuid().ToString("N")
        $requestUrl = "{0}{1}{2}probeNonce={3}" -f $baseAddress, $Path, $separator, $nonce
        $writeOut = "%{http_code}`t%{time_starttransfer}`t%{time_total}`t%{size_download}`t%{content_type}"
        $curlArguments = @(
            "--silent",
            "--show-error",
            "--compressed",
            "--connect-timeout", "15",
            "--max-time", "90",
            "--header", "Accept: application/json",
            "--header", "Cache-Control: no-cache, no-store, max-age=0",
            "--header", "Pragma: no-cache",
            "--output", $bodyPath,
            "--dump-header", $headerPath,
            "--write-out", $writeOut,
            $requestUrl
        )

        $curlOutput = & $curlCommand.Source @curlArguments 2> $errorPath
        $curlExitCode = $LASTEXITCODE
        $fields = @(([string]$curlOutput).Split("`t"))
        $httpStatus = if ($fields.Count -ge 1 -and $fields[0] -match "^\d{3}$") { [int]$fields[0] } else { 0 }
        $ttfbMs = if ($fields.Count -ge 2) { 1000 * (Convert-InvariantDouble $fields[1]) } else { 0 }
        $networkTotalMs = if ($fields.Count -ge 3) { 1000 * (Convert-InvariantDouble $fields[2]) } else { 0 }
        $downloadMs = [Math]::Max(0, $networkTotalMs - $ttfbMs)
        $wireBytes = if ($fields.Count -ge 4) { [long](Convert-InvariantDouble $fields[3]) } else { 0 }
        $contentTypeFromCurl = if ($fields.Count -ge 5) { [string]$fields[4] } else { "" }
        $headerText = if ((Get-Item -LiteralPath $headerPath).Length -gt 0) {
            [IO.File]::ReadAllText($headerPath)
        } else {
            ""
        }
        $contentEncoding = Get-SafeResponseHeader $headerText "Content-Encoding"
        $vercelCache = Get-SafeResponseHeader $headerText "x-vercel-cache"
        if (-not [string]::IsNullOrWhiteSpace($vercelCache)) {
            $vercelCache = $vercelCache.ToUpperInvariant()
            if ($vercelCache -notin @("BYPASS", "MISS", "HIT", "STALE", "PRERENDER")) {
                $vercelCache = "OTHER"
            }
        }
        $cacheHit = $vercelCache -eq "HIT"

        $bodyBytes = (Get-Item -LiteralPath $bodyPath).Length
        $jsonParseMs = 0.0
        $errorKind = $null
        if ($curlExitCode -ne 0) {
            $errorKind = "transport"
        } elseif ($httpStatus -ne 200) {
            $errorKind = "http-status"
        } elseif ($bodyBytes -eq 0) {
            $errorKind = "empty-body"
        } elseif ($contentTypeFromCurl -notmatch "(?i)^application/json(?:;|$)") {
            $errorKind = "content-type"
        } else {
            try {
                $bodyText = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($bodyPath))
                $parseTimer = [Diagnostics.Stopwatch]::StartNew()
                try {
                    $payload = $bodyText | ConvertFrom-Json
                } finally {
                    $parseTimer.Stop()
                    $jsonParseMs = $parseTimer.Elapsed.TotalMilliseconds
                }
                if ($null -eq $payload) {
                    $errorKind = "invalid-json"
                }
            } catch {
                $errorKind = "invalid-json"
            }
        }
        if ($null -eq $errorKind -and $cacheHit) {
            $errorKind = "cache-hit"
        }
        $responseSha256 = $null
        if ($null -eq $errorKind) {
            $responseSha256 = (
                Get-FileHash -LiteralPath $bodyPath -Algorithm SHA256
            ).Hash.ToLowerInvariant()
        }

        $record = [ordered]@{
            recordedAtUtc = [DateTime]::UtcNow.ToString("o", [Globalization.CultureInfo]::InvariantCulture)
            domain = $Domain
            phase = $Phase
            sampleIndex = $SampleIndex
            ok = $null -eq $errorKind
            errorKind = $errorKind
            httpStatus = $httpStatus
            ttfbMs = [Math]::Round($ttfbMs, 3)
            downloadMs = [Math]::Round($downloadMs, 3)
            networkTotalMs = [Math]::Round($networkTotalMs, 3)
            jsonParseMs = [Math]::Round($jsonParseMs, 3)
            endToEndMs = [Math]::Round($networkTotalMs + $jsonParseMs, 3)
            wireBytes = $wireBytes
            decodedBodyBytes = $bodyBytes
            # Exact response identity without retaining response bodies or request details.
            responseSha256 = $responseSha256
            contentEncoding = if ([string]::IsNullOrWhiteSpace($contentEncoding)) { "none" } else { $contentEncoding.ToLowerInvariant() }
            vercelCache = if ([string]::IsNullOrWhiteSpace($vercelCache)) { "absent" } else { $vercelCache }
            cacheBypassRequested = $true
            cacheBypassSatisfied = -not $cacheHit
            candidateTelemetryExpected = $CandidateTelemetryExpected
            expectedCandidateReadEvents = if ($CandidateTelemetryExpected) {
                $telemetryEventsPerSuccessfulRequest[$Domain]
            } else {
                0
            }
        }
        Add-RawRecord $record
        return [pscustomobject]@{
            Record = [pscustomobject]$record
            Payload = $payload
        }
    } finally {
        foreach ($temporaryPath in @($bodyPath, $headerPath, $errorPath)) {
            if (Test-Path -LiteralPath $temporaryPath) {
                Remove-Item -LiteralPath $temporaryPath -Force
            }
        }
    }
}

$paths = @{
    "analysis" = "/api/analyze?mix=phoenix2"
    "tier-list" = "/api/tier-list?mix=phoenix2"
    "recommendation-players" = "/api/recommendations/players"
    "job-status" = "/api/analyze?mix=phoenix2&jobId=$([uri]::EscapeDataString($JobId.Trim()))"
}

if ($Domains -contains "recommendation-player") {
    # This discovery request is retained and explicitly included in the expected
    # recommendation-players telemetry count when that domain is in the group.
    $discoveryTelemetryExpected = (
        $ExpectCanaryTelemetry -and $Domains -contains "recommendation-players"
    )
    $discovery = Invoke-ProbeRequest `
        -Domain "recommendation-players" `
        -Path $paths["recommendation-players"] `
        -Phase "discovery" `
        -SampleIndex 0 `
        -CandidateTelemetryExpected $discoveryTelemetryExpected
    if (-not $discovery.Record.ok) {
        throw "The sanitized recommendation-player discovery request failed."
    }
    $playerKey = [string]$discovery.Payload.players[0].playerKey
    if ([string]::IsNullOrWhiteSpace($playerKey)) {
        throw "No public recommendation player key is available for probing."
    }
    $paths["recommendation-player"] = "/api/recommendations?playerKey=$([uri]::EscapeDataString($playerKey))"
}

for ($warmup = 1; $warmup -le $WarmupSamples; $warmup++) {
    foreach ($domain in $Domains) {
        $null = Invoke-ProbeRequest `
            -Domain $domain `
            -Path $paths[$domain] `
            -Phase "warmup" `
            -SampleIndex $warmup `
            -CandidateTelemetryExpected ([bool]$ExpectCanaryTelemetry)
    }
}

$scoredStart = [DateTime]::UtcNow
for ($sample = 1; $sample -le $Samples; $sample++) {
    if ($sample -gt 1 -and $Samples -gt 1 -and $WindowMinutes -gt 0) {
        $targetOffsetSeconds = (($sample - 1) * $WindowMinutes * 60) / ($Samples - 1)
        $remainingMilliseconds = ($scoredStart.AddSeconds($targetOffsetSeconds) - [DateTime]::UtcNow).TotalMilliseconds
        if ($remainingMilliseconds -gt 0) {
            Start-Sleep -Milliseconds ([int][Math]::Ceiling($remainingMilliseconds))
        }
    }
    foreach ($domain in $Domains) {
        $null = Invoke-ProbeRequest `
            -Domain $domain `
            -Path $paths[$domain] `
            -Phase "scored" `
            -SampleIndex $sample `
            -CandidateTelemetryExpected ([bool]$ExpectCanaryTelemetry)
    }
}

function Get-Percentile([object[]]$Records, [string]$Property, [double]$Percent) {
    $values = @($Records | ForEach-Object { [double]($_.$Property) } | Sort-Object)
    if ($values.Count -eq 0) {
        return $null
    }
    $index = [Math]::Max(0, [Math]::Ceiling($Percent * $values.Count) - 1)
    return [Math]::Round($values[$index], 3)
}

$domainResults = [ordered]@{}
$hasProbeFailure = $false
$hasIncompleteP99 = $false
foreach ($domain in $Domains) {
    $scoredRecords = @($allRecords | Where-Object { $_.domain -eq $domain -and $_.phase -eq "scored" })
    $warmupRecords = @($allRecords | Where-Object { $_.domain -eq $domain -and $_.phase -eq "warmup" })
    $successfulRecords = @($scoredRecords | Where-Object { $_.ok })
    $failedRecords = @($scoredRecords | Where-Object { -not $_.ok })
    $warmupFailures = @($warmupRecords | Where-Object { -not $_.ok })
    $p99Scored = -not $SkipP99 -and $successfulRecords.Count -ge 100
    if (-not $SkipP99 -and -not $p99Scored) {
        $hasIncompleteP99 = $true
    }
    if ($failedRecords.Count -gt 0 -or $warmupFailures.Count -gt 0) {
        $hasProbeFailure = $true
    }

    $discoveryEvents = @(
        $allRecords | Where-Object {
            $_.domain -eq $domain -and $_.phase -eq "discovery" -and $_.candidateTelemetryExpected
        }
    ) | Measure-Object -Property expectedCandidateReadEvents -Sum
    $expectedCandidateReadEvents = if ($ExpectCanaryTelemetry) {
        (($Samples + $WarmupSamples) * $telemetryEventsPerSuccessfulRequest[$domain]) +
            [int]$discoveryEvents.Sum
    } else {
        0
    }

    $domainResults[$domain] = [ordered]@{
        scoredAttempts = $scoredRecords.Count
        scoredSuccesses = $successfulRecords.Count
        scoredErrors = $failedRecords.Count
        warmupAttempts = $warmupRecords.Count
        warmupErrors = $warmupFailures.Count
        cacheHits = @($scoredRecords | Where-Object { $_.vercelCache -eq "HIT" }).Count
        p99Scored = $p99Scored
        expectedCandidateReadEvents = $expectedCandidateReadEvents
        telemetryCountGate = if ($ExpectCanaryTelemetry) {
            "pending-server-log-reconciliation"
        } else {
            "not-applicable-baseline"
        }
        endToEndMs = [ordered]@{
            p50 = Get-Percentile $successfulRecords "endToEndMs" 0.50
            p95 = Get-Percentile $successfulRecords "endToEndMs" 0.95
            p99 = if ($p99Scored) { Get-Percentile $successfulRecords "endToEndMs" 0.99 } else { $null }
            max = Get-Percentile $successfulRecords "endToEndMs" 1.00
        }
        ttfbMs = [ordered]@{
            p50 = Get-Percentile $successfulRecords "ttfbMs" 0.50
            p95 = Get-Percentile $successfulRecords "ttfbMs" 0.95
            p99 = if ($p99Scored) { Get-Percentile $successfulRecords "ttfbMs" 0.99 } else { $null }
        }
        downloadMs = [ordered]@{
            p50 = Get-Percentile $successfulRecords "downloadMs" 0.50
            p95 = Get-Percentile $successfulRecords "downloadMs" 0.95
            p99 = if ($p99Scored) { Get-Percentile $successfulRecords "downloadMs" 0.99 } else { $null }
        }
        jsonParseMs = [ordered]@{
            p50 = Get-Percentile $successfulRecords "jsonParseMs" 0.50
            p95 = Get-Percentile $successfulRecords "jsonParseMs" 0.95
            p99 = if ($p99Scored) { Get-Percentile $successfulRecords "jsonParseMs" 0.99 } else { $null }
        }
    }
}

$summary = [ordered]@{
    schemaVersion = 2
    runId = $runId
    baseHost = if ($SuppressBaseHost) { $null } else { $BaseUrl.Host }
    domains = $Domains
    scoredSamplesPerDomain = $Samples
    warmupSamplesPerDomain = $WarmupSamples
    requestedWindowMinutes = $WindowMinutes
    elapsedScoredMinutes = [Math]::Round(([DateTime]::UtcNow - $scoredStart).TotalMinutes, 3)
    compressionRequested = $true
    cacheBypass = [ordered]@{
        requested = $true
        mechanisms = @("unique-query-nonce", "cache-control-no-cache-no-store", "pragma-no-cache")
        gate = "zero-x-vercel-cache-HIT"
    }
    telemetry = [ordered]@{
        expected = [bool]$ExpectCanaryTelemetry
        countGateComplete = -not [bool]$ExpectCanaryTelemetry
        requirement = if ($ExpectCanaryTelemetry) {
            "Reconcile server logs to each domain's expectedCandidateReadEvents; every event must be candidate-served, with zero candidate errors, fallbacks, or mismatches."
        } else {
            "Not applicable to this Vercel-authoritative baseline run."
        }
    }
    rawSamplesPath = $rawSamplesPath
    results = $domainResults
}
$summaryJson = $summary | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText($summaryPath, $summaryJson + [Environment]::NewLine, $utf8WithoutBom)
$summary | ConvertTo-Json -Depth 8 -Compress

if ($hasProbeFailure) {
    exit 2
}
if ($hasIncompleteP99) {
    exit 3
}
