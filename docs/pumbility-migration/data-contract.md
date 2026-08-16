# Stored-data contract

The migration is a representation change. These logical records, invariants, orderings, and lineage fields must survive unchanged.

## Mixes

`mix_registry.py` and `lib/mixes.ts` define the identities:

| Key | Upstream value | Label | State |
| --- | --- | --- | --- |
| `phoenix1` | `Phoenix` | Phoenix 1 | Archived |
| `phoenix2` | `Phoenix2` | Phoenix 2 | Active/default |
| `combined` | `Phoenix+Phoenix2` | Phoenix 1 + 2 | Derived only |

Accepted Python aliases remove spaces and are case-insensitive. Unsupported mixes are errors.

## Snapshot schema

`phoenix2_sync.SNAPSHOT_SCHEMA_VERSION` is 2.

Player records logically contain:

- Internal upstream identifier (`playerId` in Blob snapshots; `userId` on local disk).
- Consented username.
- `lastSyncedAtUtc`.
- `lastScoreRecordedAtUtc`.

Chart records are allowlisted by `CHART_FIELDS`:

- `id`, `songName`, `type`, `level`, `difficulty`, `imageUrl`.
- `noteCount`, `stepArtist`, `bpmMin`, `bpmMax`.

Score records are allowlisted by `SCORE_FIELDS`:

- `playerId`, `chartId`, `pumbility`, `score`.
- `letterGrade`, `plate`, `recordedAt`, `isBroken`.

Required invariants:

- One player record per upstream player identifier.
- One current chart record per upstream chart identifier.
- One current valid best-score record per player/chart pair within a mix.
- Every score references an existing consented player and current chart.
- Non-finite Pumbility and broken rows are excluded.
- Chart BPM endpoints are positive or null and ordered minimum-to-maximum.
- Current rows for revoked players and charts absent from the current catalog are removed during global synchronization.

The deterministic best-row winner order in `phoenix2_sync._score_priority` is:

1. Highest finite Pumbility.
2. Highest finite raw score.
3. Lexicographically greatest raw `recordedAt` string.
4. Greatest grade-plus-plate metadata completeness count.
5. Lexicographically greatest chart identifier.

The database representation must preserve this exact winner.

## Synchronization semantics

- Daily known-player fetches use `recordedAfter = lastSyncedAtUtc - 7 days`.
- New players receive a full fetch.
- Previously empty players are skipped until 24 hours after their last check.
- Schema-1 snapshots are discarded and fully refetched to recover grade/plate metadata.
- Six workers share a 125 ms request-start limiter and shared `Retry-After` block.
- Paging follows the upstream `next` URL exactly and rejects loops or a different host.
- Checkpoints occur every 50 completed players and contain the full working snapshot.
- An interactive player refresh deliberately fetches the complete Phoenix 2 best-score history without `recordedAfter`, then merges it with stored state.
- Absence from an interactive response is not a deletion signal.

## Analysis contract

`piu_misgrade_analyzer.SCRIPT_VERSION` identifies the implementation. `build_web_payload` serializes floats at six decimal digits and emits:

- `generatedAtUtc`, `mix`, `summary`.
- Separate ordered `singles` and `doubles` arrays.
- Ten fixed `relativeGroups`.
- Seven fixed `effectBands`.

Every chart result contains the ordered field set defined at `piu_misgrade_analyzer.py:1106`:

- Identity/rank: mode, mode rank, level rank/percentile/comparison count, folder, relative group, effect band, song, difficulty, type, level, chart ID.
- Catalog: image, note count, step artist, BPM range.
- Estimates: estimated/average difficulty, difficulty delta, folder size/compression, delta and difficulty confidence intervals.
- Evidence/calibration: Pumbility-per-level, raw/shrunk ease, mean/median/std/chart residual, shrinkage K, residual confidence interval, level reference and expected residual.
- Coverage: contributors, players scored, appearance rate, reliability, contributor baseline, evidence status.

Player-mode baselines contain mode, pseudonymous player hash, valid count, baseline Pumbility/std/min/max/count. Private contribution records contain selection ranks/flags, timestamps, Pumbility, baseline, residual, mode, and pseudonymous player hash. Contributions remain private.

Required statistical invariants are implemented and tested in `tests/test_analyzer.py`:

- Finite, non-broken, strictly positive Pumbility eligibility.
- Thirty valid scores in either mode to enter analysis.
- Ranks 11–30 player-mode baseline.
- Top-fraction Pumbility plus recency union with top-100 fallback.
- Independent Singles and Doubles calibration, shrinkage, folders, and ranks.
- Exact-folder reference and one-sided large-folder compression.
- Difficulty delta scale 0.4.
- Seven absolute effect bands and ten midpoint-percentile relative groups.

## Combined tier contract

`piu_recommendations.COMBINED_TIER_SCHEMA_VERSION` is 5.

