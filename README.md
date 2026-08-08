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

The displayed difference uses 70% of the calibrated residual conversion to keep
the estimated scoring-difficulty range from becoming overly wide:

```text
difficulty difference = -0.7 × shrunk Pumbility residual / Pumbility per level
estimated scoring difficulty = official level + 0.5 + difficulty difference
```

A negative value is easier to score than the typical chart in the same mode and official level. Continuous estimates are not hard-clamped to the official folder, but the `L + 0.5` center and evidence shrinkage mean that an estimate below `L` requires an unusually strong within-folder signal.

The analyzer does not use the chart catalog's existing `scoringLevel` or an existing tier list.

## Magnitude bands and relative ranks

The primary scoring tiers use a symmetric quarter-level ladder. The extreme
bands begin at `±1.00`, and the remaining boundaries advance in `0.25` level
increments around the Typical band.

The bands are not filled by quota: a folder may contain several charts in a band
or none when its measured charts genuinely have similar scoring difficulty.

| Difficulty difference | Effect band |
| ---: | --- |
| `≤ −1.00` | Extremely Easy |
| `−1.00 to −0.75` | Very Easy |
| `−0.75 to −0.50` | Easy |
| `−0.50 to −0.25` | Slightly Easy |
| `−0.25 to +0.25` | Typical |
| `+0.25 to +0.50` | Slightly Hard |
| `+0.50 to +0.75` | Hard |
| `+0.75 to +1.00` | Very Hard |
| `≥ +1.00` | Extremely Hard |

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
- `POST /api/analyze?mix=phoenix2`: queue or follow a Phoenix 2 refresh.
- `POST /api/deploy?mix=phoenix2`: accept a signed deployment event for Phoenix 2.
- `GET /api/recommendations/players`: return consented usernames and mode eligibility without raw IDs; successful lists are cached for five minutes with stale revalidation.
- `GET /api/recommendations?playerKey=...`: return one precomputed player recommendation slice.
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
- `analysis/recommendations/latest.json` — private precomputed player recommendation index.
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

Player skill-rating history is selected independently for Singles and Doubles. A mode uses
Phoenix 2 once it has 50 valid, deduplicated Phoenix 2 chart scores; below that threshold it uses
Phoenix 1 history when available. Played status, existing Pumbility, current top-50 totals, and
projected gain always use Phoenix 2. A player with no Phoenix 2 history can still receive a
Phoenix 1-derived rating and a population score prediction; their Phoenix 2 top 50 starts empty.

Projected raw scores come from a player-balanced population response model of scoring rating and
continuous chart difficulty. The response is nonlinear, so the raw-score cost of another 0.1
difficulty can change with both the player's rating and the chart's absolute difficulty. The
prediction does not use the selected player's raw-score average as a personal baseline. Phoenix 1
and Phoenix 2 observations are matched to the Phoenix 2 catalog and combined with Phoenix 2
precedence; source calibration keeps the prediction on the Phoenix 2 score scale.

Projected raw scores are converted to Phoenix 2 letter grades, then evaluated with the official
Phoenix 2 grade-and-plate Pumbility formula. The plate distribution combines Phoenix 2 player
history with a held-out-tuned, capped Phoenix 1 prior and population smoothing; Phoenix 2 wins
for an overlapping player/chart observation. Expected Pumbility is the probability-weighted
formula value. Projected gain is calculated separately for every plate outcome against the
player's actual Phoenix 2 top 50, including replacement of the number-50 chart, and then averaged.
Phoenix 1 Pumbility totals never enter the current Phoenix 2 top 50.

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
- `VERCEL_DEPLOY_WEBHOOK_SECRET` — required in Production; generated when creating the project-scoped deployment webhook.

The only daily cron in `vercel.json` refreshes Phoenix 2 at `06:00 UTC`. The five-minute failed-retry rule remains in place, while the one-hour successful-run cooldown is temporarily disabled. The worker has an 800-second function backstop. A promoted deployment reanalyzes the stored private Phoenix 2 snapshot and republishes derived aggregates and recommendation shards without calling the upstream score API; scheduled and manual refreshes retain their normal synchronization behavior.

Project-scoped Vercel account webhooks may target `/api/deploy?mix=phoenix2`. The endpoint validates Vercel's HMAC-SHA1 `x-vercel-signature`, derives a deterministic job ID from the deployment ID, and requests cached-snapshot model reanalysis without synchronizing players. Phoenix 1 deployment events are rejected as archived.

Set the linked Vercel project's Framework Preset to **Services** and its Default Max Duration to **800 seconds**. The backend's generated Celery subscriber inherits that project default; `vercel.json` also applies 800 seconds explicitly to source-backed Python functions.

Never expose server-side secrets through a `NEXT_PUBLIC_` variable. The private snapshot allowlists
only player IDs, the consented Phoenix 2 username, analysis-required chart/score fields, and
per-player sync timestamps. It never stores game tags, API credentials, or other profile fields,
and no raw snapshot route exists. Recommendation responses use an opaque player key and never
return raw player IDs or complete score histories.

## Synchronization behavior

Every worker run fetches the consented `/api/v2/players` list and the complete Phoenix 2 catalog. Six score workers share a 125 ms request-start limiter and any `Retry-After` backoff. Known players use `recordedAfter`, new players receive a full fetch, and previously empty players are rechecked only after 24 hours. Revoked players are removed immediately.

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
Phoenix 2 precedence tests, shared rate-limit tests, queue-state and cron tests, eager Celery
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
