# Preserve captured inputs and restart with the optimized producer

## Status and boundary

Original planning scope, October 26, 2026. Three subagents examined input preservation,
numerical reuse, and the old worker's cancellation/publication lifecycle.
No production request, cancellation, restart, deployment, credential discovery,
checkpoint edit, or numerical-policy change was performed for this plan.
The subsequent user request authorized implementation. The implementation now
includes immutable bundles, reviewed numerical reuse, bounded adoption,
authenticated admission/cancellation/drain, consent-safe import and atomic
publication guards. See `captured-run-recovery-runbook.md`. Recurring monitoring
remains paused. No production cancellation or replacement refresh has been run.

The proposed source job is `analysis-20261026T01`, pinned to PR151 commit
`3c639a9a75ea1d9903286633114b69e1c8beece4`, deployment
`dpl_FNBk2YNaP3ujxcJupNgyZL8nN11x`. A deployment cannot change that job's queued
continuations. The optimized producer must run as a separate job and generation.

The last saved observation was October 25 at **11:29:04 p.m. Eastern Time**
(`2026-10-26T03:29:04Z`): running, two of eight fits saved, no reported error.
This is historical evidence, not a fresh status check. It recorded completed
official capture with 1,389 chart states per mix, Phoenix 2 source epoch
`2026-10-25T17:28:38.3841283+00:00`, and manifest SHA-256
`28b4255abdd2bf43cbf38299cf543cf9ab1d54cc4ff79fbde8ca7732263601ec`.
Chart-state counts are not HTTP-request counts or proof every chart has scores.

## Intended result

Preserve the exact official evidence and submitted scores already collected by
the source job. If a switch remains worthwhile, adopt those inputs into one new
optimized job without contacting either leaderboard or submitted-score upstream.
Reuse independently compatible numerical components and partial bootstraps.
Preserve retained General/SSS+, Clearing, Pumbility, recommendations and Radar.

The new producer removes retired SSS work and unused diagnostics. It retains
SSS+'s 200 bootstrap samples, seeds, regularization selection and sensitivity,
support/convergence rules, General/Clearing uncertainty, and publication checks.
An SSS fit is never substituted for an SSS+ fit.

Normal scheduled/incremental refresh behavior stays unchanged outside an explicit
temporary handoff. Phoenix 1 remains archived. No schedule, queue concurrency,
authentication policy, or automatic monitor is added or changed.

## Existing support and missing functionality

| Capability | Current state |
| --- | --- |
| Official capture integrity | Manifest, capture bytes, catalog and scope already have strict validation. The job's public capture marker alone does not contain all required references. |
| Private submitted snapshot | The source job owns a hashed snapshot with a job-specific path and, where supported, a database revision receipt. It may be newer than the published snapshot. |
| Main fits and partial bootstraps | Content addressed and independent of job ID. Exact input/kernel/runtime identities already govern reuse. |
| Four-cohort, product-only producer | Implemented locally with scoped parity tests; integration and release verification remain incomplete. It is not deployed. |
| Semantic source/preparation keys | Helpers and focused tests exist locally; they are not yet connected to the complete hosted production path. |
| Exact saved-run recovery | Missing. A distinct validated recovery-input contract and bounded operator handoff are required. |
| Cancellation | Existing `/api/analyze/cancel` requires a valid `CRON_SECRET`. Existing Jonathan-password refresh controls cannot cancel a job. Previous stale credentials must not be retried or rediscovered. |

Do not use ordinary `mode=reanalyze` as a shortcut: it still passes through the
official capture gate and then reads the current published snapshot rather than
necessarily this job's private synchronized snapshot. Tier-only publication also
uses published inputs and does not rebuild the global recommendation model.

## 1. Qualify the optimized release before cancelling anything

Finish the already approved computation-minimization implementation and its
finite acceptance set. Do not cancel the source job while a replacement producer
is unfinished.

Specific outstanding integration gates include:

- Connect the validated source identity and preparation lookup to the hosted
  path. General's identity must retain actual official observations; only the
  check/fetch envelope can be excluded from a numerical key.
- Integrate retained-producer runtime progress. The current runtime allowlist
  includes legacy `FINISH_STAGES` but rejects the new `official-clearing` stage.
  Make stage, preparation and progress validation producer aware, with legacy
  continuation compatibility tests.
- Complete schema-61 browser/detail validation tests, typecheck/build and
  affected runtime/publication integration tests, alongside existing exact
  retained-output parity tests.
