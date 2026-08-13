# Pumbility current-state inventory

This document freezes the current persistence and processing boundaries before the Supabase migration. It describes executable behavior as of the migration baseline work; where README prose differs from code, code and tests are authoritative.

## Ownership boundary

- `bite-open-card-draw` owns Supabase migrations, database roles/grants, RLS, database functions, Storage policies, and schema recovery.
- This repository owns synchronization, analysis and recommendation algorithms, application adapters, FastAPI and browser contracts, local/demo behavior, reconciliation, and regression evidence.
- The target database namespace is the private `pumbility` PostgreSQL schema. Tables do not use a `PUMBILITY_` prefix.
- No Supabase schema or production data is changed during Phase 1.

## Current authoritative production stores

`analysis_runtime.PrivateBlobStore` is the production artifact adapter. All objects are private.

| Logical data | Current object | Writer/reader |
| --- | --- | --- |
| Phoenix 2 latest analysis | `analysis/phoenix2/latest.json` | `publish_success`, `read_latest_payload` |
| Legacy Phoenix 2 fallback | `analysis/latest.json` | read-only fallback in `read_latest_payload` |
| Combined tier result | `analysis/combined/latest.json` | `publish_success`, `api.tier_list` |
| Phoenix 2 current private snapshot | `analysis/private/phoenix2-current.json` | `execute_analysis_job`, `publish_success` |
| Frozen Phoenix 1 private evidence | `analysis/private/phoenix1.json` | one-time seed; `execute_analysis_job` |
| Phoenix 2 checkpoints | `analysis/phoenix2/staging/<job>.json` | `execute_analysis_job` |
| Immutable Phoenix 2 aggregate runs | `analysis/phoenix2/runs/*.json` | `publish_success`; latest ten retained |
| Recommendation publication pointer | `analysis/recommendations/latest.json` | `publish_success` |
| Versioned recommendation pointers | `analysis/recommendations/indexes/<generation>.json` | model publish/rollback |
| Legacy recommendation shards | `analysis/recommendations/generations/<generation>/shards/*.json` | schema-2 publisher |
| Generation model metadata | `analysis/recommendations/models/<generation>.json` | schema-3 publisher |
| Generation numeric model | `analysis/recommendations/models/<generation>.npz` | schema-3 publisher |
| Frozen private Phoenix 1 inputs | `analysis/private/recommendation-inputs/<generation>/phoenix1/*.json` | schema-3 publisher |
| Frozen private Phoenix 2 inputs | `analysis/private/recommendation-inputs/<generation>/phoenix2/*.json` | schema-3 publisher |
| Incremental selected-player state | `analysis/private/recommendation-player-state/<public-key>.json` | player refresh worker |
| Public-safe selected-player result | `analysis/recommendations/players/<public-key>.json` | player refresh worker |

Schema-3 retention keeps the current generation, at least two schema-3 generations, every schema-3 generation younger than 48 hours, and legacy schema-2 generations unless explicit pruning is enabled. Abandoned staging objects older than 24 hours are removed. Revoked players' state and cached results are removed on a promoted schema-3 daily run.

`analysis_runtime.RuntimeJobStore` uses Vercel Runtime Cache namespace `pumbility-analysis`:

- `job:<id>`: job status, 24-hour TTL.
- `active-job`: singleton global active-job pointer.
- `latest-job:<mix>`: latest job pointer per mix.

Vercel Queues/Celery remains the execution layer:

- `analysis` queue for global work.
- `player-recommendations` queue for selected-player work.
- Queue retention is 24 hours, lease duration 800 seconds, late acknowledgement and task-ID idempotency are enabled.
- The player subscriber is capped at four concurrent consumers.

## Repository-owned immutable/public reference data

| Artifact | Contract |
| --- | --- |
| `public/data/phoenix1.json` | Frozen public Phoenix 1 chart aggregate. It is not the private Phoenix 1 score evidence. |
| `public/data/phoenix1.manifest.json` | Frozen archive checksum, counts, timestamp, and historical methodology `6.0.0-level-16-and-0.7-scale`. |
| `public/data/phoenix1-rerates.json` | Display/provenance annotations only: 231 changes, currently 197 up and 34 down. |
| `lib/data/nevsister-chart-videos.json` | Validated chart-to-video catalog; test contract currently requires 2,572 mappings. |
| `lib/data/nevsister-chart-video-overrides.json` | Manual mappings, aliases, and notes. |
| `public/images/phoenix2-ranks/*.webp` | Thirty-seven vendored Overall rank emblems. |
| `phoenix1_score_overrides.py` | Two Phoenix 1-only score transformations and reband behavior. |
| `phoenix2_pumbility.py` | Current grade, plate, formula, and projection constants. |

## Private local store

`.local-data/piu-scores/<mix>/current/` contains:

- `players.json`
- `charts.json`
- `scores.jsonl.gz`
- `snapshot_manifest.json`

`.local-data/piu-scores/<mix>/analysis/` contains the chart aggregate, summaries, rankings, pseudonymous player baselines, and folder exports. `.local-data/piu-scores/combined/analysis/web_results.json` contains the combined tier result. `.local-data/piu-scores/recommendations/` contains the local recommendation index and generation artifacts.

These files are private development inputs, not a production authority. The currently available local manifests report:

- Phoenix 1: 814 player records, 4,571 chart records, 576,916 best-score records.
- Phoenix 2: 831 player records, 4,616 chart records, 7,998 best-score records.

Those counts must not be substituted for the future production `T0` boundary.

## Non-authoritative material

The following must not be imported as production facts:

- `piu_live_run/`, `piu_phoenix_run/`, and `synthetic_demo/`.
- Local CSV exports, screenshots, stdout/stderr logs, and verification scratch directories.
- Test fixtures, including the public chart-aggregate regression fixture.
- `.next`, `node_modules`, caches, and build metadata.

## Current processing sequence

1. `synchronize_mix_snapshot` discovers consent, songs, charts, and score pages.
2. `execute_analysis_job` writes resumable checkpoints and obtains deterministic analyzer input.
3. `analyze_snapshot` produces version-specific chart analysis.
4. `build_combined_chart_results` and `build_combined_tier_payload` join frozen Phoenix 1 and live Phoenix 2 evidence.
5. `build_recommendation_model_artifacts` fits one daily global generation.
6. `publish_recommendation_model_artifacts` writes immutable generation artifacts.
7. `publish_success` updates stable pointers and retention.
8. A selected-player worker loads one frozen generation, performs a complete Phoenix 2 score fetch, best-row merge, calculation, and player-result replacement.

The migration must keep this order and statistical implementation until parity is proven.

## Phase 1 production blocker

The committed baseline manifest is intentionally marked `pending-production-capture`. Importing the collector, running its local command, and running its tests do not construct a production store or access production. The explicit production command now lazily constructs the existing `VercelPrivateBlobStore`; its in-memory tests prove that it pins all active pointers, reads every referenced JSON/NPZ artifact and both private snapshots, reads the mutable Phoenix 2 snapshot twice, re-reads the pointers, and accepts only an exactly unchanged boundary. No authorized production `T0` capture was run during implementation, so schema implementation remains blocked on that operational capture and review of its ignored private evidence.
