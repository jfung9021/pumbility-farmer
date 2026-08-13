# Pumbility migration acceptance checklist

Every item requires an owner, UTC date, result, evidence reference, and approved variance if applicable. A checked box without evidence is incomplete. Private evidence links must identify a secured artifact without revealing its path or contents.

## Phase 0: ownership and discovery

- [x] `bite-open-card-draw` is recorded as the sole owner of Supabase migrations. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-MERGE-01`; schema PR #139 merged in the owner repository as `44fbc1f`.
- [x] This repository contains no competing production DDL history. Evidence: Codex, 2026-08-13 UTC, repository scope inventory and `PUM-S2-MERGE-01`; the sole Pumbility migration is owned by `bite-open-card-draw`.
- [ ] The live project PostgreSQL version, extensions, compute, disk, WAL, pooler, and connection limits are recorded. Evidence:
- [ ] Existing schemas, exposed schemas, roles, grants, default privileges, RLS, publications, triggers, buckets, and policies are inventoried. Evidence:
- [x] Proposed `pumbility` names have no conflicts. Evidence: Codex, 2026-08-13 UTC, `PUM-S0-HOSTED-01` found no pre-existing Pumbility objects; `PUM-S2-HOSTED-01` then applied the reviewed schema successfully.
- [ ] Shared-project capacity and reserved connections are approved. Evidence:
- [x] Migration and restore authority are documented. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-MERGE-01`; the schema-owner repository contains the guarded rollback/recovery procedure merged with PR #139.
- [ ] Backup/PITR state and accepted RPO/RTO are documented. Evidence:
- [x] `bite-open-card-draw` Pumbility PRs are configured to run only Supabase/database checks. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-CI-01`; PR #139 passed the Pumbility database job while the unrelated application job was skipped.

## Phase 1: baseline contract

- [x] Current stores and repository ownership are documented. Evidence: `current-state.md`
- [x] Snapshot, analysis, recommendation, reference, and job contracts are documented. Evidence: `data-contract.md`
- [x] Production, standalone-local, browser, demo, operator, cron, and deploy behavior is documented. Evidence: `behavior-contract.md`
- [x] Stale README claims are distinguished from executable behavior. Evidence: `behavior-contract.md`
- [x] Baseline manifest schema and fail-closed template exist. Evidence: `baseline-manifest.schema.json`, `baseline-manifest.json`
- [x] Local capture uses public SHA-256 and private HMAC-SHA256 without raw private output. Evidence: focused test result
- [x] Reconciliation requires exact evidence for every changed leaf. Evidence: focused test result
- [x] Import, local capture, and in-memory tests do not construct a production store; only the explicit `production` command lazily constructs `VercelPrivateBlobStore`. Evidence: `scripts/capture_pumbility_migration_baseline.py`, focused tests
- [x] Generation-consistent production reader is implemented and reviewed against stable, moving-pointer, moving-snapshot, missing-artifact, and privacy cases. Evidence: `tests/test_pumbility_migration_baseline.py` (13 focused tests passed 2026-08-13 UTC)
- [x] Authorized generation-consistent production `T0` capture has been run. Evidence: Codex, 2026-08-13 UTC, `PUM-S1-T0-01`; the secured manifest identifies a production-ready capture.
- [x] Production pointers are unchanged before and after the `T0` read. Evidence: Codex, 2026-08-13 UTC, `PUM-S1-T0-01`; the complete active pointer set matched in a single capture attempt.
- [x] Mutable snapshot logical hash matches on two reads. Evidence: Codex, 2026-08-13 UTC, `PUM-S1-T0-01`; the two complete mutable-snapshot reads matched exactly.
- [x] Production `T0` manifest validates and has no placeholders. Evidence: Codex, 2026-08-14 UTC, `PUM-S1-T0-VALIDATE-01`; contract and privacy validation passed with all production gates true.
- [ ] Production private evidence is stored securely and its retention is approved. Evidence:
- [x] Production consent-set HMAC is recorded. Evidence: Codex, 2026-08-13 UTC, `PUM-S1-T0-PRIVATE-01`; secured whole-set evidence exists and its digest value is intentionally omitted here.
- [x] Production catalog hash by mix is recorded. Evidence: Codex, 2026-08-13 UTC, `PUM-S1-T0-01`; both mix-level catalog digests are present in the secured manifest and intentionally omitted here.
- [x] Production best-score key and record HMACs by mix are recorded. Evidence: Codex, 2026-08-13 UTC, `PUM-S1-T0-PRIVATE-01`; both whole-set evidence pairs are present and their values are intentionally omitted here.
- [x] Active analysis, combined tier, recommendation generation, model JSON, and NPZ checksums are recorded. Evidence: Codex, 2026-08-13 UTC, `PUM-S1-T0-01` and `PUM-S1-T0-PRIVATE-01`; 171 active referenced artifacts were captured without publishing their identifiers or digest values.
- [ ] Retained runs/generations and reference-data checksums are inventoried. Evidence:
- [x] Privacy scan passes with no raw IDs, usernames, scores, per-player digests, or credentials. Evidence: Codex, 2026-08-14 UTC, `PUM-S1-T0-VALIDATE-01`; both secured artifacts passed the repository privacy scanner.
- [ ] API golden matrix covers all 14 production routes. Evidence:
- [ ] Standalone-local golden matrix covers all six route handlers. Evidence:
- [ ] Desktop/mobile evidence covers all four browser routes and key states. Evidence:

## Phase 2: Supabase expand-only schema

- [x] All application tables are inside private schema `pumbility`. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-STATIC-03` and `PUM-S2-HOSTED-01`; all 38 reviewed tables are in the private schema.
- [x] No table uses the retired `PUMBILITY_` prefix. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-STATIC-03`; the reviewed table inventory contains no prefixed table.
- [x] The schema is not exposed through the Supabase Data API. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-LOCAL-01` and `PUM-S2-HOSTED-01`; exposure remains limited to the existing public schemas and browser roles have no private-schema usage.
- [x] Migration applies to an empty local Supabase database. Evidence: Codex, 2026-08-13 UTC, pinned Supabase CLI 2.114.0 `db reset --local --no-seed` replayed the full migration history successfully.
- [x] Migration applies to a production-shaped schema. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-HOSTED-01`; the migration applied to the verified existing shared project after PR #139 merged.
- [x] Database lint passes. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-LOCAL-01` and `PUM-S2-HOSTED-01`; local and linked error-level database lint returned no findings after exact migration parity.
- [x] Diff contains only intended `pumbility` and Storage-policy changes. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-CI-01` and `PUM-S2-MERGE-01`; PR #139 contained exactly the nine reviewed schema, database-check, rollback, CI, and evidence files.
- [x] No unrelated `bite-open-card-draw` object changed. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-STATIC-03`; static DDL inventory found no sibling DDL, Auth mutation, Realtime mutation, or default-privilege mutation.
- [x] Primary keys and required foreign keys exist. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-LOCAL-01`; the focused catalog/constraint transaction passed.
- [x] Natural-key uniqueness constraints exist. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-LOCAL-01`; the focused catalog/constraint transaction passed.
- [x] Only one open consent interval is allowed per player/scope. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-LOCAL-01`; the current-row uniqueness check passed.
- [x] Only one open chart revision is allowed per chart/mix. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-LOCAL-01`; the current-row uniqueness check passed.
- [x] Only one open score revision is allowed per mix/player/chart. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-LOCAL-01`; the current-row uniqueness check passed.
- [x] Mode/status/evidence/generation checks exist. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-LOCAL-01`; the focused type and constraint checks passed.
- [x] Score-ranking, chart-analysis, and recommendation-filter indexes exist. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-STATIC-03` and `PUM-S2-LOCAL-01`; static inventory and focused catalog checks passed.
- [ ] Dominant query plans are captured and approved. Evidence:
- [x] No unjustified table partitioning was added. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-STATIC-03`; all 38 application relations are ordinary tables.
- [x] RLS is enabled and forced as planned. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-LOCAL-01`; the all-table RLS catalog check passed.
- [x] `anon` and `authenticated` cannot access private relations. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-LOCAL-01`; schema-usage and relation-privilege denial checks passed.
- [x] Worker/public-read roles have only intended grants. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-LOCAL-01`; exact role, view, table, sequence, and function grant checks passed.
- [x] Job claim/idempotency functions pass database checks. Evidence: Codex, 2026-08-13 UTC, focused SQL test and concurrent global/same-player claim races passed.
- [x] Atomic publication rejects incomplete generations. Evidence: Codex, 2026-08-13 UTC, focused publication SQL checks passed inside a rollback transaction.
- [x] Private `pumbility-artifacts` bucket and policies pass. Evidence: Codex, 2026-08-13 UTC, focused Storage catalog/policy checks passed and schema-3 NPZ publication succeeded locally.
- [x] Pumbility-only logical dump and restore succeeds. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-RESTORE-01`; 38 tables and 794,572 aggregate rows restored into a disposable database with exact source counts.
- [x] Storage backup and restore succeeds independently. Evidence: Codex, 2026-08-13 UTC, `PUM-S2-RESTORE-01`; one representative private binary artifact restored with exact byte identity and was then removed from the rehearsal target.

