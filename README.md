# Pumbility Farmer

Pumbility Farmer is a PIU Phoenix 2 scoring-difficulty analyzer and Vercel web UI. It produces completely independent Singles and Doubles rankings for every official chart at level 20 or above. Player baselines and top-100 ranks use each eligible player's complete mode history, including levels below 20.

## Analysis method

Each mode is processed separately:

1. Deduplicate to a player's best score per chart.
2. Rank that player's valid scores within Singles or Doubles by Pumbility.
3. Require at least 30 scores in that mode.
4. Use the mean of ranks 11–30 as the player's mode-specific skill baseline.
5. Retain only ranks 1–100 from that player and mode for chart analysis.
6. Calculate a signed residual between each retained chart and the player's baseline.
7. Within each mode, compare a chart only with measured charts at the exact same official level; the median chart residual is that folder's reference.
8. Calibrate residual Pumbility into continuous level units independently for Singles and Doubles.
9. Anchor the typical official level `L` chart at `L + 0.5` and shrink low-evidence estimates toward that reference.

The displayed difference is:

```text
estimated scoring difficulty - (official level + 0.5)
```

A negative value is easier to score than the typical chart in the same mode and official level. Continuous estimates are not hard-clamped to the official folder, but the `L + 0.5` center and evidence shrinkage mean that an estimate below `L` requires an unusually strong within-folder signal.

The analyzer does not use the chart catalog's existing `scoringLevel` or an existing tier list.

## Relative scoring groups

Groups are midpoint-percentile deciles within the exact mode and official level. The easiest measured S23 charts are placed in group 1 relative to other S23s, and the hardest measured S23 charts are placed in group 10. Small folders use the same midpoint rule, which avoids labeling either of only two measured charts as an extreme.

| Within-level percentile | Group |
| ---: | --- |
| `0–10%` | Extremely Easy |
| `10–20%` | Very Easy |
| `20–30%` | Clearly Easy |
| `30–40%` | Moderately Easy |
| `40–50%` | Slightly Easy |
| `50–60%` | Typical |
| `60–70%` | Slightly Hard |
| `70–80%` | Moderately Hard |
| `80–90%` | Very Hard |
| `90–100%` | Extremely Hard |

The percentile tier and the numerical difficulty answer different questions: the tier shows placement among charts in the folder, while the numerical difference preserves the estimated effect size in level units.

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
- `folders/*.csv`: one export for every level-20+ folder found in the catalog.

## Web application

Install frontend dependencies and build:

```bash
npm install
npm run typecheck
npm run build
```

The frontend is a Next.js application. The Python function at `/api/analyze` supports:

- `GET /api/analyze`: load the latest successful `AnalysisPayload` from private Vercel Blob.
- `GET /api/analyze?jobId=...`: load a 24-hour queue-job status from Vercel Runtime Cache.
- `POST /api/analyze`: return immediately with a queued/existing job. The successful-run cooldown is temporarily disabled.
- `POST /api/deploy`: accept signed Vercel `deployment.promoted` events and enqueue one full synchronization per production deployment.

The response contracts are:

```text
200 { outcome: "fresh", generatedAtUtc, nextAllowedAtUtc }
202 { outcome: "started" | "existing", job }
```

The `fresh` response shape remains compatible for when the cooldown is restored, but it is not emitted while the successful-run freshness window is zero.

The browser polls active jobs every two seconds (ten seconds in a hidden tab), displays the synchronization stage and player progress, then reloads the latest rankings after completion. All responses are read as text before conditional JSON parsing so a platform-generated timeout page is shown as a useful message instead of a JSON syntax error.

The FastAPI publisher and Celery subscriber declared in `pyproject.toml` run as a private-data backend in Vercel Services; the Next.js UI remains the frontend service. The subscriber uses Vercel Queues through the `vercel://` broker. Vercel Runtime Cache stores job status, and the worker stores only private JSON objects in Vercel Blob:

- `analysis/latest.json` — current aggregate returned through the API.
- `analysis/private/phoenix2-current.json` — private, privacy-minimized incremental snapshot.
- `analysis/staging/<job>.json` — resumable 50-player checkpoints, deleted after success or after 24 hours.
- `analysis/runs/*.json` — the latest ten immutable aggregate runs.

For combined Next.js, Python-function, and in-process queue development, use `vercel dev`. A Python-only worker test can set `PIU_ANALYSIS_RAW_DIR` to an existing snapshot directory instead of configuring a live credential.

## Vercel configuration

The project is designed for the existing `pumbility-farmer.vercel.app` project using a standard Vercel deployment. It does not use a Sites workflow.

Configure these server-side variables:

- `PIU_SCORES_API_KEY` — required for live synchronization.
- `BLOB_READ_WRITE_TOKEN` — required; automatically provided after connecting a **private** Vercel Blob store.
- `CRON_SECRET` — required; a sensitive random value of at least 16 characters used for the secured daily cron route.
- `ANALYSIS_BOOTSTRAP_SAMPLES` — optional; defaults to 500.
- `VERCEL_DEPLOY_WEBHOOK_SECRET` — required in Production; generated when creating the project-scoped deployment webhook.

The daily cron is defined in `vercel.json` at `06:00 UTC`. Manual and scheduled requests retain global-active-job deduplication and the five-minute failed-retry rule, but the one-hour successful-run cooldown is temporarily disabled. The worker has an 800-second function backstop.

A project-scoped Vercel account webhook listens for `deployment.promoted` and targets `/api/deploy`. The endpoint validates Vercel's HMAC-SHA1 `x-vercel-signature`, derives a deterministic job ID from the deployment ID, and requests a full player synchronization rather than an incremental one. If another refresh is active, it returns a non-2xx response so Vercel retries the promotion event with backoff.

Set the linked Vercel project's Framework Preset to **Services** and its Default Max Duration to **800 seconds**. The backend's generated Celery subscriber inherits that project default; `vercel.json` also applies 800 seconds explicitly to source-backed Python functions.

Never expose server-side secrets through a `NEXT_PUBLIC_` variable. The private snapshot allowlists only player IDs, analysis-required chart/score fields, and per-player sync timestamps. It never stores usernames, game tags, API credentials, or other profile fields, and no raw snapshot route exists.

## Synchronization behavior

Every worker run fetches the consented `/api/v2/players` list and the complete `mix=Phoenix2` chart catalog. Six score workers share a 125 ms request-start limiter and any `Retry-After` backoff. Known players use `recordedAfter`, new players receive a full fetch, and previously empty players are rechecked only after 24 hours. Revoked players are removed immediately.

Valid rows are merged deterministically by player/chart, retaining the best Pumbility/score. Players with no Phoenix 2 rows are excluded, and only players with at least 30 valid Singles or 30 valid Doubles scores are passed to the analyzer. No `minLevel` score filter is used.

## Verification

```bash
python -m unittest discover -s tests -v
npm run test:frontend
npm run typecheck
npm run build
```

The suite includes incremental merge/pruning/recheck/checkpoint tests, shared rate-limit tests, queue-state and cron tests, eager Celery execution, optimized/full payload equivalence, JSON fallback handling, and a mocked 809-player bounded-concurrency benchmark.

`tests/fixtures/production-chart-aggregates-20260807.json` contains all 1,294 chart-level aggregates captured from the public production API for the within-level methodology regression. Its schema is allowlisted and contains no player identifiers, raw scores, usernames, game tags, or credentials. Refresh it explicitly with:

```bash
python scripts/capture_public_analysis_fixture.py
```

For an authorized in-memory end-to-end check against the private current snapshot, run `scripts/validate_private_snapshot.py` through `vercel env run -e production`. The validator prints aggregate counts only and never persists raw rows.

## Evidence labels

- **Published:** at least 10 contributing players.
- **Provisional:** 5–9 contributing players.
- **Insufficient:** 1–4 contributing players.
- **Unrated:** the chart was not present in any eligible player's top 100 for that mode.

The local sample snapshots contain one player. They are useful for functional testing, but their measured charts remain correctly labeled **Insufficient**.
