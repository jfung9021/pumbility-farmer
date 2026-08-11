# Pumbility Farmer

Pumbility Farmer is a PIU Phoenix scoring-difficulty analyzer and Vercel web UI. Its primary tier list combines normalized Phoenix 1 and Phoenix 2 score evidence against the current Phoenix 2 catalog. Phoenix 1 is a frozen, privacy-safe source captured on August 7, 2026; Phoenix 2 remains live and uses the upstream `mix=Phoenix2` filter. Singles and Doubles rankings are completely independent. Published chart analysis starts at level 16, while player baselines and contribution cutoffs use each eligible player's complete mode history, including levels below 16.

## Analysis method

Each mode is processed separately:

1. Deduplicate to a player's best score per chart.
2. Exclude broken, non-finite, zero, and negative Pumbility rows, then rank the
   remaining scores within Singles or Doubles.
3. Require at least 30 positive-Pumbility scores in that mode.
4. Use the mean of ranks 11–30 as the player's mode-specific skill baseline.
5. Select both the top 20% of that player's valid scores by Pumbility and the
   most recent 20% by `recordedAt` within the mode. The two windows use the same
   rounded-up per-player limit and are deduplicated by player/chart before chart
   analysis. If their union contains fewer than 100 scores, use the player's top
   100 by Pumbility instead (or all available scores when the player has fewer
   than 100).
6. Calculate a signed residual between each retained chart and the player's baseline.
7. Within each mode, compare a chart only with measured charts at the exact same official level; the median chart residual is that folder's reference.
8. Estimate Pumbility per level independently for Singles and Doubles by comparing
   scores from the same player in narrow raw-score bands. Calibration never uses
   chart residuals and never silently falls back to the former `50` divisor. Its
   positive empirical scale is version-specific, which supports the larger
   Phoenix 1 Pumbility scale without applying Phoenix 2 bounds.
9. Estimate mode-wide empirical-Bayes shrinkage from within-chart noise and
   between-chart variance.
10. Anchor the typical official level `L` chart at `L + 0.5` and shrink
    low-evidence estimates toward that reference.

The displayed difference uses 40% of the calibrated residual conversion to keep
the estimated scoring-difficulty range from becoming overly wide:

```text
difficulty difference = -0.4 × shrunk Pumbility residual / Pumbility per level
estimated scoring difficulty = official level + 0.5 + difficulty difference
```

Two frozen Phoenix 1 charts need raw-score corrections before they enter any
analysis or recommendation model. Phoenix 1 scores use these formulas:

- `Solve My Hurt - SHORT CUT - D26`:
  `(((score / 1,000,000 × 1,566) - 540) / 1,026) × 1,000,000`
- `Slam D24`:
  `(((score / 1,000,000 × 1,004) - 300) / 704) × 1,000,000`

Phoenix 2 scores and every other chart are unchanged. Each corrected score also
selects the corresponding Phoenix 1 Pumbility band for combined tier evidence.

A negative value is easier to score than the typical chart in the same mode and official level. Continuous estimates are not hard-clamped to the official folder, but the `L + 0.5` center and evidence shrinkage mean that an estimate below `L` requires an unusually strong within-folder signal.

The analyzer does not use the chart catalog's existing `scoringLevel` or an existing tier list.

## Magnitude bands and relative ranks

The primary scoring tiers use seven fixed absolute bands. Overrated and
Underrated begin beyond `±0.50`; the inner boundaries are `±0.30` and `±0.10`.

The bands are not filled by quota: a folder may contain several charts in a band
or none when its measured charts genuinely have similar scoring difficulty.

Larger folders naturally have more chances to contain a tail value, so the
centered differences for every chart in a folder are multiplied by
`min(1, expectedNormalMax(30) / expectedNormalMax(n))`, where `n` is the number
of measured charts in that folder. This normalizes the expected range to a
30-chart reference. The correction grows slowly with the expected extreme
(roughly with the square root of the log of chart count), never expands a
folder, and never assigns categories by quota. A genuinely strong point
estimate can therefore remain Overrated or Underrated regardless of its
publication status or confidence interval.

| Difficulty difference | Effect band |
| ---: | --- |
| `< −0.50` | Overrated |
| `−0.50 to −0.30` | Very Easy |
| `−0.30 to −0.10` | Easy |
| `−0.10 to +0.10` | Medium |
| `+0.10 to +0.30` | Hard |
| `+0.30 to +0.50` | Very Hard |
| `> +0.50` | Underrated |