## Phase 3: adapters and backfill tooling

- [x] Persistence interfaces isolate existing algorithms from storage. Evidence: Codex, 2026-08-13 UTC, `pumbility_store.py`; full Python suite 200/200 passed.
- [x] Existing Vercel adapters remain available for fallback. Evidence: Codex, 2026-08-13 UTC, default `PUMBILITY_DATA_BACKEND=vercel`; adapter/runtime regression tests included in the 200/200 passing Python suite.
- [x] Supabase structured-data adapter passes integration checks. Evidence: Codex, 2026-08-13 UTC, local backfill, typed analysis, artifact/model publication, direct API reads, and exact source reconciliation passed.
- [x] Supabase artifact adapter verifies checksum before use. Evidence: Codex, 2026-08-13 UTC, `tests/test_pumbility_store.py`; full Python suite 200/200 passed.
- [x] Runtime uses transaction pooling with prepared statements disabled. Evidence: Codex, 2026-08-13 UTC, `_connect(..., prepare_threshold=None)` in `pumbility_store.py`; adapter tests passed.
- [x] Migrations/backfills use a direct or session connection. Evidence: Codex, 2026-08-13 UTC, guarded direct-PostgreSQL CLIs and sibling migration runner; Python compile and dry-run passed. Executable DB proof remains gated by Phase 2.
- [x] Expected Supabase migration version is pinned. Evidence: Codex, 2026-08-13 UTC, adapter assertion for `20260813010000`; schema contract and unit tests passed.
- [x] Backfill is idempotent and resumable. Evidence: Codex, 2026-08-13 UTC, two consecutive full Phoenix 1/Phoenix 2 imports completed with identical aggregate counts.
- [x] Hosted backfill has a separate operator-only entry point and does not weaken local loopback
      guards. Evidence: `scripts/backfill_pumbility_production.py`, focused target/boundary tests,
      and `production-rollout.md`.
