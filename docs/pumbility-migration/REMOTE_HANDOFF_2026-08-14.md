# Pumbility Supabase overhaul — paused handoff

Updated: 2026-08-14 (Asia/Tokyo)

This is a sanitized continuation record for a new Codex Remote chat. It contains
no database passwords, API keys, raw player identifiers, private digests, or
private artifact paths.

## Current live handoff (supersedes the historical pause sections below)

Continuation update, 2026-08-14 UTC / 2026-08-15 JST: rollout commit `119fc11` was deployed with
Vercel authority and the read pool enabled. A new adjacent production group-1 retest produced
103/103 unique `candidate-served` events per domain with zero non-latency failures, but analysis
p95/p99 and tier-list p95 still failed the fixed latency diagnostic. The alias was returned to the
READY no-canary deployment. An authenticated immediate Vercel Cron smoke then returned 202, its
queue subscriber returned 200, and the public Phoenix 2 generation advanced. Because that smoke ran
in the stricter Vercel-only state, a subsequent hosted reconciliation correctly stopped after
`source-boundary`; the Supabase shadow is now stale and must be restored through the guarded
backfill/population path before another canary. This immediate-run smoke is not the missing genuine
time-scheduled topology evidence. See `evidence/PUM-S10-PRODUCTION-GROUP1-RETEST-01.md`.

- Rollout code is at `1ca5399`, `Instrument and optimize Pumbility read canaries`.
- The rollout candidate is pushed on `agent/pumbility-rollout-latency-qualification` through
  `9fc7c64` and is tracked by draft PR #69. Runtime optimization begins at `38857ac`; the later
  commits add the protected-Preview operator harness and its Windows entrypoint fixes. Production
  remains on the live commit and deployment recorded below.
- Candidate `38857ac` adds an opt-in, default-off bounded Psycopg read pool for hot artifact/job
  reads only; direct large-JSON responses; token-isolated Vercel Blob client reuse; lazy Celery
  imports; fixed sanitized pool telemetry/deadlines; and fail-closed topology qualification tools.
  Writes, worker snapshot reconstruction, statistical algorithms, API data, and rollout flags are
  unchanged.
- The candidate passed 303 Python tests, 45 frontend tests, TypeScript typecheck, dependency-lock
  and frozen-sync checks, Python compilation, Phoenix 1 archive verification, the Next production
  build, PowerShell parsing, and `git diff --check` on 2026-08-14 JST.
- The original p95 +10% / p99 +20% targets remain reported honestly. The owner now permits a
  distinct latency-only waiver after deep optimization if and only if every correctness, exact
  parity, integrity, privacy, capacity, fallback, failure, rollback, and evidence gate passes. A
  latency miss must never be relabeled as a pass or waive a non-latency failure.
- The sensitive database variable is now scoped to Preview through Vercel without exposing its
  value. All qualification deployments are isolated Preview targets with no production alias.
- The same-source IAD pool-off/pool-on comparison completed two independent 3-warmup plus
  100-scored-sample runs per domain. Both runs had exact response parity, 400/400 scored HTTP
  successes, gzip throughout, and zero cache hits. In the accepted repeat, pool-on improved
  analysis p95/p99 from `927.397/1114.606 ms` to `858.336/1072.641 ms` and tier-list from
  `1010.346/1216.471 ms` to `977.592/1139.716 ms`. Both deployments reconciled exactly at 206/206
  `candidate-served` events (103 analysis and 103 tier-list) with zero other outcomes or conflicting
  duplicates. The bounded read pool is therefore the accepted connection candidate, still default
  off and not enabled in production.
- The accepted pool-on build was then compared between independently response-attested `iad1` and
  `cle1` execution regions, again with 3 warmups plus 100 scored samples per domain. Exact response
  parity, 400/400 scored successes, gzip, and zero cache hits held. CLE improved analysis p95/p99
  from `989.563/1153.432 ms` to `788.082/884.863 ms` and tier-list from
  `911.686/1134.544 ms` to `882.896/1037.418 ms`. Each region reconciled exactly at 206/206
  `candidate-served` events with zero other outcomes. CLE is a measured latency candidate, not an
  adopted topology.
- A same-IAD Vercel-only versus pool-on-canary smoke returned semantically identical JSON for both
  domains but different raw response bytes because the candidate-served PostgreSQL object has a
  different JSON representation/order. The raw-byte diagnostic remains failed and was not weakened;
  no full baseline/canary qualification was claimed from that smoke.