Midpoint-percentile deciles remain available as a separate within-folder rank.
Their labels are deliberately descriptive rather than semantic:

| Within-level percentile | Group |
| ---: | --- |
| `0–10%` | Easiest 10% |
| `10–20%` | 10–20% percentile |
| `20–30%` | 20–30% percentile |
| `30–40%` | 30–40% percentile |
| `40–50%` | 40–50% percentile |
| `50–60%` | 50–60% percentile |
| `60–70%` | 60–70% percentile |
| `70–80%` | 70–80% percentile |
| `80–90%` | 80–90% percentile |
| `90–100%` | Hardest 10% |

The percentile and numerical effect answer different questions: percentile shows
placement in the folder, while the effect band preserves magnitude in level units.

## Python CLI

Install Python 3.12 dependencies:

```bash
python -m pip install -r requirements.txt
```

Run the controlled validation fixture:

```bash
python piu_misgrade_analyzer.py synthetic --output-dir ./synthetic_demo
```

Analyze a cached snapshot:

```bash
python piu_misgrade_analyzer.py cache \
  --mix phoenix2 \
  --raw-dir ./piu_phoenix_run/raw \
  --output-dir ./piu_analysis
```

Run against PIU Scores using a server-side environment variable:

```bash
export PIU_SCORES_API_KEY='piu_scores_live_...'
python piu_misgrade_analyzer.py live --output-dir ./piu_analysis
```

Primary outputs include:

- `web_results.json`: complete UI data contract with separate `singles` and `doubles` arrays.
- `singles_rankings.csv` and `doubles_rankings.csv`: independent mode rankings.
- `chart_tiers.csv`: combined export with an explicit mode column.
- `analysis_summary.json`: method and coverage diagnostics.
- `player_baselines_pseudonymous.csv`: separate player-mode baselines with hashed IDs.
- `folders/*.csv`: one export for every level-16+ folder found in the catalog.

## Private local snapshot and visual analysis

Local methodology work can use privacy-minimized snapshots of Phoenix 2 best scores visible to
the configured credential. A community-tool key can read only players who explicitly
shared data with that tool; this is not a global PIU Scores export.

The local snapshots live under `.local-data/piu-scores/<mix>/`. Raw player IDs and score histories are
never served to the browser. Phoenix 2 snapshots retain the consented username required by the
recommendation dropdown, while game tags and all other profile fields remain excluded. In local
mode, the dashboard reads each mix's chart-level aggregate
from `analysis/web_results.json`, including the re-analyzed Phoenix 1 result. A legacy Phoenix 2 aggregate at
`.local-data/piu-scores/analysis/web_results.json` remains readable until it is replaced.

Set the credential in the current PowerShell process and capture a complete snapshot:

```powershell
$env:PIU_SCORES_API_KEY = "piu_scores_live_..."
npm run snapshot:local
Remove-Item Env:PIU_SCORES_API_KEY
```

`snapshot:local` remains a Phoenix 2 alias. Phoenix 1 stays archived in the application, but an
explicit one-time replacement capture is available with `npm run snapshot:phoenix1:rebuild`.
That command discards the old local checkpoint and captures Phoenix 1 from scratch; it is not a
recurring refresh path.

The capture follows every API cursor, uses the shared rate limiter and retry behavior, strips
profile fields, validates references and uniqueness, and promotes staged files only after the
snapshot passes validation. An interrupted capture can resume from its private staging file; use
`python scripts/capture_private_score_snapshot.py --restart` to discard that checkpoint.

Analyze the cached data without network access and launch the existing dashboard:

```powershell
npm run analyze:local
npm run dev:local
```

`npm run analyze:local` re-analyzes the cached Phoenix 1 and Phoenix 2 snapshots and builds the
private local recommendation index. Use
`npm run analyze:phoenix1` or `npm run analyze:phoenix2` to re-analyze only one version.
The Phoenix 1 result is written only to `.local-data`; the frozen public archive is not changed.

Open `http://localhost:3000`. Local mode is enabled by the ignored `.env.local` file. The dashboard
shows a **Local snapshot** badge and its refresh button reloads the aggregate from disk instead of
starting a production job. After changing the methodology, run `npm run analyze:local` again and
click **Reload local results**; another API pull is needed only when fresher score data is desired.

For a fast deterministic iteration, call the cache command directly with `--bootstrap-samples 0`.
Use the production bootstrap setting before accepting a methodology change. Chart jacket URLs are
not cached, so images still require network access even though the ranking analysis does not.

