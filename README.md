# Pumbility Farmer

Pumbility Farmer is a PIU Phoenix 2 scoring-difficulty analyzer and Vercel web UI. It produces completely independent Singles and Doubles rankings for every official chart at level 20 or above.

## Analysis method

Each mode is processed separately:

1. Deduplicate to a player's best score per chart.
2. Rank that player's valid scores within Singles or Doubles by Pumbility.
3. Require at least 30 scores in that mode.
4. Use the mean of ranks 11–30 as the player's mode-specific skill baseline.
5. Retain only ranks 1–100 from that player and mode for chart analysis.
6. Calculate a signed residual between each retained chart and the player's baseline.
7. Calibrate residual Pumbility into continuous level units independently for Singles and Doubles.
8. Anchor the average official level `L` at `L + 0.5` and shrink low-evidence estimates toward that average.

The displayed difference is:

```text
estimated scoring difficulty - (official level + 0.5)
```

A negative value is easier to score than average. For example, a D24 estimated at D21.2 has a difference of `21.2 - 24.5 = -3.3`. Continuous estimates are not confined to the official folder, so an S20 can be estimated below S20.

The analyzer does not use the chart catalog's existing `scoringLevel` or an existing tier list.

## Relative scoring groups

| Difference | Group |
| ---: | --- |
| `≤ -3.00` | Extremely Easy |
| `-3.00 to -2.00` | Very Easy |
| `-2.00 to -1.25` | Clearly Easy |
| `-1.25 to -0.75` | Moderately Easy |
| `-0.75 to -0.25` | Slightly Easy |
| `-0.25 to +0.25` | Typical |
| `+0.25 to +0.75` | Slightly Hard |
| `+0.75 to +1.25` | Moderately Hard |
| `+1.25 to +2.00` | Very Hard |
| `≥ +2.00` | Extremely Hard |

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

- `GET`: load the latest successful result from Vercel Blob.
- `POST`: pull a fresh PIU Scores snapshot, run both independent analyses, store the result, and return it.

For combined Next.js and Python-function development, use `vercel dev`. A Python-only local test can set `PIU_ANALYSIS_RAW_DIR` to an existing snapshot directory instead of configuring a live credential.

## Vercel configuration

The project is designed for the existing `pumbility-farmer.vercel.app` project using a standard Vercel deployment. It does not use a Sites workflow.

Configure these server-side variables:

- `PIU_SCORES_API_KEY` — required for fresh live analysis.
- `BLOB_READ_WRITE_TOKEN` — recommended; automatically provided after connecting a Vercel Blob store.
- `ANALYSIS_RUN_SECRET` — optional password protecting the run button.
- `ANALYSIS_COOLDOWN_SECONDS` — optional; defaults to 300 seconds.
- `ANALYSIS_BOOTSTRAP_SAMPLES` — optional; defaults to 500.

Never expose the PIU Scores credential through a `NEXT_PUBLIC_` variable. The API cache and aggregate outputs intentionally omit raw player IDs and names.

## Evidence labels

- **Published:** at least 10 contributing players.
- **Provisional:** 5–9 contributing players.
- **Insufficient:** 1–4 contributing players.
- **Unrated:** the chart was not present in any eligible player's top 100 for that mode.

The current cached snapshot contains one player. It is useful for functional testing, but its measured charts remain correctly labeled **Insufficient**.