- Verify D26+ Clearing and General Pumbility/ranks remain intact. Their work was
  bundled into a legacy SSS stage and must survive removal of that branch.
- Preserve main target-cache compatibility and truthful omitted-diagnostic
  metadata. Keep historical builders and artifacts readable.

These are release prerequisites, not permission to restart general optimization
or weaken numerical checks. Perform at most one general integration review after
the scoped implementation. One proven regression permits one focused repair and
affected verification; do not restart review.

## 2. Prepare an immutable recovery input bundle

Add an operator-only preflight/prepare operation under the existing protected
operator authentication. The request names the source job and expected identity;
it must not accept arbitrary storage paths, secrets or unvalidated payloads.
Preparation does no fitting, upstream fetching, cancellation or pointer change.

Resolve, validate and copy these dependencies into a content-addressed private
recovery namespace:

1. The source job's submitted Phoenix 2 snapshot, canonical hash and original
   artifact receipt/revision; also the submitted Phoenix 1 archive actually used.
2. The exact official manifest, immutable Phoenix 1/Phoenix 2 capture bodies and
   catalog references, matching the source job's recorded manifest hash.
3. Actual catalog, source exclusions/policy, BPM metadata, relevant optional
   official/Clearing inputs, settings and producer recipes used by that run.
4. References to compatible prepared inputs, full/main fit objects, private point
   context and partial bootstrap objects, preserving all original identities.
5. A sanitized record of the current tier/global-model publication for rollback.

The bundle manifest records source job/deployment, content hashes, original source
epochs/fetch times, copy-verification time, policy/recipe identities and its own
version/hash. Validate every transitive reference. Read revision receipts before
and after preparation; if an input changed during the read, reject/retry this
bounded preparation rather than assemble mixed inputs. Do not freeze mutable
progress counters as though they were numerical inputs.

Store verified copies outside ordinary abandoned-checkpoint cleanup, and retain
the original dependencies until recovery verification is complete. Existing
cleanup can delete other jobs' private manifests/shards after 24 hours. A bundle
containing only old paths or the aggregate capture marker is insufficient.
Permanent Phoenix 1 evidence keeps its existing archive retention contract.

If any exact input cannot be recovered and verified, stop. Do not silently use
the latest manifest, an older published snapshot or newly fetched data.
Expose only hashes, counts, epochs, compatibility results and safe error classes;
keep player data and private object paths out of public status/logs.

## 3. Inventory reusable work and decide whether switching still helps

The preview inventories the four retained SSS+ cohorts by source and mode:
completed outer/main fit, private point context, verified bootstrap count,
component identity and expected remaining work. It does not run an optimizer.

| Artifact | Adoption rule |
| --- | --- |
| Completed 995000 SSS+ outer fit | Require matching boards/source decomposition, target, samples, seed and producer recipe. Normalize only optional diagnostics into the separate product-only path. |
| Main target component | Require identical ordered binary observations, chart/player membership, target, 200 samples, seed, regularization grid, kernel and numerical runtime. |
| Partial bootstrap | Require the same complete identity, validated parent, completed count, accumulated effects and next RNG state. Preserve stored CV, point and sensitivity work. |
| Private point context | Retain and validate it for later refinement. Public chart effects alone may otherwise require a point-fit replay. |
| 990000 SSS fit | Retain as historical work; it cannot supply a 995000 SSS+ fit. |
| Old pipeline or typed runtime state | Never relabel or patch into the new producer. Create fresh generation-specific preparation and runtime envelopes. |

A saved bootstrap count of 200 does not alone prove a finalized fit: result
construction/validation can remain. Likewise a numerical cache hit is not a
completed publication. Missing/corrupt optional caches may recompute under the
approved recipe; missing/corrupt pinned source inputs fail closed.

At execution time, perform a fresh read-only status/preflight. If the old job has
completed and verified publication, do not restart it just to adopt the new
producer. If it is already publishing or the conservative switch cost is not
lower, prefer allowing it to finish. Decide using current retained-cache coverage
and measured stage costs, not the historical two-of-eight counter.

Compare remaining old work with: release readiness + cancellation/drain + input
adoption/preparation + unmatched retained fitting + assembly/publication/checks.
Four completely fresh retired SSS cohorts account for 800 bootstrap refits and
32 preliminary solves; only still-unfinished retired work is avoidable now.
Already completed work is a sunk cost. Do not promise a wall-time saving or ETA
from cohort counts alone.