The leading dot and Git ignore protect against accidental discovery and commits, not unauthorized
local access or disk theft. On Windows, `attrib +H .local-data` can additionally hide the directory
in Explorer, but it is not a security control. Delete `.local-data/piu-scores` to remove all locally
captured private data.

## Web application

Install frontend dependencies and build:

```bash
npm install
npm run typecheck
npm run build
```

The frontend is a Next.js application. `/` is the feature landing page, `/tier-list` contains the
combined Phoenix rankings dashboard, and `/recommendations` contains the player-specific route.
Phoenix 1 loads the frozen public artifact at `/data/phoenix1.json`;
`/api/analyze?mix=phoenix1` redirects to that canonical copy.
Phoenix 2 remains the default. The Python function at `/api/analyze` supports:

- `GET /api/analyze?mix=phoenix2`: load the latest successful `AnalysisPayload`.
- `GET /api/analyze?mix=phoenix2&jobId=...`: load a matching 24-hour queue-job status.
- `POST /api/analyze?mix=phoenix2`: protected administrator trigger; requires `X-Analysis-Run-Secret` matching `CRON_SECRET` and queues or follows a full Phoenix 2 refresh.
- `POST /api/deploy?mix=phoenix2`: validate and acknowledge a legacy signed deployment event without starting analysis.
- `GET /api/recommendations/players`: return consented usernames and mode eligibility without raw IDs; successful lists are cached for five minutes with stale revalidation.
- `GET /api/recommendations?playerKey=...`: return the last cached recommendation for one player.
- `POST /api/recommendations/refresh?playerKey=...`: synchronize only that player's new Phoenix 2 scores and queue a lightweight recommendation calculation; requests for the same player within 60 seconds are deduplicated.
- `GET /api/recommendations/refresh?jobId=...`: poll a player-refresh job.
- `GET /api/tier-list`: return the public combined Phoenix 1 and Phoenix 2 tier aggregate.

Phoenix 1 POST, cron, deployment, worker, and publisher paths reject updates as archived.

The Phoenix 1 chart cards also show official-rating changes in Phoenix 2. A separate
annotation file maps chart IDs to their Phoenix 1 and Phoenix 2 ratings, so these labels do not
modify the frozen scoring analysis. The current import contains 231 level-16+ changes from the
`Phoenix 2 build` worksheet: 197 uprates and 34 downrates. Regenerate the annotation layer from
an updated copy of the source workbook with:

```powershell
python scripts/import_phoenix2_rerates.py "C:\path\to\PIU Phoenix 2 chart rerates & removals.xlsx"
```

The importer pairs multi-chart rows, resolves documented title aliases, matches the frozen archive
by song and Phoenix 1 difficulty, and fails instead of publishing partial or ambiguous matches.

The response contracts are:

```text
200 { outcome: "fresh", generatedAtUtc, nextAllowedAtUtc }
202 { outcome: "started" | "existing", job }
409 { outcome: "busy", activeMix, error }
409 { outcome: "archived", archiveUrl, error }
```

The `fresh` response shape remains compatible for when the cooldown is restored, but it is not emitted while the successful-run freshness window is zero.

The browser polls active jobs every two seconds (ten seconds in a hidden tab), displays the synchronization stage and player progress, then reloads the latest rankings after completion. All responses are read as text before conditional JSON parsing so a platform-generated timeout page is shown as a useful message instead of a JSON syntax error.

The FastAPI publisher and Celery subscriber declared in `pyproject.toml` run as a private-data backend in Vercel Services; the Next.js UI remains the frontend service. The subscriber uses Vercel Queues through the `vercel://` broker. Vercel Runtime Cache stores job status, and the worker stores only private JSON objects in Vercel Blob:

- `analysis/phoenix2/latest.json` — current Phoenix 2 aggregate.
- `analysis/combined/latest.json` — current combined tier-list aggregate.
- `analysis/private/phoenix2-current.json` — private, privacy-minimized incremental snapshot.
- `analysis/private/phoenix1.json` — frozen private Phoenix 1 recommendation and plate evidence.
- `analysis/recommendations/latest.json` — compact player index and atomic pointer to the current daily model generation.
- `analysis/recommendations/indexes/<generation>.json` — immutable rollback pointer for a published or shadow generation.
- `analysis/recommendations/models/<generation>.json` — daily catalog, plate-population, recommendation-method, and score-model metadata.
- `analysis/recommendations/models/<generation>.npz` — compressed, non-pickle population score surfaces and chart-indexed all-score peer cohorts.
- `analysis/private/recommendation-inputs/<generation>/{phoenix1,phoenix2}/*.json` — private ten-player input shards used by player-only refreshes.
- `analysis/private/recommendation-player-state/<playerKey>.json` — newest incrementally merged Phoenix 2 state for one player.
- `analysis/recommendations/players/<playerKey>.json` — cached public-safe top-50 result for one player; full candidate arrays are not stored.
- `analysis/phoenix2/staging/<job>.json` — resumable 50-player checkpoints.
- `analysis/phoenix2/runs/*.json` — the latest ten immutable Phoenix 2 aggregate runs.

The old `analysis/latest.json` is a read-only Phoenix 2 fallback and is no longer written.

Seed the frozen Phoenix 1 recommendation evidence once, from the verified local archive, before
the first production recommendation refresh:

```powershell
npm run seed:phoenix1
```

The recommendation engine treats Phoenix 2 `charts.json` as a strict allowlist. Charts absent
from that catalog are removed completely. For the same player and chart, a Phoenix 2 score always
supersedes Phoenix 1; Phoenix 1 is used only when no Phoenix 2 score exists. Version-specific
Pumbility residuals are converted to level units before evidence is combined.

Player rating history is selected independently for Singles and Doubles. The public recommendation
rating averages ranks 1-20 by Pumbility and converts that average to the continuous chart level where
an S with Fair Game earns the same Phoenix 2 Pumbility. A mode uses Phoenix 2 once it has 20 valid,
deduplicated scores; below that threshold it uses a complete Phoenix 1 top 20, then any available
Phoenix 2 history. This public rating is displayed on the page and sets the eligible-chart ceiling.

Score projections use a separate rating calculated with the same S-and-Fair-Game conversion from
Pumbility ranks 11-30. Phoenix 2 supplies that window at 30 valid scores; otherwise a complete
Phoenix 1 ranks 11-30 window is used. A mode without either complete 30-score source still receives
top-20-based farm-edge recommendations, but it does not receive personal projected scores. When
training or projecting an already-played target chart, that chart is removed from the projection
window and rank 31 is promoted when available. Played status, existing Pumbility, current top-50
totals, and projected gain always use Phoenix 2.

The recommendation page opens on **Overall**, followed by **Single** and **Double**. Overall keeps
the mode-specific rating and projection for every chart, merges the displayed top 50 from each
eligible mode, recalculates each candidate's gain against one shared Single-and-Double Phoenix 2
pool, and displays the best 50 merged opportunities. Overall Pumbility is the sum of the highest 50
Phoenix 2 Pumbility values in the union of the player's Single and Double scores; it is not the sum
of both mode totals. If only one mode can be rated, Overall still uses that mode's recommendations
and explains which source is unavailable, while every existing Phoenix 2 score in either mode still
participates in the Overall total.

The current top-50 total also drives the progress indicator. Single and Double use their separate
Phoenix 2 skill-title ladders (Beginner, Intermediate, Advanced, Expert, and the mode-specific
Master title at 19,000). Overall uses the Phoenix 2 Pumbility rank ladder from Unranked through the five
divisions of Bronze, Silver, Gold, Platinum, Diamond, Red Beryl, and Alexandrite, followed by
Phoenix at 20,000. Phoenix 1 may supply an existing rating or projection fallback, but never a
current Pumbility total or progress value.

Suggested-chart eligibility has no lower estimated-difficulty bound and extends through 0.5 points
above the player's scoring rating. Charts beyond that upper bound are excluded before projected-gain
ranking.

Projected raw scores target the unweighted median (50th percentile) among all other players with a
similar ranks 11-30 projection rating and a normalized result on the exact chart. Phoenix 1 and Phoenix 2 observations are joined with Phoenix 2
precedence, calibrated to the Phoenix 2 score scale, and normalized with the Phoenix 2 chart catalog
and grade-and-plate formula. Observations that cannot be normalized are excluded. After excluding the
selected player, the search tries rating radii 0.2, 0.3, 0.4, and 0.5 seeking at least 20 peers. If
none succeeds, it repeats the radii seeking 10, then repeats seeking five. Every peer within the
narrowest successful radius participates in the ordinary median; peers are not truncated to the
support target or weighted by distance. Below five peers at the maximum radius, the player-balanced
nonlinear population response model is used.

