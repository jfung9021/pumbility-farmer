# Scoring difficulty methodology research

Saved October 17, 2026. Research is deferred; the local tier list still uses the
implemented player-adjusted official Scoring model. The proposed fixed-rank
replacement has not been implemented.

## Purpose

Estimate how difficult a chart is to score on while separating chart difficulty
from the strength of the people who played it. Keep official Phoenix 1 and Phoenix 2
leaderboards as the score sources, retain each deterministically matched
player/chart's higher raw score, and fit Singles and Doubles separately using
current official levels 20 and above.

## Existing model

`official_player_scoring.py::fit_cross_level_player_comparisons` fits shared-player
chart effects across levels. Players need at least four observed eligible charts.
Each player's target-chart comparisons receive total weight one. The model uses
an additive raw-score relationship between player ability and chart cost.

`official_cross_level_scoring.py` calibrates median eligible reference-chart costs
to official level plus 0.5, with exponential interpolation and provisional tangent
tails. Charts need at least five contributing players and membership in the main
comparison component for a numeric estimate. Reference charts need at least ten
contributors and pass the source-aware comparability checks; a folder needs five
reference charts. The conditional bootstrap holds fitted effects and anchors fixed.

This is a defensible baseline, rather than a demonstrated optimum. Its intervals
exclude effort, selection, identity, chart-change, and source-truncation uncertainty.

## Fixed rank alternative

The alternative discussed uses the descending merged player-score list:

| Available merged player scores | Chart reference score |
|---:|---|
| At least 50 | Twentieth-highest entry |
| 5 through 49 | Arithmetic mean of the top five entries |
| 1 through 4 | Arithmetic mean of all entries |
| None | Unrated |

Repeated equal scores from different players count separately. Cross-mix duplicates
are removed before counting. The twentieth entry is positional, rather than the
upstream competition-rank label. Fractional means retain full precision.

The proposed cost is `1,000,000 - referenceScore`. Reference medians would use
comparable boards with at least 50 players so every reference uses the same
twentieth-score statistic. Smaller boards would receive provisional estimates.

On the pinned official captures, the selection branches are:

| Mode | Twentieth score | Top five mean | Mean of 1 to 4 | No scores |
|---|---:|---:|---:|---:|
| Singles | 522 | 0 | 0 | 32 |
| Doubles | 778 | 12 | 2 | 43 |

### Perfect score ceiling

The twentieth score is 1,000,000 on 170 Singles and 207 Doubles charts:

| Current official level | Singles charts | Doubles charts |
|---:|---:|---:|
| 20 | 99 | 98 |
| 21 | 57 | 64 |
| 22 | 14 | 34 |
| 23 | 0 | 11 |
| 24 and above | 0 | 0 |

Their costs are all zero. Since both proposed level-20 median costs are zero,
these charts would all map to 20.5, including charts officially rated 21 through 23.
The statistic cannot distinguish them. Crossing from 49 to 50 observations also
switches the statistic abruptly and can increase estimated difficulty even when
the new observation is a low score.

## Recommended research direction

Retain player adjustment as the baseline. Different charts attract different
players, so even a complete clearer count would not make a raw score percentile or
rank independent of player strength. Shared-player comparisons address this
population-composition problem under model assumptions.

Test a model that respects the bounded score range and explicitly handles perfect
scores. Define the goal precisely, for example the player ability associated with
achieving a recorded best score of at least 995,000. Compare multiple targets where
data support them; scoring difficulty near 980,000 may order charts differently
from scoring difficulty near 999,000. A target is a proposed modeling choice, not
an adopted product requirement.

Compare the current additive model, a bounded-response candidate, and raw leaderboard
statistics using held-out observed player/chart scores. Keep target observations
out of validation-time player estimates, and evaluate by mode, official folder,
score range, player strength, and ceiling concentration. Examine ranking stability
when individual players or source captures are removed. Held-out observed scores
do not establish accuracy on missing attempts or unobserved players.

Official records are observed best scores, with differing source dates, practice
effort, self-selection, and leaderboard cutoffs. Absence is unknown rather than a
failure or a zero. If every informative player perfects two charts, the available
data may provide only bounds or insufficient separation. Additional comparable
nonperfect observations or controlled play data would be needed for stronger claims.

## Sources and local evidence

- [Rasch measurement framework](https://www.rasch.org/memo22.htm) motivates separating
  person ability and item difficulty under explicit model assumptions.
- [Continuous item response model using a censored normal distribution](https://link.springer.com/article/10.1007/s41237-026-00292-x)
  is a candidate model family for bounded responses with ceiling effects, rather
  than validation of a PIU implementation.
- [Missing not at random in matrix completion](https://proceedings.neurips.cc/paper_files/paper/2019/hash/d828725179d622a56f951e527a966ed7-Abstract.html)
  explains why selective observation can bias estimates.
- The pinned official captures have source dates September 6, 2026 for Phoenix 1
  and October 11, 2026 for Phoenix 2. The private manifest is
  `.local-data/piu-scores/official/scoring/manifest.json`.
- Current implementation and verification are recorded in
  `docs/official-cross-level-scoring-implementation-plan.md` and
  `output/official-cross-level-implementation-2026-10-17/verification.json`.

## Deferred work

No fixed-rank implementation, new scoring fit, model replacement, or production
change is part of saving this research. Revisit target definition, ceiling-aware
likelihood, validation, and evidence labels before choosing a replacement.
