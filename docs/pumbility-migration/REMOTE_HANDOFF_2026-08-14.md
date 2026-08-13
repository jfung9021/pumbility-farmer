# Pumbility Supabase overhaul — paused handoff

Updated: 2026-08-14 (Asia/Tokyo)

This is a sanitized continuation record for a new Codex Remote chat. It contains
no database passwords, API keys, raw player identifiers, private digests, or
private artifact paths.

## Current live handoff (supersedes the historical pause sections below)

- Rollout code is at `4fa8e9a` after PR #67, `Parallelize Pumbility canary reads`.
- Production deployment `dpl_5sSxQezmjvXopWzHwEJJF3m6XC3M` is READY and aliased to the real
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
- Canary group 1 (`analysis,tier-list`) then produced 60/60 exact `candidate-served` comparisons and
  zero HTTP errors, mismatches, candidate errors, or fallbacks. It nevertheless failed the latency
  gate: analysis p95/p99 was `3098.320/5695.727 ms` versus `2531.782/2570.183 ms` baseline, and
  tier-list p95/p99 was `2584.942/14273.838 ms` versus `1967.591/1985.784 ms` baseline.
- Because the permitted increases are 10% at p95 and 20% at p99, the read canary was removed and
  groups 2/3 plus Supabase authority were not attempted. A post-rollback smoke test returned valid
  JSON and HTTP 200 for analysis, tier-list, and recommendation-player-list routes.

The current proven blocker is Supabase candidate-read latency under production canary load, not
correctness or parity. Do not re-enable any read canary until a focused change has direct evidence
that it can meet the existing p95/p99 gate. After such a fix is ready, the shortest remaining live
test path is three 15-minute grouped canaries followed by the owner-approved 45-minute active
post-cutover watch. Allow additional time for deployment and rollback checks.

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
