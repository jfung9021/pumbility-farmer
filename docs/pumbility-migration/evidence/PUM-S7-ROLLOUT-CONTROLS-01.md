# PUM-S7-ROLLOUT-CONTROLS-01

Date: 2026-08-13 UTC / 2026-08-14 JST

Owner: Codex, under the repository owner's approved rapid guarded rollout

## Boundary and result

PR #61 merged as `aa53e1ea7267260c38893bbf4170c686622c877c` and the same commit was
redeployed to production after the two new rollback controls were explicitly installed as `false`.
The ready deployment was aliased to `https://pumbility-farmer.vercel.app`.

Production remains in the documented safe state:

- `PUMBILITY_DATA_BACKEND=shadow`;
- `PUMBILITY_SHADOW_STRICT=false`;
- `PUMBILITY_CANONICAL_SNAPSHOT_WRITE_ENABLED=false`;
- `PUMBILITY_BLOB_MIRROR_ENABLED=false`;
- `PUMBILITY_BLOB_READ_FALLBACK_ENABLED=false`;
- no Supabase read-canary domain is enabled.

The release added five independently allowlisted read domains, exact dual-read comparison with
automatic per-read Vercel fallback, startup rejection of invalid/partial authority flag sets,
Supabase-primary Vercel read fallback, reference-only durable mirror intents and replay, and guarded
operators for the genuine scheduled-cycle and full-sync gates. None of those additions changed the
current Vercel read or publication authority.

## Verification

- Python suite: 239/239 passed before merge.
- Frontend suite: 45/45 passed.
- Phoenix 1 archive: 2,470 charts, 2,464 measured, expected checksum passed.
- Python compilation, TypeScript typecheck, production build, and `git diff --check`: passed.
- PR preview and merged production Vercel deployments: ready.
- Live production HTTP: analysis, tier list, and recommendation-player list returned 200; protected
  cron without authorization returned 401; the three public JSON bodies had no scanned private
  player ID, user ID, raw-score, or score fields.
- Live browser: landing page, populated tier list, and recommendation player selector rendered with
  no console errors.
- Canary telemetry contains domain, outcome, and timings only; a focused regression proves it is
  emitted at the production-default warning threshold and omits the private artifact key.

## Explicitly not claimed

No scheduled production cycle, full sync, post-cycle reconciliation, canonical shadow write,
Supabase read canary, cutover, rollback exercise, or two-hour stabilization gate is claimed by this
evidence. Those flags remain gated by their preceding evidence.