## 4. Add a distinct saved-input recovery contract

Use a new explicit recovery operation and immutable bundle ID. Do not overload
ordinary reanalysis or modify a historical checkpoint's job ID, pipeline code,
generation identity, schemas or predecessor hashes.

The authenticated request supplies source job, bundle hash, expected active head,
expected serving revisions and an idempotency key. Repeated requests return the
same recovery intent/job; they must not create duplicate fits or bypass cooldown
and ownership rules silently.

The replacement writes new job-owned snapshot/preparation envelopes through
existing validators, retaining separate provenance linking the original source
job and exact content. Existing typed snapshot validators enforce old path/job
identity, so merely copying a reference into a new checkpoint is invalid.

In recovery mode, skip both official capture/probes and player synchronization.
Use a test client that raises on every upstream method to prove this. Mark the
job as adopting captured evidence, never as having refreshed upstream sources.
Original epochs/fetch times remain unchanged; computation, storage verification
and publication times remain separately truthful.

Bind General, SSS+, Clearing, recommendation-mode inputs, Overall assembly and
typed persistence to the same bundle. Adapt current job/revision-bound model
input guards explicitly. Validate current consent/membership and source-serving
revisions before publication. If they conflict, stop with the exact reason; do
not overwrite live source pointers or restore revoked players to make hashes fit.

## 5. Cancel and drain before releasing numerical work

Deploy and verify the qualified recovery-capable release while the source job
remains on its original deployment. Preparation/preview can happen without
stopping that job. Execute the handoff only after a fresh go/no-go decision.

Provide a narrow, durable handoff reservation under the existing global analysis
admission mechanism. Conditional reservation must match the expected source job
and head. It must prevent scheduled/operator requests from starting another fit
between cancellation and replacement admission. A proposed waiting replacement
may hold admission, but must perform no fitting or publication until drainage is
verified. Do not change schedules or queue concurrency. If another owner wins the
race, return a conflict and stop rather than compete.

Use existing cancellation semantics with an already available authorized channel.
The existing CRON-secret route may be used only with a known valid credential.
If that credential is unavailable, the new one-shot recovery operation must use
the established operator authentication and call the same owner/cancellation
store primitive server-side. This operation needs focused authorization tests;
it is not a credential-discovery or authentication-weakening workaround.
Do not make manual database edits, clear cancellation flags or revive the old job.

Cancellation acceptance is insufficient. Require all of the following:

- The old job is terminal with cancellation recorded, and no live old owner/lease.
- The old callback has ended, supported by sanitized invocation evidence and the
  configured invocation bound. At PR151, platform maximum and lease are 800
  seconds; numerical work/I/O budgets are 600/750 seconds. These are bounds, not
  a claim that cancellation instantly interrupts an optimizer.
- A queued old delivery can only acknowledge terminal state and do no numerical
  work, continuation or publication; existing claim/requeue guards remain intact.
- A final publication-pointer read after drainage resolves any old commit already
  underway. Record its actual outcome and compare expected serving revisions.

The old publication path pulses its lease before publication, but its pointer
transaction does not atomically test cancellation/owner state. A transaction
already entered may finish during drainage. A new deployment cannot retrofit a
fence into that old callback. Do not start the replacement on the assumption that
an HTTP cancellation reply prevents every late commit.

If quiescence or ownership cannot be established, stop and preserve the bundle
and serving generation. Do not treat lease expiry alone as proof of callback
termination. Release/abort a handoff reservation only after safe reconciliation;
do not leave an unexplained admission block or blindly expire one into overlap.

## 6. Run exactly one replacement and verify its result

After the drain gate, atomically admit the one idempotent replacement on the
verified new deployment and bundle. Build a new retained generation. Let normal
validated cache lookup adopt compatible components, including partial bootstrap
state; never copy old progress ordinals as proof of new completed work.

Use existing deadline-aware continuations and bounded stall accounting. Preserve
the bundle and all numerical objects on failure. A changed kernel/runtime or
input invalidates reuse; it does not justify editing identities or reducing work.

Completion requires terminal success and matched tier/global recommendation
model publication from the replacement, followed by:

- Live General/SSS+ list and lazy detail with matching generation; no retired
  SS/SS+/SSS variants.
- Transmission D25 and Pastry mix D25 with captured official observations and
  finite General/SSS+ estimates, or an exact legitimate model-eligibility blocker.
