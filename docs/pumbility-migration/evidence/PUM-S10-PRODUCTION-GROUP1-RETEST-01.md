# PUM-S10-PRODUCTION-GROUP1-RETEST-01

Date: 2026-08-14 UTC / 2026-08-15 JST

Operator: Codex. Source and candidate code boundary: `119fc11`. Sanitized raw samples and their
checksums remain in ignored local evidence; deployment identities and private digests are not
committed. Owner signature remains pending.

Scope: production group-1 read-canary retest on rollout commit `119fc11`, followed by an
immediate no-canary rollback and one operator-triggered Vercel Cron smoke. This record contains
only aggregate-safe evidence. It does not contain deployment references, request identifiers,
artifact identifiers, response digests, credentials, database locations, or player data.

## Preconditions

- A fresh hosted reconciliation completed all ordered stages against a stable Vercel boundary.
- Phoenix 1 had 582,301 exact relational leaves and Phoenix 2 had 15,544.
- 173 JSON artifacts, one binary artifact, and 56 cached-player artifacts matched exactly.
- The privacy gate passed.
- Vercel remained authoritative; strict shadowing, canonical writes, Blob mirror, Blob fallback,
  selected-player refresh, and the read-canary allowlist were disabled.
- Nine fixed local pre-canary regression checks passed.

## Adjacent production measurements

The corrected probe used gzip, three cache-bypass mechanisms, three unscored warmups, and 100
scored requests per domain over each 15-minute window. The retained aggregate run identifiers are
`20260814T154959690Z` for the Vercel-only baseline and `20260814T161042848Z` for the canary.

| Domain | Metric | Baseline | Canary | Fixed limit | Diagnostic result |
|---|---:|---:|---:|---:|---|
| analysis | p95 | 859.616 ms | 1,183.740 ms | 945.578 ms | failed |
| analysis | p99 | 982.211 ms | 1,594.714 ms | 1,178.653 ms | failed |
| tier-list | p95 | 935.442 ms | 1,196.511 ms | 1,028.986 ms | failed |
| tier-list | p99 | 1,273.276 ms | 1,255.753 ms | 1,527.931 ms | passed |

Both windows returned 100/100 scored successes and 3/3 successful warmups per domain, with zero
HTTP errors, JSON errors, or cache hits. The canary deployment's logs were read in bounded time
segments and deduplicated by the immutable log-record identifier. The exact result was 206 unique
events: 103 `analysis` and 103 `tier-list`, all `candidate-served`, with zero authority errors,
candidate errors, fallbacks, mismatches, or conflicting outcomes.

The fixed latency diagnostic remains failed. The owner's latency-only waiver was not used to
advance the rollout because candidate-topology qualification remains incomplete. The alias was
returned to the retained READY no-canary deployment immediately after evidence capture.

## Cron smoke and resulting safe state

The deployed Vercel Cron definition remained enabled at `/api/cron/phoenix2` with its daily
schedule unchanged. The operator used Vercel's authenticated immediate-run control once. The cron
route returned HTTP 202, its queue subscriber returned HTTP 200, and the public Phoenix 2 analysis
generation advanced after the trigger. This proves the deployed route and worker can execute, but
it is a manual control-plane smoke and does not satisfy the separate genuine time-scheduled
topology gate.

The smoke ran on the stricter Vercel-only rollback deployment. Consequently, Vercel advanced while
the Supabase shadow remained unchanged. A protected hosted reconciliation then failed closed after
completing only `source-boundary`. The production read-canary allowlist remains empty, so this
expected shadow staleness cannot affect user responses. Before any later canary or cutover, restore
the shadow through the guarded production backfill/population path and rerun exact reconciliation,
privacy, stable-boundary, and pre-canary gates.

## Decision

- Production authority: Vercel.
- Supabase read canary: off.
- Supabase authoritative reads/writes: not enabled.
- Canonical shadow writes: off on the active deployment.
- Group 1 correctness: passed for the recorded window.
- Group 1 latency diagnostic: failed.
- Rollout advancement: blocked on restored exact shadow parity and the documented non-latency
  topology qualification gates.