- [x] Canonical extraction order matches the baseline contract. Evidence: Codex, 2026-08-13 UTC, a fresh current-algorithm Phoenix 1 payload and the Supabase-derived payload had identical SHA-256 after excluding generated timestamps; Phoenix 2 differed from the prior local payload only by generated timestamp.
- [ ] Durable global and selected-player jobs retain current response shapes. Evidence:
- [ ] Global heartbeat continues through sync, analysis, model fitting, and publication. Evidence:
- [ ] Lease expiry cannot create two publishers. Evidence:
- [x] No production read path changed in the adapter release. Evidence: Codex, 2026-08-13 UTC, backend defaults to Vercel and new Supabase/shadow paths require explicit server-only flags; runtime regression suite passed.
- [x] No public payload changed in the adapter release. Evidence: Codex, 2026-08-13 UTC, 200 Python and 45 frontend contract tests passed. Production/API golden evidence remains a later gate.

## Phase 4: backfill and source-data parity

- [x] Frozen Phoenix 1 private evidence is imported with provenance. Evidence: Codex, 2026-08-13 UTC, 814 players, 4,571 charts, and 576,916 score revisions imported from the validated local manifest.
- [x] Frozen Phoenix 1 public archive is a separate immutable imported analysis run. Evidence: Codex, 2026-08-13 UTC, bounded reference import created a distinct archive run and associated public artifact.
- [x] Historical Phoenix 1 methodology is preserved, not silently recomputed. Evidence: Codex, 2026-08-13 UTC, public archive artifact/run retained separately; the current typed shadow analysis is a separate generation.
- [x] Phoenix 2 `T0` snapshot is imported. Evidence: Codex, 2026-08-13 UTC, 831 players, 4,616 charts, and 7,998 score revisions imported from the validated local manifest.
- [ ] Retained analysis and combined-tier generations are imported. Evidence:
- [x] Recommendation indexes, models, binaries, inputs, state, and caches are imported. Evidence: Codex, 2026-08-14 UTC, `PUM-S4-HOSTED-RECONCILIATION-01`; the guarded hosted operator proved exact parity for 173 JSON artifacts, one private NPZ model, and 48 cached-player artifacts while Vercel remained authoritative.
- [x] Rerates, videos, aliases, and score overrides are imported with provenance. Evidence: Codex, 2026-08-13 UTC, 231 rerates, 2,583 video rows, four aliases, and two score overrides imported.
- [x] `T0` player, chart, and score counts match exactly. Evidence: Codex, 2026-08-13 UTC, backfill transaction assertions and privacy-safe reconciliation passed for both mixes.
- [x] `T0` consent, catalog, score-key, and score-record digests match exactly. Evidence: Codex, 2026-08-14 UTC, `PUM-S4-HOSTED-RECONCILIATION-01`; exact keyed reconciliation matched 582,301 Phoenix 1 and 14,619 Phoenix 2 player/chart/score leaves with zero unexplained mismatches.
- [x] Every score references an existing player and chart. Evidence: Codex, 2026-08-13 UTC, migration FKs plus completed 584,914-row current-score import and focused constraint tests.
- [x] Every current chart references a valid revision. Evidence: Codex, 2026-08-13 UTC, full backfill count assertions and focused schema checks passed.
- [x] No duplicate natural keys or foreign-key orphans exist. Evidence: Codex, 2026-08-13 UTC, idempotent second import, natural-key constraints, and focused SQL checks passed.
- [ ] Every post-`T0` difference through `T1` has an accepted typed evidence entry. Evidence:
- [x] Reconciliation reports zero unexplained mismatches and zero unused explanations. Evidence: Codex, 2026-08-14 UTC, `PUM-S4-HOSTED-RECONCILIATION-01`; guarded production reconciliation passed with zero unexplained mismatches and no accepted-change ledger, followed by an unchanged stable boundary and privacy scan.
- [ ] Revoked players are absent from current operational/public state. Evidence:
- [ ] No private fields exist in public views or evidence artifacts. Evidence:

## Phase 5: synchronization parity

- [ ] Exact upstream mix values remain Phoenix and Phoenix2. Evidence:
- [ ] Seven-day incremental overlap remains. Evidence:
- [ ] Six global score workers remain. Evidence:
- [ ] Shared 125 ms request-start limiter remains. Evidence:
- [ ] Shared `Retry-After` behavior remains. Evidence:
- [ ] Opaque paging and repeated-cursor defense remain. Evidence:
- [ ] New-player full fetch remains. Evidence:
- [ ] Empty-player 24-hour recheck remains. Evidence:
- [ ] Consent revocation pruning remains immediate. Evidence:
- [ ] Current-catalog pruning remains. Evidence:
- [ ] Full sync discards the watermark. Evidence:
- [ ] Checkpoint/resume produces identical final state. Evidence:
- [ ] Schema-1 metadata refetch behavior remains. Evidence:
- [ ] Best-score winner order matches exactly. Evidence:
- [ ] Interactive refresh performs a complete Phoenix 2 fetch. Evidence:
- [ ] Missing interactive response rows do not delete stored rows. Evidence:
- [ ] Interactive generation switch keeps all inputs consistent. Evidence:

## Phase 5: analysis parity

- [x] Methodology/source/dependency versions and random seed are recorded. Evidence: Codex, 2026-08-14 UTC, `PUM-S5-TYPED-POPULATION-01`; the hosted typed shadow runs persisted the pinned analyzer script version, source and dependency hashes, production configuration, and random seed after parity passed.
- [ ] Eligible Singles players match. Evidence:
- [ ] Eligible Doubles players match. Evidence:
- [ ] Positive/non-broken filtering matches. Evidence:
- [ ] Ranks 11–30 baselines and counts match. Evidence:
- [ ] Top-Pumbility and recency windows match. Evidence:
- [ ] Deduplicated union and top-100 fallback match. Evidence:
- [ ] Selected contribution rows match. Evidence:
- [ ] Pumbility-per-level calibration matches. Evidence:
- [ ] Shrinkage parameters match. Evidence:
- [ ] Folder medians and range compression match. Evidence:
- [ ] Estimates and all confidence intervals match at serialized precision. Evidence:
- [ ] Contributor counts, evidence bands, relative groups, mode ranks, and level ranks match. Evidence:
- [ ] Phoenix 1 special score conversions and rebands match. Evidence:
- [ ] Phoenix 2 allowlist and overlap precedence match. Evidence:
- [ ] Combined chart membership/order/results match. Evidence:
- [x] Public tier payload semantic and exact-field hashes match. Evidence: Codex, 2026-08-14 UTC, `PUM-S5-TYPED-POPULATION-01`; the production-equivalent Phoenix 2 analysis matched after removing only contract-defined volatile timestamps, and the combined-tier payload matched exactly.

