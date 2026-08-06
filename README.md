# PIU Phoenix 2 Misgrade Analyzer

This exploratory analyzer implements a custom chart-ranking method without using PIU Scores' existing tier lists or `scoringLevel` field.

## Requested metric

For each eligible player:

1. Sort all valid Phoenix 2 best scores by the API-provided per-score Pumbility value.
2. Take the mean of ranks 11 through 30 as the player's baseline.
3. For each of ranks 1 through 10, compute `(score Pumbility - baseline)^2`.
4. Average those squared residuals by chart across players.
5. Within each official folder, rank the largest metric as easiest and split charts into ten equal-count bands: `.0` easiest through `.9` hardest.

Target folders: S20, S21, S22, S23, D20, D21, D22, D23, and D24.

## Live run

Use a newly rotated tool key and keep it only in an environment variable:

```bash
export PIU_SCORES_API_KEY='piu_scores_live_...'
python piu_misgrade_analyzer.py live --output-dir ./piu_live_run
```

A PIU Scores tool key can read only players who have explicitly shared their score data with that tool. A zero-player result means the sharing cohort is empty, not that the API has no users.

If the run finds shared players but reports zero best-score rows, none of those accounts currently exposes Phoenix 2 scores to that credential. Import Phoenix 2 scores into at least one PIU Scores account, then share that account's score data with the exact community tool whose key is in `PIU_SCORES_API_KEY`. For a one-account analysis, that account's personal API token can be used instead. The method requires at least 30 valid best scores per player before that player can contribute to the result.

The script writes no API credential to disk or logs. The raw cache intentionally omits player names and game tags.

## Offline validation

```bash
python piu_misgrade_analyzer.py synthetic --output-dir ./synthetic_demo
```

The included synthetic fixture has a known easiest-to-hardest order. The analyzer must recover `.0` through `.9` in all nine target folders or exit with an error.

## Main outputs

- `chart_tiers.csv`: aggregate chart metrics, requested raw tier, reliability companion tier, contributor counts, bootstrap interval, and evidence status.
- `folders/*.csv`: one file per target folder.
- `analysis_summary.json`: cohort and coverage diagnostics.
- `player_baselines_pseudonymous.csv`: player baseline diagnostics using hashes rather than names or raw IDs.
- `raw/`: live API snapshot for reproducible reruns; no credential is stored.

## Interpretation

The primary `misgradeRawPb2` field is exactly the requested mean squared residual. `misgradeRmsPb` is its square root, shown only to make the magnitude interpretable in Pumbility points. The `misgradeShrunkPb2` and `reliabilityTier` fields are companion diagnostics that reduce small-sample instability; they do not replace the requested raw result.

Recommended evidence labels:

- Published: at least 10 top-10 contributors.
- Provisional: 5-9 contributors.
- Insufficient: 1-4 contributors.
- Unrated: no player had that chart in their top 10.

## Security note

The key originally pasted into chat should be rotated before a real run. Never put a tool key in client-side JavaScript, a public repository, a command-line argument, or a report.
