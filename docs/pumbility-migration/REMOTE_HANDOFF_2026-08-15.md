# Pumbility Supabase rollout — continuation handoff

Updated: 2026-08-15 JST / 2026-08-15 UTC

This document is the current sanitized continuation record for a future Codex conversation. It
supersedes `REMOTE_HANDOFF_2026-08-14.md` for live-state decisions while retaining that file as
historical evidence. It contains no passwords, tokens, database locations, private deployment
references, player identifiers, artifact identifiers, or private digests.

## 2026-08-15 live rollout completion

This section supersedes the older live-state, blocker, and pending-phase descriptions retained later
in this file as historical context. Supabase authority is live on the public Production alias from
release commit `3e0b52c`. The prior READY Production deployment remains privately retained for
rollback; its identity is intentionally not recorded here.

The guarded cutover completed after every gate retained by the owner-approved compressed runbook
passed. Hosted qualification proved both
queues at 100/100 durable effects with one real redelivery each, 30/30 capacity tasks with a peak of
6 connections against a limit of 12 and zero connection/deadline errors, exact private reads for all
four required Blob targets, isolated Blob mutation safety, Supabase and Blob timeout recovery, and
real worker-process crash recovery with one exactly-once effect. A Vercel Celery
`Reject(requeue=True)` was proven to be acknowledged without requeue; release commit `3e0b52c` uses
process loss for the qualification redelivery path, and the affected hosted gate passed after that
focused repair.

The unaliased IAD Production-target candidate passed startup, API, both-worker/queue, selected-player
refresh, zero-outbox, telemetry, and exact reconciliation gates before atomic promotion. The
owner-approved compressed Production watch ran from `2026-08-15T02:57:14Z` through
`2026-08-15T03:17:14Z`, with scheduled polls at +10 and +20 minutes. Both polls reported the intended
READY alias, healthy analysis/tier-list/player-list/recommendation/job-status responses, a completed
supervised player job, outbox counts `0/0/0`, and zero 5xx, fallback, candidate, or authority errors.
The registered daily cron remained exactly `/api/cron/phoenix2` at `0 6 * * *`.

Post-watch hosted reconciliation passed with a stable source boundary, privacy scan, exact relational
and artifact parity, 582,301 Phoenix 1 exact matches, 15,661 Phoenix 2 exact matches, 173 JSON
artifacts, 1 binary artifact, and 56 cached-player artifacts. Keep Vercel mirror/read fallback and the
private rollback reference available through at least `2026-08-29T02:57:14Z`; run another exact
reconciliation before considering their removal.

## Required startup reading

Before taking any action, read these files completely:

1. `AGENTS.md`
2. `docs/pumbility-migration/REMOTE_HANDOFF_2026-08-15.md`
3. `docs/pumbility-migration/production-rollout.md`
4. `docs/pumbility-migration/evidence/PUM-S10-PRODUCTION-GROUP1-RETEST-01.md`

For schema-owner work in the sibling repository, also read completely:

1. `C:\Users\jfung\Downloads\bite-open-card-draw\AGENTS.md`
2. `C:\Users\jfung\Downloads\bite-open-card-draw\docs\codex-current-brief.md`

Do not read archived sibling plans unless its current brief routes the task there.

## Owner decisions that remain in force

- Do not wait multiple days for rollout evidence.
- One supervised scheduled cycle is sufficient; no multi-day soak is required.
- Run the three read-canary groups as three actively watched 15-minute windows, 45 minutes total.
- The original latency thresholds remain diagnostic and must never be rewritten as passing.
- If latency is the only remaining failure after every non-latency gate passes, the owner permits
  the distinct `owner-latency-waived` result.
- Correctness, exact parity, integrity, privacy, fallback, worker, queue, capacity, failure,
  rollback, and evidence gates are not waivable.
- Production testing is authorized, but that does not permit enabling Supabase authority before
  its preceding gates pass.
- Vercel remains authoritative until the final guarded cutover.
- Preserve all unrelated worktree changes.

## Workspace and repository state

Primary repository:

`C:\Users\jfung\Downloads\piu_misgrade_bundle`

Schema-owner sibling:

`C:\Users\jfung\Downloads\bite-open-card-draw`

Current primary-repository branches and commits at handoff:

| Purpose | Branch | Commit | State |
|---|---|---:|---|
| Current upstream application | `origin/main` | `8881be3` | Contains the merged tier-list what-if selector; not contained in the live rollout deployment |
| Pumbility rollout | `agent/pumbility-rollout-latency-qualification` | `b63a719` | Pushed; draft PR #69 is open and mergeable |
| Live rollout code boundary | rollout history | `119fc11` | Directly deployed to Production; later `b63a719` changes are documentation only |
| Recommendation readiness UI | `feature/recommendation-eligibility-progress` | `05e27ff` | Pushed, clean, one commit ahead of its main boundary, no PR, not deployed |

The current primary shared worktree intentionally retains these unrelated items:

```text
 M next-env.d.ts
?? .codex-remote-attachments/
?? .vercelignore
?? CLAUDE.md
```

Do not stage, edit, delete, move, or commit them. Inspect `git status --short` again before every
commit because the workspace is shared.

The schema-owner sibling has an unrelated modified `.gitignore`. Do not edit or stage it.

## Supabase production migration status

The linked production Supabase project has exact local/remote migration parity through:

`20260813010000_pumbility_schema.sql`

Fresh read-only verification on 2026-08-15 JST:

- `npx supabase migration list --linked` reported every local migration remotely applied through
  `20260813010000`, with no local-only or remote-only migration.
- `npx supabase db lint --linked --level error` returned zero results and no schema errors for
  `extensions`, `public`, or `pumbility`.

The schema migration is therefore applied. The remaining blocker is data-shadow parity and rollout
qualification, not schema publication.

No compressed-canonical projection migration has been created or applied. Its design remains an
optional optimization and must not be treated as a rollout prerequisite.

## Current live Production state

Public alias:

`https://pumbility-farmer.vercel.app`

The alias is READY in `iad1` and points to the promoted Supabase-authority deployment built from code
commit `3e0b52c`.

Active behavioral state:

```text
PUMBILITY_DATA_BACKEND=supabase
PUMBILITY_SHADOW_STRICT=false
PUMBILITY_CANONICAL_SNAPSHOT_WRITE_ENABLED=true
PUMBILITY_BLOB_MIRROR_ENABLED=true
PUMBILITY_BLOB_READ_FALLBACK_ENABLED=true
PUMBILITY_SUPABASE_READ_CANARY=
PLAYER_RECOMMENDATION_REFRESH_ENABLED=true
PUMBILITY_SUPABASE_READ_POOL_ENABLED=true
PUMBILITY_SUPABASE_READ_POOL_MAX_SIZE=2
PUMBILITY_SUPABASE_READ_POOL_MAX_WAITING=2
```

Supabase is authoritative. Canonical writes retain the Vercel mirror, read fallback is available for
the rollback window, selected-player refresh is enabled, and the bounded read pool is active. The
Blob mirror outbox was empty at promotion and at both scheduled watch polls.

The deployed Vercel Cron remains enabled at `/api/cron/phoenix2` with the normal daily schedule:

`0 6 * * *`

Internal pre-canary diagnostic and repair routes are default-off and return 404 in Production.

## Latest cron and parity consequence

An authenticated `vercel crons run` smoke was executed once on 2026-08-14 UTC:

- `/api/cron/phoenix2` returned HTTP 202.
- The analysis queue subscriber returned HTTP 200.
- The public Phoenix 2 generation advanced after the trigger.
- The configured daily cron schedule was not changed.

This proves the deployed route and worker can execute. It is a manual control-plane smoke and does
not satisfy the separate genuine time-scheduled topology gate.

Because the smoke ran on the stricter Vercel-only deployment, Vercel advanced but Supabase did not.
A protected hosted reconciliation immediately afterward failed closed after completing only
`source-boundary`. The Supabase shadow is therefore intentionally treated as stale.

Do not run another read canary, enable canonical shadow writes, or attempt cutover until a guarded
backfill/population restores exact parity and all reconciliation stages pass again.

## Latest production group-1 evidence

The adjacent corrected runs were:

- Vercel-only baseline: `20260814T154959690Z`
- Group-1 canary: `20260814T161042848Z`

Both used gzip, three cache-bypass mechanisms, three unscored warmups, 100 scored requests per
domain, and a 15-minute scored window.