## Phase 5: recommendation parity

- [ ] Recommendation generation membership matches. Evidence:
- [ ] Public player keys, usernames, display names, and duplicate suffixes match. Evidence:
- [ ] Mode eligibility and reasons match. Evidence:
- [ ] Rating-source selection and top-20 window match. Evidence:
- [ ] Projection-source selection and ranks 11–30 window match. Evidence:
- [ ] Phoenix 1 fallback and Phoenix 2 thresholds match. Evidence:
- [ ] Current per-mode top 50 and shared Overall top 50 match. Evidence:
- [ ] Played state and existing Pumbility match. Evidence:
- [ ] Peer cohorts, radii, thresholds 20/10/5, and selected-player exclusion match. Evidence:
- [ ] Population fallback matches. Evidence:
- [ ] Projected score, grade, plate/probability/source/support/confidence match. Evidence:
- [ ] Expected Pumbility and projected gain match. Evidence:
- [ ] Tie ordering matches. Evidence:
- [ ] Complete filter candidate pools match. Evidence:
- [ ] Top-20 order matches for every player/mode. Evidence:
- [ ] Stale-generation behavior matches. Evidence:
- [ ] Failed refresh preserves the previous cache. Evidence:
- [ ] Rollback marks newer player results stale exactly as before. Evidence:
- [ ] All-player parity, not sampling, passes for the owner-approved single scheduled run and one full sync. Evidence:

## Phase 5/6: API and frontend regression

