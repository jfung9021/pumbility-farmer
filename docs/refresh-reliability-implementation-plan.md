# Refresh reliability implementation plan

Restore daily tier and recommendation publication, repair adjacent job recovery
defects, and make the website accurately describe the data it displays. Preserve
the existing scoring, clearing, Pumbility and recommendation calculations.

The October 19 investigation confirmed the reported daily refresh failure and
four additional defects. Two additional job defects were reproduced with actual
application functions and mocked storage; their production incidence is not
established. A stale recommendation response demonstrates a timestamp mismatch
in the current UI code. Publication-change retry failure was reproduced with
HTTP 409 responses and the actual chart-detail loader.

This is an implementation plan. Investigation changed documentation and temporary
evidence only; it did not modify application code, production jobs or data.

## Findings and production impact

| Priority | Finding | Evidence and scope |
| --- | --- | --- |
| P1 | Daily analysis mistakes completed tier fits for stalled work | The October 18 and 19 scheduled jobs both failed after advancing nested target fits. The outer checkpoint audit only recognizes its phase/database cursor, so the third observation of an unchanged outer token aborts the job. Confirmed production incident. |
| P1 | An expired running lease cannot be reclaimed by another worker | The shared job adapter rejects a different owner before checking expiry or invoking the atomic claim function. Global analysis treats that rejection as a successful duplicate no-op; player refresh raises before doing work. Deterministic reproduction; production occurrence unconfirmed. |
| P1 | Player status polling can cancel healthy work after 30 seconds | The status GET marks queued/running jobs failed at 30 seconds and the SQL adapter cancels a different owner's lease. The player worker has no continuous heartbeat, and the recommendation page separately stops waiting after 30 seconds. Deterministic reproduction; a real cancellation incident is unconfirmed. |
| P2 | Cached recommendations are labeled with the wrong model age | Production returned a cache using the October 15 model with `stale: true`, while the header prefers the current October 17 model timestamp. The older model's results therefore receive a newer timestamp. Production API evidence plus a deterministic display mismatch in the checkout. |
| P2 | Retry repeats an obsolete publication generation | Tier chart details and official player profiles correctly reject stale generations with HTTP 409. Their Retry actions request the same old generation again. Production mismatch responses and a loader reproduction confirm the recovery defect; no real user incident is claimed. |

Failure visibility also needs a focused improvement. The current outage is
visible as an old timestamp, but Tier List and Skill Radar do not explain the
failed global publication consistently. This is an operational consequence of
the observed outage, rather than a sixth independent refresh failure.

### Production state observed on October 19

All times below are Japan Standard Time.

| Item | Observed state |
| --- | --- |
| Published tier generation | October 18 at 11:11; publication completed at 11:31 |
| October 18 daily job | Started at 15:00; failed at 15:24 with the false no-progress error |
| October 19 daily job | Started at 15:00; failed at 15:24 with the same error |
| Global analysis and recommendation model | October 17 at 14:50; recommendation index contains 987 players |
| Skill Radar player index | October 17 at 14:50; 250 players |
| Sample cached recommendation model | October 15 at 15:09, although the available global model is October 17 at 14:50 |
| Official comparison build | October 19 at 08:17; independently generated from pinned official evidence |

Skill Radar can generate a fresh response from retained scores while continuing
to use the old tier suggestion catalog. Response creation, personal score sync,
population model publication and tier publication are separate timestamps.

The retrieved 72-hour logs include 18 completed player refresh events. Their
recorded durations range from 3.7 to 37.1 seconds, demonstrating that healthy work
can exceed the current 30-second cutoff. These events do not prove that polling
cancelled a production job.

Earlier operator HTTP 500/504 responses and a tier-only input-pinning HTTP error
preceded the successful October 18 tier publication. They are historical
observations, not additional ongoing incidents. Official comparison's pinned
source age is also not classified as a defect.

## Implementation sequence

### 1 Restore global analysis progress tracking

Primary files: `analysis_runtime.py:2333`, `analysis_runtime.py:2362`,
`analysis_runtime.py:4324`, and the committed progress exposed by
`production_tiers.py:25` and `production_tiers.py:378`.

Persist nested tier progress in the outer typed checkpoint before handing work
to another invocation. Bind that progress to the same generation, pinned inputs
and pipeline identity as the committed tier stage. The audit token must include
the verified nested progress ordinal or equivalent committed stage identity,
alongside the existing outer phase/database cursor.

Use committed artifacts as the authority. A heartbeat, queue delivery ID,
timestamp or repeated continuation request must not reset the no-progress
counter. Recovery must also handle a crash after a fit or finishing stage commits
but before its continuation returns: reconcile the committed progress before
deciding the outer checkpoint has stalled.