- Correct Clearing/Pumbility and a player recommendation's documented
  stale/current-model contract; global model must match this run.
- Radar and retained source/freshness provenance checks.

No recurring monitor is re-enabled by this plan. During an explicitly authorized
execution, report relevant bounded checks and final verification in Eastern Time.
A genuine scheduled daily cron remains separate operational evidence.

## Focused acceptance tests

1. Export/adopt exact manifest/capture bytes and submitted snapshot content; source
   epoch/fetch times unchanged; new job envelopes and provenance validate.
2. Missing, corrupt, wrong-mix, wrong-revision or concurrently changed pinned
   dependencies fail before cancellation/admission. Cleanup preserves the bundle.
3. All upstream methods raise during recovery; the complete recovery still works
   using saved inputs. Normal future refresh still follows its existing contract.
4. Exact compatible completed/main/partial-bootstrap reuse, RNG continuity and
   retained-output parity. An incompatible target/seed/runtime/input never hits.
5. Old generation/code/predecessor state remains rejected and byte-for-byte
   unchanged. Retained runtime accepts the new stage and its own progress totals.
6. Unauthorized handoff has no mutation; expected-head races, ordinary cron
   admission, retries and duplicate deliveries cannot create overlapping work.
7. Cancel during fit, during continuation handoff and during publication; verify
   drainage/final-pointer reconciliation before replacement numerical work.
8. Replacement interruption resumes its own checkpoints idempotently. Failure
   before atomic publication leaves the complete serving generation available.
9. Changed consent/source revisions block incompatible publication; tier and
   global-model pointers, browser/detail and player freshness checks stay joined.

Use deterministic small fixtures and existing persisted-component replay. Do not
run another complete production bootstrap merely to test the handoff.

## Stop conditions and rollback

Stop for unfinished release qualification, unverifiable input bundle, unavailable
authorized cancellation, unknown old-callback quiescence, another active owner,
source/consent drift, or an unfavorable restart-versus-finish comparison. Report
the precise blocker; do not enter blind restart/review loops.

Before replacement publication, rollback means retain the current complete
serving generation and bundle. If replacement fails, do not clear the old job's
cancellation; any retry needs a new validated intent after cancelling/draining
the failed replacement as applicable. After publication, use only the existing
conditional rollback of the joined tier/model generation, and never overwrite a
newer publication. Preserve historical captures, samples, models and audit data.

The subsequent user request authorized implementation. Executing a handoff still
requires the fresh favorable decision and all stop/go gates above; no production
cancellation or replacement refresh has been performed as part of implementation.

## Local evidence

Implementation verification: 75 recovery/progress checks pass; the affected
runtime/queue/budget batch passes 59 checks with four optional PostgreSQL checks
skipped; 50 numerical/cache/recipe checks pass; all 329 frontend checks,
TypeScript and the production build pass. The single general review found one
canonical-import retry defect. Its focused atomic-receipt repair passes 31
affected checks, including interruption after import and source/consent conflicts.
No second general review was performed.

The previously documented unrelated canonical-snapshot persistence fixture
failure also appeared in a broader store batch and was left unchanged. It is
recorded under Implementation validation in the daily-cron reliability plan.

- `analysis_runtime.py:1198`: new requests opt into official capture.
- `analysis_runtime.py:1247`: abandoned typed checkpoint cleanup.
- `analysis_runtime.py:2021`: job-specific snapshot write/read integrity.
- `analysis_runtime.py:2505`: current runtime progress validation integration gap.
- `analysis_runtime.py:3043`: combined input revision/identity guards.
- `analysis_runtime.py:4331`: pre-publication lease pulse.
- `analysis_runtime.py:4636`: official gate followed by current-snapshot reanalysis.
- `production_tiers.py:165`: outer-fit identity; `:908`: retained cache adoption.
- `target_fit_cache.py:79`: main identity; `:128`: bootstrap checkpoint;
  `:252`: private point-context hydration.
- `pumbility_store.py:1936`: atomic joined generation publication.
- `api/analyze.py:118`: existing protected cancellation route.
- `tier_publication_jobs.py:88`: tier-only published-source pinning.
- `vercel.json:14` and frozen PR151 worker/store code: invocation bound and
  terminal delivery/claim/requeue behavior.
- `../cron-2026-10-26/official-source-refresh-observations.jsonl` and
  `../cron-2026-10-26/official-source-refresh-release.json`: saved run evidence
  (relative to the worktree parent, not new production observations).