Projected raw scores are converted to Phoenix 2 letter grades, then evaluated with the official
Phoenix 2 grade-and-plate Pumbility formula. The plate distribution combines Phoenix 2 player
history with a held-out-tuned, capped Phoenix 1 prior and population smoothing; Phoenix 2 wins
for an overlapping player/chart observation. Expected Pumbility is the probability-weighted
formula value. Projected gain is calculated separately for every plate outcome against the
active Phoenix 2 top-50 pool, including replacement of the number-50 chart, and then averaged.
Single and Double use their independent mode pool; Overall uses the shared S+D pool.
Phoenix 1 Pumbility totals never enter the current Phoenix 2 top 50.

The population models and frozen per-player inputs are rebuilt once in the daily background run.
Opening or selecting a player on `/recommendations` first renders any cached result, then requests
only that player's scores newer than the last successful player sync. The lightweight worker loads
the frozen model and the selected player's small input shard, recalculates at most 50 published
recommendations per mode, and replaces the page automatically. If the upstream request or worker
fails, the previous cached result remains visible with a warning. This keeps the interactive path
independent of the hundreds of player score endpoints and the population-wide model fitting.
Interactive refreshes have a rolling 60-second deduplication window, a 30-second browser and
server deadline, and a queue concurrency cap of four so independent workers cannot overwhelm the
score API. Worker logs include queue wait, model load, upstream fetch, merge, compute, publish, and
end-to-end timings for percentile monitoring.

The interactive path is guarded by `PLAYER_RECOMMENDATION_REFRESH_ENABLED`, which defaults to
disabled in source and is enabled explicitly in the production project after the all-player parity
check passes. While disabled, schema-3 runs build immutable shadow artifacts but do not replace the
stable schema-2 recommendation pointer. The next daily or protected administrator run after
activation promotes the new pointer. The previous schema-2 generation and at least two schema-3
generations (plus every schema-3 generation younger than 48 hours) remain available. Player state
and cached results are deleted during a promoted daily run when that player is no longer present in
the consented index.

Rollback uses the same `CRON_SECRET` as the protected analysis trigger:

```powershell
curl.exe -X POST "https://<backend>/api/recommendations/rollback?generationKey=<generation>" `
  -H "X-Analysis-Run-Secret: <CRON_SECRET>"