Keep the existing bound for genuinely unchanged progress and preserve the
separate database-publication retry budget. Handle older checkpoints explicitly;
do not blindly erase their resume audit or increase the limit. Preserve the
actual resumed stage in job status rather than resetting every invocation to
`discovering`.

Acceptance checks:

- A production-shaped job completes all eight target fits, prepared-input and
  finishing stages across more than three continuations and reaches publication.
- Completed fit/stage artifacts are reused after a crash; no duplicate fitting
  or calibration is needed for already committed work.
- Three unchanged durable progress observations still produce a bounded failure.
- Changed inputs/code/generation do not masquerade as advancement of an old job.
- Existing database cursor, publication retry and lost-heartbeat tests still pass.

This change should be the first independently reviewable patch because it
addresses the active production outage.

### 2 Repair shared lease recovery and player refresh liveness

Primary files: `pumbility_store.py:2459`, `pumbility_store.py:2482`,
`pumbility_store.py:2503`, `analysis_runtime.py:4031`,
`worker/tasks.py:201`, `api/recommendations.py:474`, and
`app/recommendations/page.tsx:997`.

Route acquisition of another owner's running job through the existing atomic
database claim primitive. A live lease must remain protected; an expired lease
must be reclaimable. Distinguish an explicitly verified live duplicate from an
expired owner, a database failure or an unrelated runtime error. Only a verified
live duplicate may be acknowledged without work. Apply the shared contract to
both analysis and player workers.

Make the player refresh status GET observational. It must not fail a queued job
because it waited 30 seconds, or cancel a running job based solely on payload
age. Recover truly abandoned jobs through the lease-aware worker/recovery path.

Start, pulse and stop the existing continuous lease heartbeat around player
refresh work using `try/finally`. Propagate loss of ownership through the expensive
operation to its publication boundary: an old worker must not publish or claim
completion after its lease has been replaced. Reuse the existing conditional
publication controls where available; close any player publication gap revealed
by the ownership test without changing recommendation math.

Align the recommendation and radar polling contracts. A bounded browser wait
should report that work remains pending, preserve the usable cached result and
allow observation to resume. It must not claim the server job failed merely
because the browser stopped waiting.

Acceptance checks:

- Poll queued and running jobs after 31 seconds and confirm zero state writes or
  cancellations from the status endpoint.
- Run a deliberately slow refresh through the old cutoff while polling it;
  observe heartbeat renewal, exactly one publication and completed status.
- Let worker A die with a running job, expire its lease, then have worker B claim
  and continue the same job/checkpoint. Cover global and player workers.
- Reject a duplicate while A's lease remains live without failing or cancelling A.
- Inject a database/claim error and verify it is surfaced or retried, never
  acknowledged as a harmless duplicate.
- Verify a former owner cannot overwrite B's result or cancel B after lease loss.
- Cover the actual SQL claim behavior in an isolated PostgreSQL integration test,
  in addition to the existing mocked adapter tests.

### 3 Correct displayed freshness and publication change recovery

#### Label the model actually used by cached results

Primary files: `app/recommendations/page.tsx:1202`,
`app/recommendations/page.tsx:1222`, and `pumbility_contract.py:203`.

Display the cached result's `modelGeneratedAtUtc` as the model used by that
result. Consume the API's `stale` flag and show a short older-model notice while
refreshing and after refresh failure. If the current available model timestamp
is useful, label it separately; do not substitute it for the cached result's
provenance. Clear the notice only after a compatible refreshed result replaces
the old cache.

Acceptance: a fixture with cache/model A, available model B and `stale: true`
displays A until B's actual result arrives. A failed refresh keeps A's correct
timestamp and the older-model notice.

#### Recover once from an obsolete generation

Primary files: `lib/api-response.ts:16`, `lib/tier-chart-details.ts:14`,
`app/tier-list/page.tsx:501`, and
`app/official-leaderboard-comparison/page.tsx:51`.

Preserve HTTP status in structured API errors. When a detail/profile request
returns 409, reload its parent tier list or official comparison index once,
preserve the selected chart/player if still present, and fetch against the new
generation. Invalidate the old-generation request cache through its existing
generation key. If the selection disappeared or publication changes again,
show a clear recoverable state instead of entering another automatic loop.

Keep transient server-error retry separate from generation replacement. Periodic
catalog polling is unnecessary for this specific fix.

Acceptance: load index A, publish B, observe one 409, reload B and successfully
fetch the selected entity using B. Also test removed entities, another generation
change, transient errors and the loader's generation cache behavior.