| Domain | Metric | Baseline | Canary | Fixed limit | Result |
|---|---:|---:|---:|---:|---|
| analysis | p95 | 859.616 ms | 1,183.740 ms | 945.578 ms | failed |
| analysis | p99 | 982.211 ms | 1,594.714 ms | 1,178.653 ms | failed |
| tier-list | p95 | 935.442 ms | 1,196.511 ms | 1,028.986 ms | failed |
| tier-list | p99 | 1,273.276 ms | 1,255.753 ms | 1,527.931 ms | passed |

Non-latency evidence:

- 100/100 scored successes per domain.
- 3/3 successful warmups per domain.
- Zero HTTP errors.
- Zero JSON errors.
- Zero cache hits.
- Server logs were queried in bounded time segments and deduplicated by immutable log-record ID.
- Exactly 103 `analysis` and 103 `tier-list` events existed.
- All 206 events were `candidate-served`.
- Zero authority errors, candidate errors, fallbacks, mismatches, or conflicting outcomes.

The fixed latency diagnostic failed. The owner waiver was not yet eligible because the candidate
topology's non-latency qualification is incomplete. The alias was returned to the no-canary
deployment immediately after evidence capture.

Canonical evidence:

`docs/pumbility-migration/evidence/PUM-S10-PRODUCTION-GROUP1-RETEST-01.md`

## Completed implementation and verification

The rollout branch contains:

- Sanitized per-phase canary telemetry.
- One-roundtrip ordinary Supabase artifact/job reads.
- Opt-in bounded Psycopg read pooling.
- Explicit JSON responses for large read routes.
- Token-isolated reusable Vercel Blob clients.
- Lazy Celery imports and dependency-free queue constants.
- Corrected compressed/cache-bypassed production probe tooling.
- Protected-preview comparison tooling.
- Pre-canary reconciliation tooling.
- Fail-closed topology qualification tooling.
- Protected Preview-only hosted reconciliation and numeric repair routes.
- Default-off controls and privacy-safe aggregate evidence.

Prior verification includes the full Python/frontend/typecheck/build suites recorded in the older
handoff, plus the hosted exact reconciliation and current production evidence above. Do not claim
those older passing boundaries as evidence for the now-stale post-cron shadow; rerun the applicable
gates after restoration.

The recommendation readiness feature at `05e27ff` implements:

- A nonblank insufficient-data state.
- Singles and Doubles counts such as `25/30` and `10/30`.
- Accessible per-mode progress bars.
- Exact remaining-chart messages.
- A small warning between the Pumbility panel and recommendation list when only one mode qualifies.
- A preparation state when both thresholds pass but the cache is not ready.
- Responsive mobile layout.

Fresh feature verification passed:

- 124 Python recommendation and analysis-runtime tests.
- 47 frontend tests.
- TypeScript typecheck.
- `git diff --check`.

The feature is not merged or deployed. After its code deploys, regenerate the recommendation model
and player summaries so `scoreProgress` is present. Legacy artifacts otherwise fall back to `0/30`
when no selected-player cache exists.

## Remaining blockers

The numbered list below is superseded by the live completion section above. There is no immediate
rollout blocker. The remaining scheduled obligation is to retain rollback plus mirror/read fallback
for 14 days and run another exact reconciliation before considering fallback removal. PR cleanup or
merging is repository administration, not a live rollout blocker.

1. The deployed commit does not contain current `origin/main` or the recommendation readiness
   feature.
2. Draft PR #69 is not merged.
3. The recommendation feature has no PR and is not merged.
4. Supabase shadow data is stale after the Vercel-only cron smoke.
5. No fresh exact post-restoration reconciliation exists.
6. The candidate topology lacks qualifying hosted evidence for workers, both queues, private Blob,
   genuine scheduled cron correlation, cold starts, connection capacity, injected failures, and
   rollback.
7. The owner latency waiver is not eligible until item 6 passes completely.
8. Read-canary groups 1–3 must be rerun after topology qualification. The latest group-1 run is
   valuable correctness evidence but occurred before the required topology gate.
9. Supabase authority, Blob mirror/read fallback, and selected-player refresh remain off.
10. The final 45-minute post-cutover watch and 14-day rollback window have not begun.

## Approved execution plan

### Phase 1 — Consolidate every code change