- A read-only design audit found that compressed canonical artifact bytes could plausibly save
  another 100--250 ms for analysis and 70--180 ms for tier-list, but it remains conditional. Do not
  implement its additive schema/publication migration unless the lower-risk preview still shows
  candidate fetch plus integrity as a material residual bottleneck; it is not a prerequisite for an
  otherwise evidence-complete owner latency waiver.
- Production deployment `dpl_GMs4LwAMcvZKu76t7FPLDx45MFZp` is READY in `iad1` and aliased to the real
  `https://pumbility-farmer.vercel.app` site.
- Vercel remains authoritative for every read and publication. Production is `shadow`, strict
  shadowing is false, canonical Supabase shadow writes are enabled, Blob mirror/read fallback are
  disabled, and `PUMBILITY_SUPABASE_READ_CANARY` is absent.
- Selected-player recommendation refresh remains intentionally frozen with
  `PLAYER_RECOMMENDATION_REFRESH_ENABLED=false` to keep the accepted artifact boundary stable.
- A genuine production cron job, an immediate supervised full sync, canonical typed shadow
  generation, exact reconciliation, privacy, regression, capacity, and rollback gates have passed.
- Latest exact reconciliation: Phoenix 1 `582301`, Phoenix 2 `15238`, zero unexplained differences,
  173 JSON artifacts, one binary artifact, 56 cached-player artifacts, privacy scan passed.
- The dedicated runtime credential was rotated after a stale credential was detected. The new
  credential was installed in Vercel only after the exact reconciliation above passed.
- The optimized commit passed 281 Python tests, 45 frontend tests, TypeScript typecheck, the Next
  production build, and a fresh remote pre-canary gate. That gate again proved exact relational and
  artifact parity, a stable boundary, privacy, Phoenix 1 `582301`, Phoenix 2 `15238`, 173 JSON
  artifacts, one binary artifact, and 56 cached-player artifacts.
- A protected, same-commit region experiment sent 100 compressed scored analysis requests to each
  of `iad1` and `cle1`; every decoded response was identical and all 200 sampled server events were
  `candidate-served`. `cle1` reduced client p50/p95/p99 TTFB from
  `931.612/998.508/1036.324 ms` to `801.525/903.262/930.232 ms`. Median Supabase connect/fetch fell
  from `86.663/229.780 ms` to `21.203/106.051 ms`, but median authoritative Blob read rose from
  `153.219 ms` to `283.816 ms`. Do not move production to `cle1` until its worker, private Blob,
  cron, queue, cold-start, connection-capacity, failure, and rollback topology gates pass.
- The corrected adjacent Vercel-only baseline completed 100/100 scored requests per domain with
  zero errors or cache hits: analysis p95/p99 was `1189.065/1271.094 ms`; tier-list was
  `977.721/1104.796 ms`.
- The optimized production group-1 canary then completed 103/103 exact `candidate-served` events per
  domain, including warmups, with zero HTTP errors, cache hits, mismatches, authority errors,
  candidate errors, or fallbacks. It still failed the fixed latency gate: analysis p95/p99 was
  `1599.077/1752.292 ms` (+34.5%/+37.9%), and tier-list p95/p99 was
  `1254.527/1323.056 ms` (+28.3%/+19.8%).
- The real alias was immediately rolled back to the optimized Vercel-only deployment above. Warm
  post-rollback checks returned HTTP 200 for analysis, tier-list, and recommendation-player-list;
  canary telemetry is absent. Groups 2/3 and Supabase authority were not attempted.

The current operational blocker is topology qualification, not read-path parity. The tooling is
fail-closed and intentionally has no hosted diagnostic route/task, so worker execution, private Blob
behavior and mutation atomicity, genuine control-plane cron delivery, both queues with redelivery,
30 cold starts per component, connection capacity, injected failures, and rollback evidence do not
yet exist for the candidate topology. Do not manufacture those records from an operator laptop or
move production to CLE. Do not re-enable a production read canary until the relevant topology has
complete non-latency evidence. The original latency targets remain reported; if latency is the only
remaining miss after that work, the owner may use the distinct latency-only waiver. The shortest
remaining live test path after qualification is the three 15-minute grouped canaries followed by the
owner-approved 45-minute active post-cutover watch.

## Resume instruction

Open this exact workspace in Codex Remote:

`C:\Users\jfung\Downloads\piu_misgrade_bundle`

Then tell the new chat:

> Read `AGENTS.md` and
> `docs/pumbility-migration/REMOTE_HANDOFF_2026-08-14.md` completely. Continue
> the paused Pumbility Supabase rollout from the documented safe state. Preserve
> unrelated worktree changes, keep Vercel authoritative, and do not enable a
> Supabase read or canonical-write flag until every preceding reconciliation,
> shadow, and regression gate has evidence.

The schema-owner repository is the sibling checkout:

`C:\Users\jfung\Downloads\bite-open-card-draw`

## Historical safety state at first pause

- There is no active production operator command or active subagent.
- Production remains Vercel-backed and user-facing behavior has not been cut
  over to Supabase.
- Supabase canonical snapshot writes remain disabled.
- Supabase strict shadow mode remains disabled.
- The hosted backfill is additive and restartable. Its latest relational and
  artifact copy completed, but the final artifact reconciliation is not yet
  approved because a JSONB checksum-normalization defect was found.
- Do not enable any Supabase read path until that defect is fixed and the exact
  reconciliation passes.

## Completed work

### Planning and baseline

- The repository's current ingestion, analysis, recommendation, publication,
  caching, privacy, local/demo, and failure behavior was exhaustively mapped.
- Existing behavior is frozen as the compatibility contract. No intended
  product behavior change is authorized.
- A production T0 boundary was captured in a secured ignored bundle. Its
  consistency and privacy gates passed. Do not print its private location,
  identifiers, or HMAC values.
- The acceptance checklist and evidence index are under
  `docs/pumbility-migration/`.

### Schema-owner repository

- Repository: `Jonathan-Fung-Gaming/bite-open-card-draw`.
- Branch/HEAD at pause: `main` / `44fbc1f`.
- PR #139, `Add Pumbility shared schema`, is merged.
- Hosted migration `20260813010000` is applied to the approved
  `bite-open-card-draw` Supabase project.
- The private, unexposed `pumbility` schema has 38 forced-RLS application
  tables. Tables intentionally have no `PUMBILITY_` prefix.
- All 41 linked local/remote migrations match.
- Linked Pumbility database lint passes with no schema errors.
- The private `pumbility-artifacts` bucket exists.
- The Pumbility-only PR CI route runs only Supabase database checks; mixed PRs
  retain both database and normal application checks.
- A pre-migration logical recovery bundle and Storage inventory were created on
  the spacious secondary drive and restoration was proven locally.
- The schema repo currently has an uncommitted `.gitignore` modification. Its
  ownership was not established in this pause turn; preserve it unless proven
  part of this task.

### Consumer repository

- Repository: `pumbility-farming` in this workspace.
- Branch/HEAD at pause: `main` / `d3f54d3`.
- PR #56, `Add Supabase persistence overhaul`, merged as `26241e8`.
- PR #57, `Add secure production backfill operator`, merged as `366650b`.
- A focused Windows command-shim repair is on main as `d3f54d3`.
- The direct PostgreSQL runtime uses a dedicated least-privilege login through
  the transaction pooler. The one-off operator uses the session pooler. Never
  deploy the project owner/Postgres credential to Vercel.
- Continuous Supabase job lease heartbeats were implemented and verified in the
  current consumer work.
- Before the latest focused production fixes, the full consumer verification
  passed: 211 Python tests, 45 frontend tests, TypeScript typecheck, archive
  verification, dependency lock check, and Next production build.

### Hosted backfill evidence

- Approved Supabase project reference is encoded in the guarded operator; do not
  substitute another project.
- A narrow runtime login exists with no superuser or bypass-RLS privilege and a
  connection limit of 12.
- Current hosted import boundary:
  - Phoenix 1: 814 players, 4,571 charts, 576,916 current scores.
  - Phoenix 2: 838 players, 4,616 charts, 9,165 current scores.
- Those counts exactly match the two hosted sync-run manifests.
- Reference rows imported: 4 aliases, 231 rerates, 2 score overrides, and 2,583
  video rows.
- The copied compatibility artifact inventory was 174 JSON artifacts, one NPZ
  binary model, and 48 cached-player artifacts at the stable copy boundary.
- Hosted Storage has one private object, about 10.57 MiB, with one matching
  typed reference and zero missing referenced objects.
- Hosted migration parity, schema lint, aggregate public-schema baseline counts,
  and safe Pumbility row counts passed after the backfill.
- The existing local Phoenix 2 snapshot is older than the hosted production
  boundary (7 fewer players and 1,167 fewer scores). Do not compare hosted data
  to that local snapshot as production parity evidence. Use the stable live
  Vercel boundary/pinned T0 evidence.

## Historical blocker (resolved before the current live handoff)

The guarded production operator passes these stages:

1. stable Vercel source boundary;
2. Phoenix 1 bounded/restartable import;
3. Phoenix 2 import;
4. reference import;
5. compatibility artifact copy;
6. unchanged production boundary; and
7. exact relational reconciliation for both mixes.

It fails before completing the first immutable recommendation JSON comparison.
The failure occurs inside `PumbilityArtifactStore.get_json()` checksum
validation, before the verifier can compare source and target values.