#### Expose the failure behind delayed data

Primary files: `app/_components/refresh-meta.tsx`, `app/tier-list/page.tsx:942`,
`app/recommendations/page.tsx:1223`, `app/skill-radar/page.tsx:212`, and a small
read-only backend status route.

Read a bounded summary from durable job/publication state: last successful tier
and model generation, latest global refresh state, and a sanitized failure code
and time. Do not hydrate raw score histories, expose player identifiers or start
work from a status read. Preserve local standalone operation with a bounded
unavailable-status fallback.

Use a consistent daily-refresh delay threshold, reusing the existing 26-hour
recommendation policy unless the product policy changes. A durable failed job
should be visible independently of that age threshold. Display Radar's tier
suggestion age separately from personal score-sync age. Preserve cached data
while explaining the delay.

Emit a sanitized terminal application-failure event for global jobs. An HTTP 200
queue acknowledgment and Celery's task-success log do not establish that the
application job completed. Operational checks should evaluate terminal job
state and last successful publication, not just transport status. Keep bounded
retries; do not force deterministic validation failures into endless redelivery.

Acceptance: an old publication plus a failed latest job shows delayed/failure
information across dependent pages; fresh personal scores do not imply fresh
tier suggestions. A successful new publication clears the failure state.

## Verification and rollout

Investigation verification already performed:

- Seven focused existing analysis/job-store tests passed.
- Twenty-three focused tier-publication job/store tests passed.
- Actual route, adapter and worker functions reproduced the player timeout and
  expired-lease defects with mocked network/database seams.
- The real chart-detail loader repeated a stale-generation request; read-only
  production mismatch requests returned the expected HTTP 409 responses.
- Public and protected read-only status APIs and the available 72-hour runtime
  logs established the production dates and daily failures above.

Direct production PostgreSQL access from this host timed out. The lease findings
therefore establish executable code defects, not a census of affected production
jobs. Keep the isolated database integration check as an implementation gate.

During implementation, run the regression cases above and the directly affected
analysis, job-store, player refresh and tier-publication suites. For frontend
changes, read the installed Next.js documentation first, run affected frontend
tests, typecheck and the production build, then inspect the stale/pending/409
flows once in the browser. Do not install a framework update or change unrelated
uncommitted feature work for this repair.

After the corrected pipeline is deployed, recover one compatible refresh through
the supported job path. Preserve validated committed fits and pinned inputs; if
a failed job is incompatible, create a new generation using compatible cached
artifacts. Do not fake advancement by deleting an audit counter, altering
timestamps or overwriting publication pointers. Retain the current good public
generation until the replacement completes its existing conditional promotion.

Read back the public tier and recommendation generations and exercise one chart
detail, recommendation and Radar flow. The tier-only job alone does not update
the global recommendation model, so recovery must verify the global workflow.
Confirm one genuine daily run at 15:00 JST completes before declaring scheduled
refresh recovery verified. Retain the prior generation for the existing
conditional rollback procedure.

## Completion boundary and evidence

Implementation is complete when the five defects have focused regression
coverage, relevant checks pass, the supported global refresh publishes compatible
outputs, the website labels their provenance correctly, and generation changes
recover without repeating stale requests. Scheduled operation remains explicitly
unverified until the genuine cron check completes. Stop after one general review
and directly relevant verification; repair only a demonstrated regression in the
changed behavior.

Do not broaden this work into model changes, source-capture policy changes,
general UI cleanup, an official comparison recapture or a new scheduling system.
The comparison's independent pinned-source build and the historical operator
failures remain separate observations.

Detailed findings and aggregate evidence are in:

- `tmp/website-refresh-investigation-2026-10-19/analysis-continuations-findings.json`
- `tmp/website-refresh-investigation-2026-10-19/analysis-continuations-reproduction.json`
- `tmp/website-refresh-investigation-2026-10-19/publication-player-findings.md`
- `tmp/website-refresh-investigation-2026-10-19/player-poll-timeout-proof.json`
- `tmp/website-refresh-investigation-2026-10-19/website-freshness-findings.json`
- `tmp/website-refresh-investigation-2026-10-19/cached-recommendation-age-evidence.json`
- `tmp/website-refresh-investigation-2026-10-19/runtime-evidence-summary.json`
- `tmp/website-refresh-investigation-2026-10-19/public-api-health.json`
- `tmp/clearing-refresh-diagnosis-2026-10-19/scheduled-jobs.json`

Raw runtime responses remain private temporary diagnostics. The existing
`docs/tier-publication-runbook.md` governs tier-only operation and rollback;
this plan adds the global continuation and player-job repairs.