- Phoenix 2 charts are the strict target allowlist.
- Phoenix 2 replaces Phoenix 1 for an overlapping player/chart observation.
- Version/mode residuals are normalized to level units before joining.
- Singles and Doubles remain limited to official level 16+ and retain their existing independent models.
- Co-op adds a separate `coop` array containing the current Phoenix 2 2x, 3x, 4x, and 5x catalog without an official-level gate.
- Phoenix 1 rerates are presentation provenance, not inputs that mutate the frozen public analysis.
- Two Phoenix 1 score overrides are applied only to Phoenix 1 evidence.
- Every Single and Double chart contains `whatIfEstimates`, ordered by alternative official level. Each entry has `level` and an `estimatedDifficulty` rounded to six decimal digits or `null` when the target folder has no measured reference or the chart has no usable observations. Co-op does not use these folder what-if estimates.
- Alternatives cover the three official levels below and above the chart, omit its current level, and never go below level 16. Near the floor the list is correspondingly shorter.
- What-if values are chart-only projections, not tier-list recalculations. They preserve the selected contribution set, player baselines, reliability/shrinkage, target-folder reference and range compression, ranks, percentiles, and tier membership.
- Phoenix 2 observations are revalued at the hypothetical level with the existing score-derived grade, recorded plate, and Phoenix 2 Pumbility formula. Phoenix 1 observations, and Phoenix 2 observations without sufficient grade/plate data, retain the existing normalized one-level residual shift.
- The hypothetical chart residual is recomputed from the frozen observations and compared with the frozen target-folder model using the existing 0.4 difficulty-delta scale. No hypothetical confidence interval, rank, percentile, or effect band is published.
- Co-op tier difficulty uses a player/source-adjusted conditional chart q75 in log miss-point space. The player/source fit uses every observation; raw scores and residuals are not trimmed. The conditional quantile supplies outlier resistance.
- Co-op difficulty is calibrated monotonically to whole integers from 10 through 25, anchors the median measured chart at 17, and does not impose a normal distribution or tier-size quota.
- Co-op recommendation goals use monotonic letter-grade bands determined only by whole-number estimated difficulty and a fixed Fair Game plate. The current 140-chart goal ladder totals exactly 16,000 Co-op Rating (`[CO-OP] Master`).
- Equal Co-op projected gains are ordered by the underlying continuous difficulty signal before stable name/ID fallbacks; the UI still displays the rounded whole-number difficulty.
- The raw exact-chart population q75 score-and-plate pair remains in the payload as analysis provenance and is not used as the recommendation target.

## Recommendation contract

- Public recommendation schema: 23.
- Legacy storage schema: 2, ten-player public shards.
- Selected-player refresh storage schema: 3.
- Global model artifact schema: 5.
- Player state schema: 1.
- Private input shard size: ten players.

Public player keys are the first 20 hexadecimal characters of SHA-256 over the stable namespace and internal player ID. Duplicate case-insensitive usernames receive the last four characters of the public key as a display suffix. This mapping must remain stable through migration.

The compact public player list exposes only public key, username, display name, and Singles/Doubles eligibility. It never exposes internal IDs or mode payloads.

Per-player results follow `lib/types.ts` and contain generation timestamps, staleness, method, public player metadata, and Overall/Single/Double mode results. Each mode may contain:

- Eligibility/reason and valid counts.
- Rating and projection sources/windows.
- Current top-50 totals/cutoff/counts.
- Candidate range/counts.
- Complete filter candidate pool.
- Ordered top 20.

Candidate rows retain chart metadata, estimated difficulty/evidence, distance and farm edge, played state, existing/expected Pumbility, projected gain/score/grade/plate/probability, source/support/confidence.

Recommendation invariants are tested in `tests/test_recommendations.py`:

- Display rating uses top 20; projection rating uses ranks 11–30.
- Phoenix 2 supplies rating at 20 rows and projection at 30; complete Phoenix 1 windows are the fallback.
- Played/current/top-50 state always uses Phoenix 2.
- Overall uses the highest 50 values from the shared S+D pool.
- Suggested candidates extend through rating +0.5 without a lower estimated-difficulty bound.
- Official-level filters use every matching level-16+ catalog chart.
- Peer searches try radii 0.2–0.5 for 20, then 10, then five peers; fewer than five uses population fallback.
- The selected player is excluded from peers.
- Projected plate uses the ordered weighted median with the lower exact-50% boundary.
- Equal displayed gains prefer lower estimated difficulty, then expected Pumbility/name/chart ID.

## Jobs and publication

Global jobs contain ID, status, stage, progress, timestamps, retry time, error, attempt, full-sync/reanalysis flags, trigger, and mix. Player jobs additionally contain kind and public player key plus timing diagnostics after execution.

Status values are queued, running, completed, and failed. A failed global job retries after five minutes; a failed selected-player job retries after 60 seconds. A global active job is considered stale after five minutes without an update. The selected-player status endpoint declares queued/running jobs stale after 30 seconds.

Stable publication pointers must never reference an incomplete generation. Existing Blob publication is multi-write; the target database implementation must improve atomicity without changing the externally visible generation contract.

## Hashing and parity

- Public artifacts: canonical SHA-256 plus raw-file SHA-256 when byte identity matters.
- Private datasets: whole-dataset HMAC-SHA256 using `PUMBILITY_BASELINE_HMAC_KEY`.
- No per-player digest is committed.
- Canonical numeric hashing treats equivalent JSON numeric spellings as equal and rejects non-finite values.
- Analysis has both exact and semantic hashes; semantic hashes omit generation/capture timestamps.
- Every count/hash difference after `T0` must be either exact or matched by one exact evidence entry. No percentage tolerance is allowed to hide a mismatch.
