# PUM-S9-RUNTIME-TYPED-PERSISTENCE-01

Date: 2026-08-13 UTC / 2026-08-14 JST

Owner: Codex

## Result

The production analysis worker now has a dormant, flag-gated path that persists its already-computed
analysis facts and recommendation model metadata into the typed Supabase schema before promoting
the public compatibility pointers. The analysis run is linked to the corresponding typed job row,
and immutable analysis/model generations are safe to resume when a retry finds an identical
`shadow` or `published` row.

Before typed persistence, the worker writes the current canonical snapshot through the existing
dual-write adapter. The typed writer then rereads the relational snapshot and requires its canonical
source hash to match the runtime input exactly. A missing job row, changed snapshot, conflicting
immutable generation, or typed/model persistence error fails the job before public pointer
promotion. The staging checkpoint remains available for retry.

The path is inactive while `PUMBILITY_CANONICAL_SNAPSHOT_WRITE_ENABLED=false`. Production therefore
continues to use Vercel for reads and public writes, and this evidence does not claim a scheduled
cycle, canonical-write activation, read canary, or cutover.

## Verification

- Focused Python suite: 97/97 passed.
- Full Python suite: 247/247 passed.
- Frontend API/runtime suite: 45/45 passed.
- Python compilation, TypeScript typecheck, production Next.js build, and `git diff --check`: passed.
- Regression coverage proves typed rows are attempted before public pointer promotion, the typed
  analysis is linked to the runtime job, identical immutable retries are idempotent, and a typed
  persistence failure leaves the public latest pointer unchanged while retaining the staging
  checkpoint.
