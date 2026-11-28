# Recommendations-only production refresh

Use this mode to apply the current General recommendation method to the already
published production inputs. It reads stored Phoenix 1/Phoenix 2 snapshots and
current tier annotations, builds recommendation models and private input shards,
and attaches the new generation to the existing published analysis run. General
Singles/Doubles use Phoenix 2 submitted results; Phoenix 1 remains an input to
the independent Co-op path.

The current Phoenix 2 snapshot may be newer than the historical analysis run.
The model records that snapshot's actual provenance separately while retaining
the published tier annotations and analysis-run identity.

The operation performs no upstream score synchronization, official capture,
tier fitting, or Scoring/Clearing analysis. Only the recommendation publication
pointer advances. Tier artifacts, source snapshots, the published analysis run,
and the separate Pro Farming generation retain their existing identities.

## Start and follow the job

Deploy the implementation before invoking the new mode. Use the existing
`X-Jonathan-Password` header, with its value supplied by the normal secret
provider. Do not put the password in a URL or committed file.

```powershell
$backendOrigin = "https://<backend>"
$operatorHeaders = @{ "X-Jonathan-Password" = $env:JONATHAN_PASSWORD }
$started = Invoke-RestMethod -Method Post -Headers $operatorHeaders `
  -Uri "$backendOrigin/api/jonathan/refresh?mode=recommendations"
```

The request uses the existing analysis queue and admission lock. Inspect its
`outcome` and job before proceeding: `started` creates the requested job;
`existing` or `busy` can identify work already in progress. Follow only a job
whose `recommendationsOnly` flag is true. Its flags also identify stored-input
reanalysis and disabled official-source refresh.

Poll `GET /api/analyze?mix=phoenix2&jobId=<jobId>` through terminal state, and
read `GET /api/refresh-status` for serving-model and tier timestamps. A response
accepting the request is not publication completion.

Publication validates the pinned source, tier, base-analysis, recommendation
revisions, and current worker ownership. Missing or incompatible published
inputs, a changed dependency, cancellation, or lost ownership prevents promotion.
Retain the previous serving generation when the job fails; inspect its status
before retrying.

## Cancel existing work when required

The same operator password authorizes:

```powershell
Invoke-RestMethod -Method Post -Headers $operatorHeaders `
  -Uri "$backendOrigin/api/jonathan/cancel?jobId=<jobId>"
```

The response reports `cancelled`, or `completed` when that job already finished.
Record the old job's stage before cancellation. For a job still before
`publishing`, confirm its terminal cancelled state (`cancelRequested: true`) and
release of its matching `active:analysis` admission head. Once those checks pass,
start the recommendations-only job through normal admission. The old invocation
must renew its lease immediately before publication; cancellation and head
release make that check fail, and queued callbacks exit without further work.

Cancellation cannot undo a publication transaction already past its final lease
check. If the old job was `publishing`, or its publication state is ambiguous,
confirm that transaction has finished and reconcile serving revisions before
starting replacement work. Do not clear leases, reset cancellation flags, or
delete admission state manually.

## Verify the published result

Record the serving tier timestamp/revision before starting. After the job
completes:

1. Read `/api/refresh-status`: recommendation model publication advances while
   the tier publication timestamp remains unchanged.
2. Read `/api/recommendations/players`: confirm the current method and peer policy
   (`peerWindows: [200]`, `minimumPeers: 20`) and enabled player refresh.
3. Confirm the job completed with `recommendationsOnly: true` and the retained
   published analysis-run identity. Source and tier revisions must still match
   their pre-run values.

The job rebuilds the shared model, input shards, and player index. Existing
per-player recommendation caches are retained: a read can return a stale result
or require regeneration until the ordinary player-refresh path uses the new
model. Read-only verification should not call the player-refresh POST endpoint.

Run the dedicated recommendation-model, operator-route, and publication-guard
checks without starting a tier analysis:

```powershell
python -m unittest tests.test_recommendation_model_refresh `
  tests.test_recommendation_operator_routes `
  tests.test_recommendation_refresh_store
```