Build one release candidate from current `origin/main` containing:

- Current main, including the tier-list what-if selector.
- The rollout branch through `b63a719`.
- Recommendation readiness commit `05e27ff`.

Use an isolated integration worktree. Preserve the existing branches and history. Resolve conflicts
once in the integration branch, then verify that all intended commits are present.

Required checks:

- Full Python suite.
- Frontend tests.
- TypeScript typecheck.
- Next production build.
- Python compilation.
- Phoenix 1 archive verification.
- Migration/schema tests.
- Privacy scans.
- API semantic and raw-contract tests where applicable.
- Recommendation readiness states.
- Tier-list what-if regression tests.
- `git diff --check`.

Open one final release PR. Do not merge a red or incomplete build.

Exit: one reviewed green commit containing every intended code change.

### Phase 2 — Deploy the combined code in the safe state

Deploy an unaliased Production candidate with Vercel authority, all Supabase read/write controls
off, selected-player refresh frozen, and the accepted bounded read pool on.

Verify:

- READY in IAD1.
- Exact runtime flags.
- Analysis and tier-list HTTP 200.
- Recommendation player-list HTTP 200.
- Landing, tier, recommendation, and operator UI routes.
- Unauthenticated cron HTTP 401.
- Internal diagnostics HTTP 404.
- No Supabase read-canary telemetry.

Retain the current live deployment as rollback. Promote only after the candidate passes.

Exit: every accumulated code change is live while Vercel remains the only authority.

### Phase 3 — Restore exact Supabase shadow parity

The production database credential is intentionally unavailable to the local operator shell. Add a
narrow default-off hosted operator action that runs the existing guarded backfill/population path
without exposing credentials.

Required controls:

- Preview-only execution.
- Separate diagnostic and mutation enable flags.
- Approved production project, dedicated role, session pooler, and schema.
- Exact Vercel-authoritative safe flags.
- Advisory lock.
- Stable source boundary.
- Idempotent and restartable writes.
- Aggregate-only sanitized output.
- Production route remains inaccessible.
- Bounded execution or a reviewed queued operator job if request duration may exceed the serverless
  limit.

Run the guarded relational/artifact backfill, typed population, and only any specifically proven
artifact repair. Then run the complete ordered reconciliation:

1. Source boundary.
2. Relational parity for Phoenix 1 and Phoenix 2.
3. Model JSON.
4. Publication pointers.
5. Numeric model.
6. Cached-player artifacts.
7. Privacy.
8. Stable final boundary.

Exit: zero unexplained relational differences, exact artifact parity, privacy pass, and stable
boundary.

### Phase 4 — Regenerate final analysis and recommendation artifacts

Only after Phase 3 passes, deploy fail-open shadow writes:

```text
PUMBILITY_DATA_BACKEND=shadow
PUMBILITY_SHADOW_STRICT=false
PUMBILITY_CANONICAL_SNAPSHOT_WRITE_ENABLED=true
PUMBILITY_BLOB_MIRROR_ENABLED=false
PUMBILITY_BLOB_READ_FALLBACK_ENABLED=false
PUMBILITY_SUPABASE_READ_CANARY=
PLAYER_RECOMMENDATION_REFRESH_ENABLED=false
```

Vercel remains authoritative.

Run one supervised Phoenix 2 refresh/full sync. Require worker completion, atomic publication, and
the new `scoreProgress` fields in regenerated recommendation summaries. Reconcile both stores
again.

Browser-test these recommendation states:

- Neither Singles nor Doubles qualifies.
- Singles only.
- Doubles only.
- Both modes qualify.
- Both thresholds pass but the selected-player cache is still preparing.
- Mobile and accessibility behavior.

Exit: final artifacts represent the combined release and match exactly across both stores.

### Phase 5 — Complete candidate-topology qualification

Use IAD1 with the bounded read pool as the intended fast-path topology. Do not move Production to
CLE during this rollout.

Add the missing reviewed, default-off in-topology diagnostic execution wiring. Produce verifier-
compatible sanitized evidence for both the controlled comparison and adopted candidate:

- Exact topology/configuration manifest with only the declared variable different.
- Anonymous private-Blob denial.
- At least 100 authenticated exact reads per required artifact.
- Isolated JSON/binary write-read-delete.
- Failed-publication atomicity.
- Analysis worker execution.
- Player-recommendation worker execution.
- At least 100 unique publish/consume/durable-effect identities for both queues.
- Exactly-once durable effects and at least one redelivery per queue.
- Thirty successful cold starts each for API, analysis worker, and recommendation worker.
- Thirty connection-capacity samples.
- Required concurrency with database usage at or below 75% of the configured limit.
- Zero connection and deadline errors.
- Every documented failure-injection scenario.
- Complete rollback within 300 seconds, safe flags restored, exact reconciliation, and no data loss.
- Privacy-safe output throughout.

For the genuine scheduler gate, add privacy-safe scheduler/route correlation. Deploy a one-shot
date-specific UTC schedule several minutes ahead, require exactly one scheduled GET/202, and do not
use `vercel crons run`. Immediately restore `0 6 * * *` through a second deployment and verify the
registered schedule and READY host. Require worker completion and post-run reconciliation.

Run `verify_pumbility_topology_qualification.py`. Stop on any non-latency failure.

The owner approved one narrowly bounded transport retry on 2026-08-15 for the protected API
comparison. An individual request may be retried once only when the first attempt receives no HTTP
response. Retain the original and retry as separate sanitized attempt records, and still require
exactly 100 successful scored responses per domain and deployment plus all three successful warmups.
Do not retry any HTTP status, application or contract error, cache hit, parity mismatch, candidate or
authority error, fallback, or missing timing boundary. A second no-response result or any ordinary
gate failure fails the comparison; no recursive retry is permitted. The detailed evidence and
redaction requirements remain canonical in `production-rollout.md`.

Exit: all candidate-topology non-latency gates pass.

### Phase 6 — Apply the latency decision

The fixed thresholds remain:

- p95 no more than 10% above control.
- p99 no more than 20% above control.

If they pass, record `passed`. If and only if latency is the sole remaining failure after Phase 5,
rerun the offline verifier with the explicit owner waiver and record the distinct
`owner-latency-waived` state. Never modify the threshold or relabel its diagnostic result.

Use current phase telemetry to make one explicit decision on the compressed-canonical projection.
Implement its additive migration only if the measured candidate fetch/integrity path remains a
dominant bottleneck and the isolated prototype meets the documented improvement, correctness,
memory, publication-time, and fallback thresholds. Otherwise record a deliberate no-go/defer
decision and continue the rollout.

Exit: the adopted topology has either passed latency or has an eligible latency-only waiver.

### Phase 7 — Run the three production canary groups

Use paired, same-commit control and canary Production-target deployments during each 15-minute
window so the three groups remain 45 minutes total while control and candidate observe the same
time boundary.

Before every group:

1. Clear the live read-canary allowlist.
2. Run fresh exact reconciliation, privacy, stable-boundary, and fixed regression checks.
3. Confirm Vercel authority.
4. Deploy only that group's allowlist.

Group 1:

`analysis,tier-list`

- Three warmups plus 100 scored requests per domain.
- Exactly 103 `candidate-served` events per domain.

Group 2:

`recommendation-players,recommendation-player`

- Exactly 104 recommendation-player-list events, including discovery.
- Exactly 206 selected-player events because each selected-player request reads two artifacts.

Group 3:

`job-status`

- Use a current supervised job less than 24 hours old.
- Exactly 103 job-status events.

All groups require zero HTTP failures, cache hits, candidate/authority errors, mismatches, integrity
failures, fallbacks, privacy failures, missing events, duplicate events, or conflicting outcomes.

Any non-latency failure immediately restores the retained no-canary deployment.

Exit: all three correctness and telemetry gates pass; latency is either passed or formally waived.

### Phase 8 — Perform the guarded Supabase-authority cutover

Run one final exact reconciliation, then deploy the complete interlocked configuration:

```text
PUMBILITY_DATA_BACKEND=supabase
PUMBILITY_CANONICAL_SNAPSHOT_WRITE_ENABLED=true
PUMBILITY_BLOB_MIRROR_ENABLED=true
PUMBILITY_BLOB_READ_FALLBACK_ENABLED=true
PUMBILITY_SUPABASE_READ_CANARY=
```

Enable selected-player refresh only after its worker and queue evidence passes.

Deploy unaliased, verify startup interlocks and health, then promote atomically. Confirm:

- Supabase is authoritative.
- Mutations synchronously mirror to Vercel.
- The reference-only Blob outbox is empty or drains idempotently.
- Analysis, tier-list, recommendations, job status, refresh, cron, and rollback paths work.

Exit: Supabase authority is live with Vercel mirror and fallback retained.

### Phase 9 — Complete the active watch and closeout

Completed under the owner-approved compression: 20 minutes with exactly two scheduled polls, 10
minutes apart, followed by a passing final exact reconciliation. The older 45-minute instructions
below are retained only as historical plan text.

Actively monitor for 45 minutes:

- Endpoint status and payload correctness.
- Database connections and timeouts.
- Worker and queue health.
- Cron and job state.
- Blob mirror failures.
- Outbox backlog.
- Fallback use.
- Privacy events.
- Recommendation progress accuracy.
- Tier-list what-if behavior.

Then run final exact reconciliation, verify the daily cron remains `0 6 * * *`, verify the alias is
on the intended merged commit, update the evidence/checklists/handoff, and close or merge the
remaining PRs.

Keep the Vercel mirror and read fallback available for 14 days. At the end of that window, perform
another exact reconciliation before considering any fallback removal.

## Rollback and stopping rules

At every pre-cutover phase, the safe state is:

```text
PUMBILITY_DATA_BACKEND=vercel
PUMBILITY_SHADOW_STRICT=false
PUMBILITY_CANONICAL_SNAPSHOT_WRITE_ENABLED=false
PUMBILITY_BLOB_MIRROR_ENABLED=false
PUMBILITY_BLOB_READ_FALLBACK_ENABLED=false
PUMBILITY_SUPABASE_READ_CANARY=
PLAYER_RECOMMENDATION_REFRESH_ENABLED=false
```

Before each deployment, retain and privately verify the exact previous safe deployment reference.
Never put that reference in committed evidence.

Rollback immediately for any:

- HTTP or response-contract regression.
- Relational or artifact mismatch.
- Checksum, integrity, or schema failure.
- Candidate/authority error or fallback during a canary.
- Missing, duplicate, or conflicting telemetry.
- Privacy failure.
- Worker, queue, cron, capacity, publication, or rollback failure.
- Unexpected flag, deployment identity, region, or schedule.

Do not rollback solely for an isolated latency miss after all non-latency gates pass; record the
diagnostic failure and use the explicit owner waiver if eligible.

## Expected fast-path duration

If CI, backfill, workers, queues, and hosted qualification pass on the first attempt, plan for
approximately 6–10 active hours, including the three 15-minute canary windows and the 45-minute
post-cutover watch. No multi-day testing period is planned. Full-sync, CI, and queue duration remain
the largest variables.

## Safe read-only orientation commands

Run these before changing anything:

```powershell
git status --short
git branch --show-current
git fetch origin --prune
git rev-parse --short origin/main
git rev-parse --short origin/agent/pumbility-rollout-latency-qualification
git rev-parse --short origin/feature/recommendation-eligibility-progress
gh pr view 69 --json state,isDraft,headRefName,baseRefName,mergeStateStatus,url
vercel inspect https://pumbility-farmer.vercel.app --scope jonathansminigameparty --no-color
vercel crons list --format json --scope jonathansminigameparty
```

Schema-owner verification:

```powershell
Set-Location C:\Users\jfung\Downloads\bite-open-card-draw
npx supabase migration list --linked
npx supabase db lint --linked --level error
```

Do not print environment-variable values, retrieve sensitive values through management APIs, place
secrets on command lines, or persist private deployment references in tracked files.

## Copy-ready prompt for the next conversation

> Read `AGENTS.md` and
> `docs/pumbility-migration/REMOTE_HANDOFF_2026-08-15.md` completely. Continue the Pumbility
> Supabase rollout from the documented live safe state and execute the approved implementation
> plan one phase at a time. Preserve unrelated worktree changes. Keep Vercel authoritative until
> every required reconciliation, shadow, regression, topology, canary, privacy, and rollback gate
> has evidence. Latency alone may use the documented owner waiver only after every non-latency gate
> passes. Do not ask for routine approvals; use the existing authenticated CLI access and stop only
> for a genuine missing authority, secret, or external blocker.
