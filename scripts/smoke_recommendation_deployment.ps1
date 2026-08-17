[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Deployment,
    [ValidateSet("staged", "production")]
    [string]$Label = "staged",
    [string]$Scope = "jonathansminigameparty",
    [switch]$AllowRefreshFallback,
    [switch]$AllowMissingPlayerCache,
    [ValidateRange(10, 600)]
    [int]$RefreshTimeoutSeconds = 120,
    [string]$AnalysisJobId = "",
    [ValidateRange(10, 7200)]
    [int]$AnalysisTimeoutSeconds = 2400,
    [ValidateRange(1, 30)]
    [int]$AnalysisPollSeconds = 1,
    [switch]$RequireSplitContinuationEvidence
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$expectedProjectId = "prj_MY8d8OpbxoiZGfiqtNwAyFiNgyB7"
$expectedOrgId = "team_2QIqV1zAjPyjocQSKdMP3F91"
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$projectLinkPath = Join-Path $repositoryRoot ".vercel\project.json"

if ([string]::IsNullOrWhiteSpace($Deployment)) {
    throw "A deployment ID or immutable deployment URL is required."
}
$normalizedDeployment = $Deployment.Trim().TrimEnd("/")
if ($normalizedDeployment -match "^(?i:https?://)?pumbility-farmer\.vercel\.app$") {
    throw "Use an immutable deployment ID or URL, not the production alias."
}
if ($RequireSplitContinuationEvidence -and [string]::IsNullOrWhiteSpace($AnalysisJobId)) {
    throw "Split continuation evidence requires an analysis job ID to observe."
}
if (-not (Test-Path -LiteralPath $projectLinkPath -PathType Leaf)) {
    throw "The trusted Vercel project link is missing."
}
$projectLink = Get-Content -LiteralPath $projectLinkPath -Raw | ConvertFrom-Json
if ($projectLink.projectId -ne $expectedProjectId -or $projectLink.orgId -ne $expectedOrgId) {
    throw "The linked Vercel project does not match the approved deployment target."
}

$vercelCommand = Get-Command vercel -ErrorAction SilentlyContinue
if ($null -eq $vercelCommand) {
    throw "The Vercel CLI is required."
}
$inspectionOutput = @(
    & $vercelCommand.Source inspect $Deployment `
        --scope $Scope `
        --no-color 2>&1
)
if ($LASTEXITCODE -ne 0) {
    throw "The deployment could not be inspected in the approved Vercel project."
}
$inspectionText = $inspectionOutput -join [Environment]::NewLine
if ($inspectionText -notmatch "(?im)^status\s+.*Ready\s*$") {
    throw "The deployment is not Ready."
}

function Get-PropertyValue([object]$Object, [string]$Name) {
    if ($null -eq $Object) {
        return $null
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Get-CollectionCount([object]$Value) {
    if ($null -eq $Value) {
        return 0
    }
    return @($Value).Count
}

function Invoke-DeploymentRequest(
    [string]$RequestPath,
    [string]$RequestLabel,
    [ValidateSet("GET", "POST")]
    [string]$Method = "GET",
    [switch]$ParseJson
) {
    $bodyPath = [IO.Path]::GetTempFileName()
    try {
        $writeOut = "__SMOKE_STATUS__:%{http_code};__SMOKE_WIRE_BYTES__:%{size_download};__SMOKE_TYPE__:%{content_type}"
        $arguments = @(
            "curl",
            $RequestPath,
            "--deployment", $Deployment,
            "--yes",
            "--",
            "--silent",
            "--show-error",
            "--compressed",
            "--connect-timeout", "15",
            "--max-time", "90",
            "--header", "Accept: application/json",
            "--header", "Cache-Control: no-cache, no-store, max-age=0",
            "--output", $bodyPath,
            "--write-out", $writeOut
        )
        if ($Method -eq "POST") {
            $arguments += @("--request", "POST")
        }

        # Keep CLI output in memory because request paths can contain an opaque player key.
        $cliOutput = @(& $vercelCommand.Source @arguments 2>&1)
        $cliExitCode = $LASTEXITCODE
        if ($cliExitCode -ne 0) {
            throw "The $RequestLabel request failed at the transport layer."
        }
        $metadata = $cliOutput -join [Environment]::NewLine
        $statusMatch = [regex]::Match($metadata, "__SMOKE_STATUS__:(\d{3})")
        $wireBytesMatch = [regex]::Match($metadata, "__SMOKE_WIRE_BYTES__:([0-9.]+)")
        $contentTypeMatch = [regex]::Match($metadata, "__SMOKE_TYPE__:([^\r\n]+)")
        if (-not $statusMatch.Success -or -not $wireBytesMatch.Success) {
            throw "The $RequestLabel request did not return parseable response metadata."
        }

        $bodyBytes = [IO.File]::ReadAllBytes($bodyPath)
        $payload = $null
        if ($ParseJson -and $bodyBytes.Length -gt 0) {
            try {
                $payload = [Text.Encoding]::UTF8.GetString($bodyBytes) | ConvertFrom-Json
            } catch {
                throw "The $RequestLabel response was not valid JSON."
            }
        }

        return [pscustomobject]@{
            Status = [int]$statusMatch.Groups[1].Value
            BodyBytes = [long]$bodyBytes.Length
            WireBytes = [long][Math]::Round(
                [double]::Parse(
                    $wireBytesMatch.Groups[1].Value,
                    [Globalization.CultureInfo]::InvariantCulture
                )
            )
            ContentType = if ($contentTypeMatch.Success) {
                $contentTypeMatch.Groups[1].Value.Trim()
            } else {
                ""
            }
            Payload = $payload
        }
    } finally {
        if (Test-Path -LiteralPath $bodyPath) {
            Remove-Item -LiteralPath $bodyPath -Force
        }
    }
}

function Assert-Status([object]$Response, [int[]]$Allowed, [string]$RequestLabel) {
    if ($Response.Status -notin $Allowed) {
        throw "The $RequestLabel request returned unexpected HTTP status $($Response.Status)."
    }
}

function Assert-ResponseWithinLimit([object]$Response, [string]$RequestLabel) {
    # Stay conservatively below Vercel's documented 4.5 MB request/response body limit.
    if ($Response.BodyBytes -gt 4500000) {
        throw "The $RequestLabel response exceeded Vercel's 4.5 MB response limit."
    }
}

function Get-SplitContinuationForMessage([string]$Message) {
    $continuations = @{
        "Base analysis checkpointed; queued for combined recommendation analysis." = "combined"
        "Combined recommendation inputs checkpointed; queued for model fitting." = "model-prepare"
        "Recommendation model inputs prepared; queued for Singles fitting." = "model-fit-singles"
        "Singles model checkpointed; queued for Doubles fitting." = "model-fit-doubles"
        "Doubles model checkpointed; queued for Overall assembly." = "model-assemble-overall"
        "Overall model assembled; queued for artifact publication." = "model"
        "Recommendation model checkpointed; queued for snapshot persistence." = "snapshot"
    }
    if ($continuations.ContainsKey($Message)) {
        return $continuations[$Message]
    }
    return $null
}

function Wait-ForAnalysisJob([string]$JobId) {
    if ([string]::IsNullOrWhiteSpace($JobId)) {
        return $null
    }
    $requiredContinuations = @(
        "combined",
        "model-prepare",
        "model-fit-singles",
        "model-fit-doubles",
        "model-assemble-overall",
        "model",
        "snapshot"
    )
    $observed = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    $encodedJobId = [uri]::EscapeDataString($JobId.Trim())
    $deadline = [DateTime]::UtcNow.AddSeconds($AnalysisTimeoutSeconds)
    $finalJob = $null

    while ([DateTime]::UtcNow -lt $deadline) {
        $poll = Invoke-DeploymentRequest `
            -RequestPath "/api/analyze?mix=phoenix2&jobId=$encodedJobId" `
            -RequestLabel "analysis job poll" `
            -ParseJson
        Assert-Status $poll @(200) "analysis job poll"
        Assert-ResponseWithinLimit $poll "analysis job poll"
        $job = $poll.Payload
        if ([string](Get-PropertyValue $job "mix") -ne "phoenix2") {
            throw "The observed analysis job did not use the Phoenix 2 contract."
        }
        $progress = Get-PropertyValue $job "progress"
        $message = [string](Get-PropertyValue $progress "message")
        $continuation = Get-SplitContinuationForMessage $message
        if ($null -ne $continuation) {
            $null = $observed.Add($continuation)
        }

        $status = [string](Get-PropertyValue $job "status")
        if ($status -eq "completed") {
            $finalJob = $job
            break
        }
        if ($status -eq "failed") {
            throw "The observed analysis job failed."
        }
        if ($status -notin @("queued", "running")) {
            throw "The observed analysis job returned an unknown status."
        }
        Start-Sleep -Seconds $AnalysisPollSeconds
    }
    if ($null -eq $finalJob) {
        throw "The observed analysis job did not complete before the smoke deadline."
    }
    if ([string](Get-PropertyValue $finalJob "stage") -ne "publishing") {
        throw "The completed analysis job did not finish in the publishing stage."
    }
    $generatedAtUtc = [string](Get-PropertyValue $finalJob "generatedAtUtc")
    if ([string]::IsNullOrWhiteSpace($generatedAtUtc)) {
        throw "The completed analysis job omitted its generation timestamp."
    }
    try {
        $null = [DateTimeOffset]::Parse(
            $generatedAtUtc,
            [Globalization.CultureInfo]::InvariantCulture
        )
    } catch {
        throw "The completed analysis job returned an invalid generation timestamp."
    }

    $orderedObserved = @(
        $requiredContinuations | Where-Object { $observed.Contains($_) }
    )
    $completeEvidence = $orderedObserved.Count -eq $requiredContinuations.Count
    if ($RequireSplitContinuationEvidence -and -not $completeEvidence) {
        throw "The analysis job completed, but transient polling did not observe every split fitting continuation."
    }
    return [pscustomobject]@{
        status = "completed"
        generatedAtUtc = $generatedAtUtc
        fullSync = [bool](Get-PropertyValue $finalJob "fullSync")
        observedContinuations = $orderedObserved
        requiredContinuationCount = $requiredContinuations.Count
        completeEvidence = $completeEvidence
    }
}

function Get-ModePayload([object]$Payload, [string]$Mode) {
    $player = Get-PropertyValue $Payload "player"
    $modes = Get-PropertyValue $player "modes"
    if ($null -eq $modes) {
        throw "The $Mode response omitted player.modes."
    }
    $modeProperties = @($modes.PSObject.Properties)
    if ($modeProperties.Count -ne 1 -or $modeProperties[0].Name -ne $Mode) {
        throw "The $Mode response was not bounded to the requested mode."
    }
    return $modeProperties[0].Value
}

function Get-ModeShape(
    [string]$Mode,
    [object]$Response,
    [object]$ModePayload,
    [string]$Variant = "default"
) {
    $propertyNames = @($ModePayload.PSObject.Properties.Name)
    return [pscustomobject]@{
        mode = $Mode
        variant = $Variant
        status = $Response.Status
        bodyBytes = $Response.BodyBytes
        wireBytes = $Response.WireBytes
        contentType = $Response.ContentType
        stale = [bool](Get-PropertyValue $Response.Payload "stale")
        topRecommendations = Get-CollectionCount (
            Get-PropertyValue $ModePayload "topRecommendations"
        )
        topScores = Get-CollectionCount (Get-PropertyValue $ModePayload "topScores")
        filterCandidates = Get-CollectionCount (
            Get-PropertyValue $ModePayload "filterCandidates"
        )
        difficultyOptions = Get-CollectionCount (
            Get-PropertyValue $ModePayload "difficultyOptions"
        )
        hasFilterCandidates = $propertyNames -contains "filterCandidates"
    }
}

function Assert-OverallDifficultyOptions([object[]]$Options) {
    $previousModeOrder = -1
    $previousLevel = -1
    foreach ($optionValue in $Options) {
        $option = [string]$optionValue
        $match = [regex]::Match($option, "^([SD])(\d+)$")
        if (-not $match.Success) {
            throw "The Overall response included a malformed difficulty option."
        }
        $modeOrder = if ($match.Groups[1].Value -eq "S") { 0 } else { 1 }
        $level = [int]$match.Groups[2].Value
        if ($modeOrder -lt $previousModeOrder -or (
            $modeOrder -eq $previousModeOrder -and $level -le $previousLevel
        )) {
            throw "The Overall difficulty options were not canonical and ascending."
        }
        if ($modeOrder -ne $previousModeOrder) {
            $previousLevel = -1
        }
        $previousModeOrder = $modeOrder
        $previousLevel = $level
    }
}

function Assert-StandardRecommendationWindow(
    [string]$Mode,
    [object]$ModePayload
) {
    $eligible = [bool](Get-PropertyValue $ModePayload "eligible")
    $ratingValue = Get-PropertyValue $ModePayload "scoringRating"
    if (-not $eligible -or $null -eq $ratingValue) {
        return $null
    }
    $baseLevel = [int][Math]::Floor([double]$ratingValue)
    $minimumLevel = [Math]::Max(16, $baseLevel - 2)
    $maximumLevel = $baseLevel + 2
    $expectedType = if ($Mode -eq "singles") { "Single" } else { "Double" }
    foreach ($collectionName in @("topRecommendations", "filterCandidates")) {
        foreach ($chart in @(Get-PropertyValue $ModePayload $collectionName)) {
            $chartType = [string](Get-PropertyValue $chart "type")
            $chartLevel = [int](Get-PropertyValue $chart "level")
            if ($chartType -ne $expectedType -or $chartLevel -lt $minimumLevel -or $chartLevel -gt $maximumLevel) {
                throw "The $Mode response included a recommendation outside its official-level window."
            }
        }
    }
    return [pscustomobject]@{
        minimum = $minimumLevel
        maximum = $maximumLevel
    }
}

function Assert-TierWhatIfContract([object]$TierPayload) {
    $summary = Get-PropertyValue $TierPayload "summary"
    $method = Get-PropertyValue $summary "method"
    $whatIfMethod = Get-PropertyValue $method "whatIfEstimates"
    if ([int](Get-PropertyValue $whatIfMethod "levelRadius") -ne 1) {
        throw "The tier-list method did not advertise a one-level What-if radius."
    }
    if ([int](Get-PropertyValue $whatIfMethod "minimumOfficialLevel") -ne 16) {
        throw "The tier-list method did not preserve the level-16 What-if floor."
    }

    $chartCount = 0
    $estimateCount = 0
    $unavailableCount = 0
    foreach ($mode in @("singles", "doubles")) {
        foreach ($chart in @(Get-PropertyValue $TierPayload $mode)) {
            $chartCount += 1
            $level = [int](Get-PropertyValue $chart "level")
            $expectedLevels = [Collections.Generic.List[int]]::new()
            if ($level - 1 -ge 16) {
                $expectedLevels.Add($level - 1)
            }
            $expectedLevels.Add($level + 1)
            $estimates = @(Get-PropertyValue $chart "whatIfEstimates")
            if ($estimates.Count -ne $expectedLevels.Count) {
                throw "A tier-list chart did not contain the exact adjacent What-if alternatives."
            }
            for ($index = 0; $index -lt $estimates.Count; $index += 1) {
                $estimate = $estimates[$index]
                if ([int](Get-PropertyValue $estimate "level") -ne $expectedLevels[$index]) {
                    throw "A tier-list chart's What-if alternatives were not canonical and adjacent."
                }
                $estimateValue = Get-PropertyValue $estimate "estimatedDifficulty"
                if ($null -eq $estimateValue) {
                    $unavailableCount += 1
                } else {
                    $numericEstimate = [double]$estimateValue
                    if ([double]::IsNaN($numericEstimate) -or [double]::IsInfinity($numericEstimate)) {
                        throw "A tier-list What-if estimate was not finite or null."
                    }
                }
                $estimateCount += 1
            }
        }
    }
    foreach ($chart in @(Get-PropertyValue $TierPayload "coop")) {
        if ((Get-CollectionCount (Get-PropertyValue $chart "whatIfEstimates")) -gt 0) {
            throw "A Co-op tier-list chart unexpectedly contained What-if estimates."
        }
    }
    if ($chartCount -eq 0 -or $estimateCount -eq 0) {
        throw "The tier-list What-if contract was vacuous."
    }
    return [pscustomobject]@{
        charts = $chartCount
        estimates = $estimateCount
        unavailable = $unavailableCount
    }
}

function Test-CurrentAnalysisRecommendation(
    [object]$Response,
    [object]$ObservedAnalysisJob
) {
    if ($null -eq $ObservedAnalysisJob) {
        return $true
    }
    $stale = [bool](Get-PropertyValue $Response.Payload "stale")
    $modelGeneratedAtUtc = [string](
        Get-PropertyValue $Response.Payload "modelGeneratedAtUtc"
    )
    $currentModelGeneratedAtUtc = [string](
        Get-PropertyValue $Response.Payload "currentModelGeneratedAtUtc"
    )
    return (
        -not $stale `
        -and $modelGeneratedAtUtc -eq $ObservedAnalysisJob.generatedAtUtc `
        -and $currentModelGeneratedAtUtc -eq $ObservedAnalysisJob.generatedAtUtc
    )
}

function Get-EncodedPlayerPath([string]$PlayerKey, [string]$Suffix) {
    $encodedPlayerKey = [uri]::EscapeDataString($PlayerKey)
    return "/api/recommendations?playerKey=$encodedPlayerKey&$Suffix"
}

function Wait-ForRefreshedOverall([object]$RefreshResponse) {
    $outcome = [string](Get-PropertyValue $RefreshResponse.Payload "outcome")
    if ($outcome -eq "fresh") {
        return
    }
    if ($outcome -notin @("started", "existing")) {
        throw "The selected-player refresh returned an unknown outcome."
    }
    $job = Get-PropertyValue $RefreshResponse.Payload "job"
    $jobId = [string](Get-PropertyValue $job "id")
    if ([string]::IsNullOrWhiteSpace($jobId)) {
        throw "The selected-player refresh did not return a job identifier."
    }

    $deadline = [DateTime]::UtcNow.AddSeconds($RefreshTimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Seconds 1
        $encodedJobId = [uri]::EscapeDataString($jobId)
        $poll = Invoke-DeploymentRequest `
            -RequestPath "/api/recommendations/refresh?jobId=$encodedJobId" `
            -RequestLabel "selected-player refresh poll" `
            -ParseJson
        Assert-Status $poll @(200) "selected-player refresh poll"
        Assert-ResponseWithinLimit $poll "selected-player refresh poll"
        $status = [string](Get-PropertyValue $poll.Payload "status")
        if ($status -eq "completed") {
            return
        }
        if ($status -eq "failed") {
            throw "The selected-player refresh job failed."
        }
        if ($status -notin @("queued", "running")) {
            throw "The selected-player refresh job returned an unknown status."
        }
    }
    throw "The selected-player refresh did not complete before the smoke deadline."
}

$routeShapes = [Collections.Generic.List[object]]::new()
foreach ($route in @(
    @{ path = "/"; label = "landing page" },
    @{ path = "/recommendations"; label = "recommendations page" },
    @{ path = "/tier-list"; label = "tier-list page" }
)) {
    $response = Invoke-DeploymentRequest `
        -RequestPath $route.path `
        -RequestLabel $route.label
    Assert-Status $response @(200) $route.label
    Assert-ResponseWithinLimit $response $route.label
    $routeShapes.Add([pscustomobject]@{
        route = $route.label
        status = $response.Status
        bodyBytes = $response.BodyBytes
        wireBytes = $response.WireBytes
        contentType = $response.ContentType
    })
}

$analysisJob = Wait-ForAnalysisJob $AnalysisJobId

$tierList = Invoke-DeploymentRequest `
    -RequestPath "/api/tier-list" `
    -RequestLabel "tier-list API" `
    -ParseJson
Assert-Status $tierList @(200) "tier-list API"
Assert-ResponseWithinLimit $tierList "tier-list API"
$tierSchemaVersion = [int](Get-PropertyValue $tierList.Payload "schemaVersion")
if ($tierSchemaVersion -ne 6 -and (
    -not [string]::IsNullOrWhiteSpace($AnalysisJobId) -or $tierSchemaVersion -ne 5
)) {
    throw "The tier-list API did not return a compatible combined-tier contract."
}
$tierMix = Get-PropertyValue $tierList.Payload "mix"
if ([string](Get-PropertyValue $tierMix "key") -ne "combined") {
    throw "The tier-list API did not return the combined mix."
}
$tierModes = @("singles", "doubles", "coop")
foreach ($tierMode in $tierModes) {
    if ($null -eq (Get-PropertyValue $tierList.Payload $tierMode)) {
        throw "The tier-list API omitted an expected mode."
    }
}
$tierWhatIfShape = if ($tierSchemaVersion -eq 6) {
    Assert-TierWhatIfContract $tierList.Payload
} else {
    [pscustomobject]@{
        pendingGeneration = $true
        charts = 0
        estimates = 0
        unavailable = 0
    }
}

$playersResponse = Invoke-DeploymentRequest `
    -RequestPath "/api/recommendations/players" `
    -RequestLabel "recommendation player list" `
    -ParseJson
Assert-Status $playersResponse @(200) "recommendation player list"
Assert-ResponseWithinLimit $playersResponse "recommendation player list"
if ($null -ne $analysisJob) {
    if ([string](Get-PropertyValue $tierList.Payload "generatedAtUtc") -ne $analysisJob.generatedAtUtc) {
        throw "The tier-list generation did not match the completed analysis job."
    }
    if ([string](Get-PropertyValue $playersResponse.Payload "modelGeneratedAtUtc") -ne $analysisJob.generatedAtUtc) {
        throw "The recommendation model generation did not match the completed analysis job."
    }
}
$players = @(Get-PropertyValue $playersResponse.Payload "players")
if ($players.Count -eq 0) {
    throw "The recommendation player list was empty."
}

$selectedPlayerKey = ""
$cachedOverall = $null
$refreshCandidateKey = ""
$cacheProbeCount = 0
$maximumCacheProbes = 20
foreach ($candidate in $players) {
    if ($cacheProbeCount -ge $maximumCacheProbes) {
        break
    }
    $candidateKey = [string](Get-PropertyValue $candidate "playerKey")
    if ([string]::IsNullOrWhiteSpace($candidateKey)) {
        continue
    }
    $cacheProbeCount += 1
    $candidateResponse = Invoke-DeploymentRequest `
        -RequestPath (Get-EncodedPlayerPath $candidateKey "mode=overall") `
        -RequestLabel "cached recommendation probe" `
        -ParseJson
    if ($candidateResponse.Status -eq 200) {
        Assert-ResponseWithinLimit $candidateResponse "cached recommendation probe"
        $probeOverall = Get-ModePayload $candidateResponse.Payload "overall"
        $probeDifficultyOptions = Get-PropertyValue $probeOverall "difficultyOptions"
        if ($null -eq $probeDifficultyOptions -or @($probeDifficultyOptions).Count -eq 0) {
            continue
        }
        $candidateEligibility = Get-PropertyValue $candidate "eligibility"
        $candidateCanRecommend = (
            [bool](Get-PropertyValue $candidateEligibility "singles") -or
            [bool](Get-PropertyValue $candidateEligibility "doubles")
        )
        if (-not $candidateCanRecommend) {
            continue
        }
        if (-not (Test-CurrentAnalysisRecommendation $candidateResponse $analysisJob)) {
            if ($AllowRefreshFallback -and $candidateCanRecommend) {
                $refreshCandidateKey = $candidateKey
                break
            }
            continue
        }
        $selectedPlayerKey = $candidateKey
        $cachedOverall = $candidateResponse
        break
    }
    if ($candidateResponse.Status -ne 404) {
        throw "A cached recommendation probe returned unexpected HTTP status $($candidateResponse.Status)."
    }
}

$refreshFallbackUsed = $false
if ([string]::IsNullOrWhiteSpace($selectedPlayerKey)) {
    if ($AllowMissingPlayerCache -and -not $AllowRefreshFallback) {
        [pscustomobject]@{
            label = $Label
            status = "passed-without-player-cache"
            cacheDiscovery = [pscustomobject]@{
                probes = $cacheProbeCount
                refreshFallbackUsed = $false
            }
            routes = @($routeShapes)
            tierList = [pscustomobject]@{
                status = $tierList.Status
                schemaVersion = $tierSchemaVersion
                bodyBytes = $tierList.BodyBytes
                wireBytes = $tierList.WireBytes
                contentType = $tierList.ContentType
                modes = $tierModes.Count
                whatIf = $tierWhatIfShape
            }
            recommendationPlayers = [pscustomobject]@{
                status = $playersResponse.Status
                bodyBytes = $playersResponse.BodyBytes
                wireBytes = $playersResponse.WireBytes
                contentType = $playersResponse.ContentType
                count = $players.Count
            }
            recommendations = @()
        } | ConvertTo-Json -Depth 6
        return
    }
    if (-not $AllowRefreshFallback) {
        throw "No current cached recommendation player was found. Re-run with -AllowRefreshFallback to permit one selected-player refresh."
    }
    if (-not [string]::IsNullOrWhiteSpace($refreshCandidateKey)) {
        $selectedPlayerKey = $refreshCandidateKey
    } else {
        $eligiblePlayers = @($players | Where-Object {
            $eligibility = Get-PropertyValue $_ "eligibility"
            [bool](Get-PropertyValue $eligibility "singles") -or
                [bool](Get-PropertyValue $eligibility "doubles")
        })
        $dualModePlayers = @($eligiblePlayers | Where-Object {
            $eligibility = Get-PropertyValue $_ "eligibility"
            [bool](Get-PropertyValue $eligibility "singles") -and
                [bool](Get-PropertyValue $eligibility "doubles")
        })
        $candidatePool = if ($dualModePlayers.Count -gt 0) {
            $dualModePlayers
        } else {
            $eligiblePlayers
        }
        $eligiblePlayer = $candidatePool | Sort-Object -Descending -Property @{
            Expression = {
                $progress = Get-PropertyValue $_ "scoreProgress"
                $singlesProgress = Get-PropertyValue $progress "singles"
                $doublesProgress = Get-PropertyValue $progress "doubles"
                [int](Get-PropertyValue $singlesProgress "validScoreCount") +
                    [int](Get-PropertyValue $doublesProgress "validScoreCount")
            }
        } | Select-Object -First 1
        if ($null -eq $eligiblePlayer) {
            throw "No eligible player was available for the refresh fallback."
        }
        $selectedPlayerKey = [string](Get-PropertyValue $eligiblePlayer "playerKey")
    }
    if ([string]::IsNullOrWhiteSpace($selectedPlayerKey)) {
        throw "The eligible refresh candidate had no opaque player key."
    }
    $encodedPlayerKey = [uri]::EscapeDataString($selectedPlayerKey)
    $refreshResponse = Invoke-DeploymentRequest `
        -RequestPath "/api/recommendations/refresh?playerKey=$encodedPlayerKey&mode=overall" `
        -RequestLabel "selected-player refresh fallback" `
        -Method "POST" `
        -ParseJson
    Assert-Status $refreshResponse @(200, 202) "selected-player refresh fallback"
    Assert-ResponseWithinLimit $refreshResponse "selected-player refresh fallback"
    Wait-ForRefreshedOverall $refreshResponse
    $refreshFallbackUsed = $true

    $publicationDeadline = [DateTime]::UtcNow.AddSeconds(15)
    while ([DateTime]::UtcNow -lt $publicationDeadline) {
        $cachedOverall = Invoke-DeploymentRequest `
            -RequestPath (Get-EncodedPlayerPath $selectedPlayerKey "mode=overall") `
            -RequestLabel "refreshed Overall publication" `
            -ParseJson
        if ($cachedOverall.Status -eq 200) {
            break
        }
        if ($cachedOverall.Status -ne 404) {
            throw "The refreshed Overall publication returned unexpected HTTP status $($cachedOverall.Status)."
        }
        Start-Sleep -Seconds 1
    }
    Assert-Status $cachedOverall @(200) "refreshed Overall publication"
}
Assert-ResponseWithinLimit $cachedOverall "cached Overall recommendation"
if (-not (Test-CurrentAnalysisRecommendation $cachedOverall $analysisJob)) {
    throw "The selected recommendation cache did not use the completed analysis model."
}

$modeShapes = [Collections.Generic.List[object]]::new()
$standardModeRanges = @{}
$overallMode = Get-ModePayload $cachedOverall.Payload "overall"
$overallProperties = @($overallMode.PSObject.Properties.Name)
if ($overallProperties -contains "filterCandidates") {
    throw "The default Overall response included its full filter candidate pool."
}
$difficultyOptions = @(Get-PropertyValue $overallMode "difficultyOptions")
if ($difficultyOptions.Count -eq 0) {
    if (@(Get-PropertyValue $overallMode "topRecommendations").Count -gt 0) {
        throw "The default Overall response omitted difficulty options for a nonempty recommendation pool."
    }
} else {
    Assert-OverallDifficultyOptions $difficultyOptions
}
$overallShape = Get-ModeShape "overall" $cachedOverall $overallMode
if ($overallShape.topRecommendations -gt 50) {
    throw "The default Overall response exceeded the Top 50 display limit."
}
$modeShapes.Add($overallShape)

foreach ($mode in @("singles", "doubles", "coop")) {
    $response = Invoke-DeploymentRequest `
        -RequestPath (Get-EncodedPlayerPath $selectedPlayerKey "mode=$mode") `
        -RequestLabel "$mode recommendation" `
        -ParseJson
    Assert-Status $response @(200) "$mode recommendation"
    $modePayload = Get-ModePayload $response.Payload $mode
    if (-not (Test-CurrentAnalysisRecommendation $response $analysisJob)) {
        throw "The $mode recommendation did not use the completed analysis model."
    }
    $modeShape = Get-ModeShape $mode $response $modePayload
    if ($modeShape.topRecommendations -gt 50) {
        throw "The $mode response exceeded the Top 50 display limit."
    }
    Assert-ResponseWithinLimit $response "$mode recommendation"
    if ($mode -in @("singles", "doubles")) {
        $modeRange = Assert-StandardRecommendationWindow $mode $modePayload
        if ($null -ne $modeRange) {
            $standardModeRanges[$mode] = $modeRange
        }
    }
    $modeShapes.Add($modeShape)
}

foreach ($difficultyOption in $difficultyOptions) {
    $option = [string]$difficultyOption
    $modeKey = if ($option.StartsWith("S")) { "singles" } else { "doubles" }
    if (-not $standardModeRanges.ContainsKey($modeKey)) {
        throw "Overall exposed a difficulty for an ineligible source mode."
    }
    $optionLevel = [int]$option.Substring(1)
    $modeRange = $standardModeRanges[$modeKey]
    if ($optionLevel -lt $modeRange.minimum -or $optionLevel -gt $modeRange.maximum) {
        throw "Overall exposed a difficulty outside its source mode's official-level window."
    }
}
foreach ($chart in @(Get-PropertyValue $overallMode "topRecommendations")) {
    $chartType = [string](Get-PropertyValue $chart "type")
    $modeKey = if ($chartType -eq "Single") { "singles" } elseif ($chartType -eq "Double") { "doubles" } else { "" }
    if (-not $standardModeRanges.ContainsKey($modeKey)) {
        throw "Overall included a recommendation without an eligible source mode."
    }
    $chartLevel = [int](Get-PropertyValue $chart "level")
    $modeRange = $standardModeRanges[$modeKey]
    if ($chartLevel -lt $modeRange.minimum -or $chartLevel -gt $modeRange.maximum) {
        throw "Overall included a recommendation outside its source mode's official-level window."
    }
}

$overallSliceCount = 0
$overallSliceCandidateCount = 0
$overallSliceMaximumCandidates = 0
$overallSliceMaximumBodyBytes = 0L
$overallSliceMaximumWireBytes = 0L
foreach ($difficultyOption in $difficultyOptions) {
    $selectedDifficulty = [string]$difficultyOption
    $encodedDifficulty = [uri]::EscapeDataString($selectedDifficulty)
    $overallSlice = Invoke-DeploymentRequest `
        -RequestPath (Get-EncodedPlayerPath $selectedPlayerKey "mode=overall&difficulty=$encodedDifficulty") `
        -RequestLabel "Overall difficulty slice" `
        -ParseJson
    Assert-Status $overallSlice @(200) "Overall difficulty slice"
    Assert-ResponseWithinLimit $overallSlice "Overall difficulty slice"
    $overallSliceMode = Get-ModePayload $overallSlice.Payload "overall"
    if (-not (Test-CurrentAnalysisRecommendation $overallSlice $analysisJob)) {
        throw "An Overall difficulty response did not use the completed analysis model."
    }
    $overallSliceProperties = @($overallSliceMode.PSObject.Properties.Name)
    if ($overallSliceProperties -notcontains "filterCandidates") {
        throw "The Overall difficulty response omitted filterCandidates."
    }
    $sliceDifficultyOptions = @(Get-PropertyValue $overallSliceMode "difficultyOptions")
    if ($sliceDifficultyOptions.Count -ne $difficultyOptions.Count) {
        throw "An Overall difficulty response changed the canonical option list."
    }
    for ($index = 0; $index -lt $difficultyOptions.Count; $index += 1) {
        if ([string]$sliceDifficultyOptions[$index] -ne [string]$difficultyOptions[$index]) {
            throw "An Overall difficulty response reordered the canonical option list."
        }
    }
    $overallFilterCandidates = @(Get-PropertyValue $overallSliceMode "filterCandidates")
    if ($overallFilterCandidates.Count -eq 0) {
        throw "The Overall difficulty response returned an empty canonical slice."
    }
    foreach ($chart in $overallFilterCandidates) {
        $chartType = [string](Get-PropertyValue $chart "type")
        $chartLevel = [int](Get-PropertyValue $chart "level")
        $actualDifficulty = if ($chartType -eq "Single") {
            "S$chartLevel"
        } elseif ($chartType -eq "Double") {
            "D$chartLevel"
        } else {
            throw "The Overall difficulty response included a non-Single/Double chart."
        }
        if ($actualDifficulty -ne $selectedDifficulty) {
            throw "The Overall difficulty response included a chart outside the requested slice."
        }
    }
    $overallSliceCount += 1
    $overallSliceCandidateCount += $overallFilterCandidates.Count
    $overallSliceMaximumCandidates = [Math]::Max(
        $overallSliceMaximumCandidates,
        $overallFilterCandidates.Count
    )
    $overallSliceMaximumBodyBytes = [Math]::Max(
        $overallSliceMaximumBodyBytes,
        $overallSlice.BodyBytes
    )
    $overallSliceMaximumWireBytes = [Math]::Max(
        $overallSliceMaximumWireBytes,
        $overallSlice.WireBytes
    )
}

$invalidDifficulty = Invoke-DeploymentRequest `
    -RequestPath (Get-EncodedPlayerPath $selectedPlayerKey "mode=overall&difficulty=Z999") `
    -RequestLabel "invalid Overall difficulty" `
    -ParseJson
Assert-Status $invalidDifficulty @(400) "invalid Overall difficulty"
Assert-ResponseWithinLimit $invalidDifficulty "invalid Overall difficulty"
$misplacedDifficulty = Invoke-DeploymentRequest `
    -RequestPath (Get-EncodedPlayerPath $selectedPlayerKey "mode=singles&difficulty=S16") `
    -RequestLabel "misplaced recommendation difficulty" `
    -ParseJson
Assert-Status $misplacedDifficulty @(400) "misplaced recommendation difficulty"
Assert-ResponseWithinLimit $misplacedDifficulty "misplaced recommendation difficulty"
$invalidMode = Invoke-DeploymentRequest `
    -RequestPath (Get-EncodedPlayerPath $selectedPlayerKey "mode=invalid") `
    -RequestLabel "invalid recommendation mode" `
    -ParseJson
Assert-Status $invalidMode @(400) "invalid recommendation mode"
Assert-ResponseWithinLimit $invalidMode "invalid recommendation mode"

$summary = [pscustomobject]@{
    label = $Label
    status = "passed"
    cacheDiscovery = [pscustomobject]@{
        probes = $cacheProbeCount
        refreshFallbackUsed = $refreshFallbackUsed
    }
    routes = @($routeShapes)
    analysisJob = if ($null -eq $analysisJob) {
        [pscustomobject]@{
            observed = $false
            evidenceDurable = $false
        }
    } else {
        [pscustomobject]@{
            observed = $true
            evidenceDurable = $false
            status = $analysisJob.status
            fullSync = $analysisJob.fullSync
            observedContinuations = @($analysisJob.observedContinuations)
            requiredContinuationCount = $analysisJob.requiredContinuationCount
            completeEvidence = $analysisJob.completeEvidence
        }
    }
    tierList = [pscustomobject]@{
        status = $tierList.Status
        bodyBytes = $tierList.BodyBytes
        wireBytes = $tierList.WireBytes
        contentType = $tierList.ContentType
        modes = $tierModes.Count
        whatIf = $tierWhatIfShape
    }
    recommendationPlayers = [pscustomobject]@{
        status = $playersResponse.Status
        bodyBytes = $playersResponse.BodyBytes
        wireBytes = $playersResponse.WireBytes
        contentType = $playersResponse.ContentType
        count = $players.Count
    }
    recommendations = @($modeShapes)
    overallDifficultySlices = [pscustomobject]@{
        count = $overallSliceCount
        candidates = $overallSliceCandidateCount
        maximumCandidates = $overallSliceMaximumCandidates
        maximumBodyBytes = $overallSliceMaximumBodyBytes
        maximumWireBytes = $overallSliceMaximumWireBytes
        invalidDifficultyStatus = $invalidDifficulty.Status
        misplacedDifficultyStatus = $misplacedDifficulty.Status
        invalidModeStatus = $invalidMode.Status
    }
}

$summary | ConvertTo-Json -Depth 6