- [ ] Every production route, method, parameter/default, auth rule, status, header, and body shape matches. Evidence:
- [x] Phoenix 1 redirect/archive behavior matches. Evidence: Codex, 2026-08-13 UTC / 2026-08-14 JST, `PUM-S6-PRODUCTION-REGRESSION-01`; live production returned the frozen archive 307 and archived-job 410 contracts.
- [x] Phoenix 2 remains the default. Evidence: Codex, 2026-08-13 UTC / 2026-08-14 JST, `PUM-S6-PRODUCTION-REGRESSION-01`; live default and explicit Phoenix 2 analysis reads returned the expected public aggregate shape.
- [x] Deployment webhook remains a signed no-op. Evidence: Codex, 2026-08-13 UTC / 2026-08-14 JST, `PUM-S6-PRODUCTION-REGRESSION-01`; the Python route suite covers the valid signed no-op and archived rejection, and the live unsigned boundary returned 401 without queuing work.
- [x] Jonathan incremental/full behavior matches. Evidence: Codex, 2026-08-13 UTC / 2026-08-14 JST, `PUM-S6-PRODUCTION-REGRESSION-01`; the Python route suite covers both forced incremental and full-sync coordination, while the live route retained password auth and `no-store`.
- [x] Production player list retains the actual 30-second cache header. Evidence: Codex, 2026-08-13 UTC / 2026-08-14 JST, `PUM-S6-PRODUCTION-REGRESSION-01`; live production returned 200 and the client-visible `public, must-revalidate, max-age=30` policy for 838 players.
- [x] Standalone-local player list retains its five-minute cache header. Evidence: Codex, 2026-08-13 UTC / 2026-08-14 JST, `PUM-S6-PRODUCTION-REGRESSION-01`; the active standalone-local server returned `public, max-age=300, s-maxage=300, stale-while-revalidate=3600`.
- [x] Local manual-rating recommendations remain available. Evidence: Codex, 2026-08-13 UTC / 2026-08-14 JST, `PUM-S6-PRODUCTION-REGRESSION-01`; a rating-20 Overall request returned 200 with the expected local recommendation shape and `no-store, max-age=0`.
- [x] Local refresh remains unavailable with current 404/503 behavior. Evidence: Codex, 2026-08-13 UTC / 2026-08-14 JST, `PUM-S6-PRODUCTION-REGRESSION-01`; standalone-local GET/POST refresh probes returned 404/503 with `no-store`.
- [x] Public responses expose no internal IDs or raw scores. Evidence: Codex, 2026-08-13 UTC / 2026-08-14 JST, `PUM-S6-PRODUCTION-REGRESSION-01`; recursive live production and local shape scans found no `playerId`, user ID, or raw-score fields. UUID-shaped values occurred only in public catalog `chartId` fields.
- [x] Landing, tier, recommendation, and operator pages function unchanged. Evidence: Codex, 2026-08-13 UTC / 2026-08-14 JST, `PUM-S6-PRODUCTION-REGRESSION-01`; direct production browser checks covered all four routes, the consent/privacy copy, external sync link, hidden operator boundary, and operator password form.
- [x] Tier defaults/grouping/layout/filtering/dialog behavior matches. Evidence: Codex, 2026-08-13 UTC / 2026-08-14 JST, `PUM-S6-PRODUCTION-REGRESSION-01`; production defaulted to Singles/estimated/compact, rendered 1,269 Singles and 1,303 Doubles charts, and passed search, official-level, grouping, layout, limited-data, and accessible-dialog interactions.
- [x] Recommendation cached-first, Overall-first, filters, progress, and limited-data behavior match. Evidence: Codex, 2026-08-13 UTC / 2026-08-14 JST, `PUM-S6-PRODUCTION-REGRESSION-01`; a cached production player opened on Overall with 20 ranked cards, progress, projected-gain controls, limited-data labels, difficulty filtering, and working Overall/Single/Double tabs.
- [x] Demo behavior matches. Evidence: Codex, 2026-08-13 UTC / 2026-08-14 JST, `PUM-S6-PRODUCTION-REGRESSION-01`; `/tier-list?demo=1` used the fixed ten-chart demo payload while retaining Singles and the filter controls.
- [x] Mobile and accessibility evidence has no material regression. Evidence: Codex, 2026-08-13 UTC / 2026-08-14 JST, `PUM-S6-PRODUCTION-REGRESSION-01`; 390px device emulation found no horizontal overflow or clipped controls on populated tier and recommendation pages, with tab, dialog, listbox, combobox, region, and limited-data semantics exposed to the accessibility tree.
- [x] Existing Python suite passes. Evidence: Codex, 2026-08-13 UTC / 2026-08-14 JST, `.venv\\Scripts\\python.exe -m unittest discover -s tests -q`: 227/227 passed after the hosted reconciliation and typed-population additions.
- [x] Existing frontend suite passes. Evidence: Codex, 2026-08-13 UTC, `npm run test:frontend`: 45/45 passed.
- [x] Phoenix 1 archive verification passes. Evidence: Codex, 2026-08-13 UTC, `.venv\\Scripts\\python.exe scripts/verify_phoenix1_archive.py`: 2,470 charts, 2,464 measured, expected SHA-256 verified.
- [x] TypeScript typecheck passes. Evidence: Codex, 2026-08-13 UTC, `npm run typecheck` passed.
- [x] Next.js build passes. Evidence: Codex, 2026-08-13 UTC, `npm run build` compiled, typechecked, prerendered all static pages, and finalized all API routes successfully.