```

The endpoint verifies the target index and every required model/input shard before replacing the
stable pointer. Legacy schema-2 shards are never pruned unless
`PLAYER_RECOMMENDATION_PRUNE_LEGACY=true` is explicitly set for a promoted run.

To replace all Phoenix 1 data from scratch, run the capture, analysis, public publish, and private
seed once. The publish command replaces the stable, unversioned artifact paths only after building
the archive, manifest, and rerates successfully:

```powershell
npm run snapshot:phoenix1:rebuild
npm run analyze:phoenix1
npm run publish:phoenix1 -- "C:\path\to\PIU Phoenix 2 chart rerates & removals.xlsx"
npm run seed:phoenix1
```

For combined Next.js, Python-function, and in-process queue development, use `vercel dev`. A Python-only worker test can set `PIU_ANALYSIS_RAW_DIR` to an existing snapshot directory instead of configuring a live credential.

## Vercel configuration

The project is designed for the existing `pumbility-farmer.vercel.app` project using a standard Vercel deployment. It does not use a Sites workflow.

Configure these server-side variables:

- `PIU_SCORES_API_KEY` — required for live synchronization.
- `BLOB_READ_WRITE_TOKEN` — required; automatically provided after connecting a **private** Vercel Blob store.
- `CRON_SECRET` — required; a sensitive random value of at least 16 characters used for the secured daily cron route.
- `ANALYSIS_BOOTSTRAP_SAMPLES` — optional; defaults to 500.
- `PLAYER_RECOMMENDATION_REFRESH_ENABLED` — optional rollout switch; defaults to false in source and is set to true for the validated production rollout.
- `PLAYER_RECOMMENDATION_PRUNE_LEGACY` — optional destructive cleanup switch; defaults to false.
- `VERCEL_DEPLOY_WEBHOOK_SECRET` — optional compatibility secret only if an old project-scoped deployment webhook still targets `/api/deploy`.

The only daily cron in `vercel.json` refreshes Phoenix 2 and rebuilds the global recommendation model at `06:00 UTC`. The protected administrator trigger starts the same global workflow. Deployments do not start analysis. The five-minute failed-retry rule remains in place, while the one-hour successful-run cooldown is temporarily disabled. The global worker has an 800-second function backstop.

### Cost controls

The global fit runs only once per day. Interactive work runs only when the rollout switch is enabled,
is deduplicated for 60 seconds per player, stops browser/server waiting after 30 seconds, and is capped
at four concurrent queue consumers. Failed player tasks return a cached-safe failure instead of
requesting queue redelivery. Artifacts use compressed numeric surfaces and bounded generation
retention to limit Blob storage and transfer.

Before enabling the interactive switch, review the project under **Dashboard → Usage** and configure
**Team Settings → Billing → Spend Management**. For a strict $20-plan budget, set a small on-demand
spend threshold (for example, $1) with notifications; enable automatic production pausing only if a
hard $21 total ceiling is preferable to availability. Disable the switch to return to daily-only
global work. Deployment, environment activation, manual analysis triggers, and rollback are operator
actions and are never performed by the application automatically.

Old project-scoped Vercel account webhooks may still target `/api/deploy?mix=phoenix2`. The endpoint validates Vercel's HMAC-SHA1 `x-vercel-signature` and returns `202 ignored`; it never queues a model run. Phoenix 1 deployment events are rejected as archived.

Set the linked Vercel project's Framework Preset to **Services** and its Default Max Duration to **800 seconds**. The backend's generated Celery subscriber inherits that project default; `vercel.json` also applies 800 seconds explicitly to source-backed Python functions.

Never expose server-side secrets through a `NEXT_PUBLIC_` variable. The private snapshot allowlists
only player IDs, the consented Phoenix 2 username, analysis-required chart/score fields, and
per-player sync timestamps. It never stores game tags, API credentials, or other profile fields,
and no raw snapshot route exists. Recommendation responses use an opaque player key and never
return raw player IDs or complete score histories.

## Synchronization behavior

The daily global worker fetches the consented `/api/v2/players` list and the complete Phoenix 2 catalog. Six score workers share a 125 ms request-start limiter and any `Retry-After` backoff. Known players use `recordedAfter`, new players receive a full fetch, and previously empty players are rechecked only after 24 hours. Revoked players are removed immediately. It then fits and serializes the population models once; it no longer computes every player's full recommendation candidate list.

Interactive player work is sent to the separate `player-recommendations` queue. Each job calls only
`/api/v2/players/<id>/scores`, supplies `recordedAfter` when prior state exists, merges best scores,
and evaluates that player against the current frozen model. The API returns a result immediately
when the same player and model were refreshed less than 60 seconds ago, so repeated opens and
browser refreshes do not create duplicate upstream work. Upstream `Retry-After` delays are honored;
those explicit rate-limit waits are outside the healthy-path latency target.

Valid rows are merged deterministically by player/chart within the selected version, retaining the best Pumbility/score. Players with no rows for that version are excluded, and only players with at least 30 valid Singles or 30 valid Doubles scores are passed to the analyzer. Calibration and shrinkage are recalculated independently for each version and mode. No `minLevel` score filter is used.

## Verification

```bash
python -m unittest discover -s tests -v
npm run test:frontend
npm run verify:phoenix1-archive
npm run typecheck
npm run build
```

The suite includes incremental merge/pruning/recheck/checkpoint tests, recommendation catalog and
Phoenix 2 precedence tests, serialized model round-trip and player-only refresh tests, shared
rate-limit tests, queue-state and cron tests, eager Celery
execution, optimized/full payload equivalence, JSON fallback handling, and a mocked 809-player
bounded-concurrency benchmark.

`tests/fixtures/production-chart-aggregates-20260807.json` contains all 1,294 chart-level aggregates captured from the public production API for the within-level methodology regression. Its schema is allowlisted and contains no player identifiers, raw scores, usernames, game tags, or credentials. Refresh it explicitly with:

```bash
python scripts/capture_public_analysis_fixture.py
```

For an authorized in-memory end-to-end check against the private current snapshot, run `scripts/validate_private_snapshot.py` through `vercel env run -e production`. The validator prints aggregate counts only and never persists raw rows.

## Evidence labels

- **Published:** at least 10 contributing players.
- **Provisional:** 5–9 contributing players.
- **Insufficient:** 1–4 contributing players.
- **Unrated:** the chart was not selected for any eligible player under either
  the deduplicated two-window rule or its top-100 fallback.

The local sample snapshots contain one player. They are useful for functional testing, but their measured charts remain correctly labeled **Insufficient**.