Concrete cause: `put_json()` and `put_json_bundle()` calculate `sha256` and byte
size from the incoming Python JSON value, then PostgreSQL `jsonb` can normalize
numeric representation. `get_json()` calculates the checksum from the
database-returned normalized Python value. At least one real production model
artifact therefore fails its own checksum even though the JSON was inserted.

This is not a relational-data failure and has not affected users because Vercel
is still authoritative.

## Historical next implementation step (completed)

Apply one focused integrity repair in `pumbility_store.py`:

1. In `PumbilityArtifactStore.put_json()`, insert/upsert the JSON in one
   transaction with a `RETURNING payload_json`, canonicalize the returned
   database-normalized value, and update `sha256`/`byte_size` before commit.
2. Do the equivalent for every entry in `put_json_bundle()` while preserving
   bundle atomicity.
3. Add a regression test that uses a JSON numeric value whose PostgreSQL JSONB
   round trip normalizes its representation, or otherwise directly proves the
   stored digest is calculated from the returned normalized value.
4. Run the focused store, production-backfill, provisioning, reconciliation,
   and Supabase-tool tests plus `py_compile` and `git diff --check`.
5. Re-run the guarded production operator. The full operator is safe and
   idempotent, though an artifact-only guarded mode may be added if it has the
   same project, credential, lock, stable-boundary, and flags-off checks.
6. Require the production reconciliation to print all completed stages and
   report zero unexplained relational mismatches plus exact artifact parity.
7. Only after that evidence, create a focused PR containing the intended dirty
   files, run CI, and merge.

The operator is invoked only through the linked Vercel production environment;
never put connection strings or keys on the command line or in a file. Existing
scripts enforce this pattern.

## Remaining rollout after the blocker

1. Merge and deploy the focused checksum/backfill fixes with the backend still
   explicitly set to `vercel` and canonical writes disabled.
2. Run the typed hosted analysis/model population path and prove exact or
   approved-tolerance parity with the frozen behavior contract.
3. Run the owner-approved single genuine scheduled shadow cycle, then an immediate supervised full
   sync, against changing production data, recording evidence for ingestion, analysis, recommendations, jobs, leases,
   privacy, cache behavior, and publication atomicity.
4. Execute the exhaustive API/browser/local regression checklist. Existing
   routes, payloads, timeouts, caching, filters, refresh behavior, privacy, and
   statistical output semantics must remain unchanged.
5. Canary reads only after shadow acceptance, one domain for 15 minutes and at least 30 probes;
   keep automatic per-read fallback and immediate Vercel rollback.
6. Enable Supabase authoritative reads/writes only after every required
   checklist item has evidence.
7. Actively monitor for two hours after cutover and keep the old Blob path available for the
   14-day stabilization/rollback window.
8. Set up and document the final local test environment for the owner, including
   the correct application URL. The FastAPI root at `http://localhost:3001/`
   returning `{"detail":"Not Found"}` is expected because no `/` route exists;
   the Next application is served on port 3000.

## Intended dirty files at pause

These are task-related and must be preserved:

- `scripts/backfill_pumbility_production.py`
- `scripts/backfill_pumbility_supabase.py`
- `scripts/provision_pumbility_production.py`
- `scripts/reconcile_pumbility_production.py` (new)
- `scripts/reconcile_pumbility_supabase.py`
- `scripts/refresh_pumbility_supabase_player.py`
- `tests/test_backfill_pumbility_production.py`
- `tests/test_provision_pumbility_production.py`
- `tests/test_pumbility_supabase_tools.py`
- `tests/test_reconcile_pumbility_production.py` (new)
- this handoff file

Known unrelated or ownership-unknown worktree items must not be staged or
modified without first establishing ownership:

- `next-env.d.ts`
- `.codex-remote-attachments/`
- `.vercelignore`
- `CLAUDE.md`

Before committing, inspect `git status --short` again because the worktree is
shared and may have advanced.

## Verification most recently passed

- Runtime typed-persistence prerequisite (`PUM-S9-RUNTIME-TYPED-PERSISTENCE-01`):
  97 focused and 247 full Python tests, 45 frontend tests, Python compilation,
  TypeScript typecheck, and production build.
- 20 focused production backfill/provision/reconcile/Supabase tool tests.
- Python compilation for the focused production scripts.
- `git diff --check`.
- Hosted linked migration parity and Pumbility schema lint.
- Hosted aggregate row/count/storage verification.

The runtime typed-persistence path remains dormant while canonical writes are off.
No scheduled-cycle acceptance, canonical-write activation, final cutover, typed
model publication, or final local-owner signoff has been claimed.