## Phase 5/7: jobs, publication, security, operations

- [ ] Only one global refresh can be active. Evidence:
- [ ] Same-player refreshes deduplicate for 60 seconds. Evidence:
- [ ] Different-player concurrency remains capped at four. Evidence:
- [ ] Duplicate delivery is idempotent. Evidence:
- [ ] Cancelled work cannot publish. Evidence:
- [ ] Global five-minute and player 60-second retry rules match. Evidence:
- [ ] Worker crash before publication leaves pointers unchanged. Evidence:
- [ ] Artifact upload failure leaves the previous generation active. Evidence:
- [ ] Atomic publication never exposes a partial generation. Evidence:
- [ ] `pumbility` remains unexposed through PostgREST. Evidence:
- [ ] Supabase and PIU credentials remain server-side and are not stored in PostgreSQL. Evidence:
- [ ] Evidence/log scanners find no IDs, raw scores, secrets, or SQL parameters. Evidence:
- [ ] Storage remains private and checksum-gated. Evidence:
- [ ] Baseline p50/p95/p99 endpoint and job timings are recorded. Evidence:
- [ ] New endpoint/global/player latency meets the approved no-regression threshold. Evidence:
- [ ] Connection, CPU, IO, WAL, disk, and Storage volume stay within budget. Evidence:
- [ ] No repeated whole-snapshot checkpoint serialization remains. Evidence:
- [ ] Daily work does not precompute every player's candidate pool. Evidence:
- [ ] Model binary loads once per generation per warm worker. Evidence:
- [ ] Freshness, failed-generation, mismatch, and pool-exhaustion alerts work. Evidence:

## Phase 6/7/8: canary, cutover, rollback, stabilization

- [ ] Endpoint canaries are independently controllable. Evidence:
- [ ] Canary period records zero unexplained mismatches. Evidence:
- [ ] Active jobs are drained before final switch. Evidence:
- [ ] Final source boundary is recorded and reconciled. Evidence:
- [ ] PostgreSQL becomes authoritative only after the gate passes. Evidence:
- [ ] Blob outbox mirror and read fallback remain available. Evidence:
- [ ] One scheduled production cycle succeeds. Evidence:
- [ ] One full operator sync succeeds. Evidence:
- [ ] Representative eligible/ineligible player refreshes succeed. Evidence:
- [ ] Recommendation rollback succeeds. Evidence:
- [ ] Forward restoration succeeds. Evidence:
- [ ] Database/pooler outage fallback succeeds. Evidence:
- [ ] Pumbility-only database restore procedure is exercised. Evidence:
- [ ] Shared-project restore procedure is approved by the owner. Evidence:
- [ ] Stabilization window completes without unexplained mismatch. Evidence:
- [ ] Final evidence bundle is signed off. Evidence:
- [ ] Dual writes stop only after signoff. Evidence:
- [ ] Blob remains read-only for the rollback window. Evidence:
- [ ] Legacy persistence removal occurs in a later PR. Evidence:
