# Deployment reliability implementation plan

Restore reliable tier publication without changing scoring, clearing or Pumbility
math. This plan addresses the issues identified by two subagent audits and a
local timestamp reproduction. The implementation adds durable worker phases,
verified checkpoints, conditional promotion, safe diagnostics and a compact list
with lazy chart evidence. See the [publication runbook](tier-publication-runbook.md)
for operation and release verification.

The separate [Skill Radar implementation plan](skill-radar-implementation-plan.md)
is saved for later.

## Evidence and diagnosis

Observed October 18, 2026, Japan time. Production code is main commit 08ee94d,
deployment dpl_QNgBCEdVrPyHg7bP2dDLqi5KJB8C.

| Finding | Evidence and implication |
| --- | --- |
| Code is deployed; data is stale | The API still returns schema 48, generated October 17 at 15:10 JST. It lacks target-scoring variants, so the current default SSS+ view shows Unrated charts. |
| Two current refresh requests failed | Requests logged at 10:12 and 10:25 JST returned HTTP 500; client traces contain RemoteDisconnected. Their production entries have no exception detail or trace ID. |
| Current cause remains unproven | Older deployments explicitly ran out of memory. Current settings are Fluid compute, performance memory and an 800-second function timeout. Neither current OOM nor timeout is established. |
| Final processing is long and synchronous | The real cached-input pipeline completed locally at schema 57: 404.5 seconds in transformations, about 429 seconds overall. This excludes hosted archive/upload, public validation, database writes, source rereads and promotion. |
| Retries lose final-stage work | Eight target fits checkpoint, but all ten final transformations and publication remain in one request. Until public.json exists, retry repeats the finish pipeline. |
| Timestamp mutation breaks validation | production_tiers.py changes the timestamp after schema-57 calibration hashes its predecessor. A fixture reproduction rejects this with Submitted calibration predecessor recovery differs. This affects the timestamped background path; tier-only calls use None, so it is not the explanation for their failures. |
| Public payload grows substantially | Current production is 11.4 MB expanded JSON; saved candidates are 83.9–87.9 MB. Production uses Python, so the Next local-reader cache does not fix this path. |
| Cache and concurrency guards are incomplete | Generation identity omits postprocessing code. Tier-only requests acquire no generation lease. Preconditions are checked before an atomic bundle write, rather than conditionally inside its transaction. |

The leading hypothesis is resource or duration pressure across the monolithic
finish/publication path. A swallowed application or storage exception remains
possible. Add diagnostics before identifying a single cause or increasing limits.

## Implementation order

### 1. Record failures and preserve honest UI states

Instrument api/jonathan.py, api_service.py and tier_release.py with safe request,
job, generation and stage IDs. Persist stage start before expensive work; record
elapsed time, RSS/peak memory, artifact sizes, exception class and sanitized stack
locations on completion or failure. Separate disconnect, validation, storage and
worker-termination outcomes. Never log credentials or player records.

When a requested scoring variant is absent, show a short unavailable state and
an explicit General action. Preserve the selected goal and its label; never
silently present General scores as SSS+. Keep genuine rated-but-Unrated target
charts distinct from an entirely unavailable analysis generation.

### 2. Fix timestamp ordering and generation identity

Pin the generation creation time before final calibration and hashing. Pass it
through stage construction; remove the post-calibration timestamp rewrite.
Both background refresh and tier-only paths must validate after a reload.

Separate three identities:

- Fit cache: source boards, model code/policy, target, bootstrap and seed.
- Stage checkpoint: predecessor hash, relevant stage code and calibration policy.
- Publication generation: pinned source/catalog revisions, exclusion policy and
  complete pipeline identity, with one persisted creation time.

A calibration or presentation change must not silently reuse stale public output
or unnecessarily invalidate the eight target fits. Reuse compatible General fits
using their own input and code identity.

### 3. Replace the long HTTP request with a durable tier-only job

The authenticated trigger returns 202 and a stable job ID quickly. Repeated
triggers for the same active generation return that job. A small status endpoint
reads persisted status without hydrating snapshots or recomputing cohorts.

Reuse the existing analysis queue, job leases, heartbeats and continuation
patterns, with a dedicated tier-publication job type. Do not enqueue full score
capture or recommendation analysis. A disconnected HTTP client must not own or
cancel worker progress.

Update the operator client to poll job status and monotonic stage sequence. The
current fit-count-only loop and nine-call limit cannot represent post-fit phases.
Persist failure/retry state; retry transient I/O with bounds, not deterministic
validation failures. Require a compatible phase result before continuing.

### 4. Checkpoint validated final stages

Use immutable private checkpoints for:

    pinned inputs -> General/fit reuse -> SSS refinement/centering
    -> SSS+ -> median calibration -> rare/spread -> combined clearing
    -> source-aware width -> submitted calibration -> archive
    -> public validation -> ready -> promotion

Each checkpoint records schema, input and output hashes, code/policy identity,
creation time and validation result. Verify content on resume; reject stale or
corrupt checkpoints. Load only the phase's required prepared inputs instead of
rebuilding all raw snapshots for every continuation.

Measure whole-phase duration and peak memory, including checkpoint I/O. Target
roughly 240 seconds per invocation with substantial headroom below 800 seconds;
split further if the measured deployed phase exceeds that budget. Keep the
existing timeout unchanged until measurements justify another setting.

### 5. Bound validation and serialization costs

Median/spread/submitted validation repeatedly recovers, copies, hashes and
replays predecessors. Carry validation proofs bound to immutable content and
stage identity so a transition need not replay every ancestor. Do not introduce
an untrusted skip-validation flag. Retain full independent replay in a dedicated
validation phase and parity tests.

Profile archive compression, upload, public projection and database operations
separately. production_tiers.py streams hashes, but pumbility_store.py still
materializes canonical JSON bytes, serializes JSONB and hashes returned normalized
JSON again. Repair the measured hotspots, preserving both existing hash conventions
and database numeric normalization. Bound checkpoint and upload memory rather
than assuming streaming at one layer fixes every layer.

Keep complete audit evidence private. Define a compact list DTO containing the
fields needed for ordering, filters, scores and tier display; load detailed chart
methodology on demand if necessary. Inventory current consumers before removing
fields. Set a provisional expanded-list target of 12 MB for the current catalog,
then verify transfer, parsing and rendering against the existing baseline. Do not
assume a 4.5 MB response limit: the current 11.4 MB response already works.

### 6. Make promotion conditional and atomic

Hold a durable generation lease with owner, expiry and heartbeat. Use immutable
content-addressed artifact paths so competing or stale workers cannot overwrite
another attempt's checkpoint.

Prepare and validate candidate data and archive before promotion. In one short
database transaction, verify lease ownership, expected current tier identity,
source revisions and existing recommendation identity; then write the tier pointer
and release audit. A conflict must leave the current generation untouched.
The existing bundle is atomic, but its preceding checks are not compare-and-swap.

Use verified revision/hash metadata for the final checks; avoid rehydrating entire
snapshots inside a publication transaction. If source revision tokens do not yet
exist, add them to the same transactions that update those sources. Do not use
timestamps alone as proof of unchanged data.

### 7. Gate deployment on published data

Treat deployment READY, candidate-ready and publication-verified as separate
states. Preview the compatible UI and candidate together before activation. Keep
the prior generation until the new candidate, archive and preconditions pass.

For this release, require schema 57, correct SSS+ variants, all 529 restored
D16–D19 estimates with folder medians at level +0.5, valid midpoint metadata,
exact Pumbility component means and matching source exclusions. Preserve General,
Singles and official 20+ numeric results according to the approved methodology.
Read back the public API and open actual chart dialogs after promotion. Preserve
recommendation pointers and retain a tested rollback target.

## Verification and bounded rollout

- Reproduce the timestamped background failure, then verify both timestamp paths.
- Interrupt each phase and retry: resume its last valid checkpoint without
  rerunning completed target fits. Reject corrupted, stale-code and changed-input
  artifacts. Verify monotonic progress independent of completed-fit count.
- Exercise concurrent triggers, expired leases, duplicate deliveries and source
  changes immediately before promotion. Inject upload/database failures; prove
  current tiers and recommendations remain unchanged after a failed promotion.
- Compare full-precision ratings, intervals, ranks, evidence and source policy
  against the complete local pipeline using the same pinned inputs and time.
- Measure peak memory and end-to-end phase costs, not just fit time. Verify
  compact and detailed API views preserve displayed values and explanations.
- Perform one instrumented production run using cached inputs: zero score API
  fetches, no target refits when their identities match, no recommendation fits.
  Read back schema 57 and verify scoring, clearing, Pumbility and chart details
  on desktop/mobile before declaring the release complete.

Run the relevant tests, type checking and build. Allow one general review and one
focused repair for proven regressions. Stop after acceptance; if the instrumented
run fails, report its specific stage and error instead of repeating blind refreshes.

Primary files: production_tiers.py, tier_release.py, api/jonathan.py,
api_service.py, pumbility_store.py, analysis_runtime.py, worker/tasks.py,
api/tier_list.py, the operator client, and tier-list capability/detail handling.
