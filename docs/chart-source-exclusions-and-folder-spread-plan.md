# Chart source exclusions and final folder spread

Implementation entry point: `npm run analyze:source-aware` uses the existing
cached snapshots and `config/chart-source-exclusions.json`. It reuses target
cohorts whose actual merged/source-board identities match and writes a separate
schema-56 generation at
`.local-data/piu-scores/combined/experiments/source-aware-width/web_results.json`.
Legacy generation builders remain available for historical replay. The local
selector is `PIU_LOCAL_SCORING_VARIANT_EXPERIMENT=source-aware-width`.

Research and implementation plan, 2026-10-18. No application code, active data,
environment selector, raw capture, or fitted model was changed by this research.

## Recommendation

Add one versioned chart/source exclusion registry for tier analysis, starting
with Phoenix 1 Mopemope D26. Apply it before source maxima, clear membership,
player histories, qualification, model fitting, and calibration.

Keep the current scoring/clearing estimators and the single 50% midpoint
adjustment. Add a final, median-preserving width floor that expands only a
folder side that is too narrow. Its initial research setting is a final
one-sided 90th-percentile distance of 0.40 from the midpoint. This is a choice
of rating units, not a newly inferred probability of success. Preserve
score-derived ordering, uneven gaps, and genuine cross-level outliers.

Apply the width policy to official SSS+ Scoring at S/D20+ and official D26+
Clearing. General/historical SSS keep their current spread policies. The source
exclusion applies to every tier-analysis scoring variant and clearing history,
including submitted histories, independently of which spread policy is used.
Below-20 algorithms retain their definitions; corrected input histories can
still change their outputs. Personal ratings and recommendations are outside
this tier-analysis change.

## Evidence and exact scope of the previews

The active schema-55 file was read from
`.local-data/piu-scores/combined/experiments/sssplus-spread-p1-observed-clearing/web_results.json`.
Official source dates are Phoenix 1 2026-09-06 and Phoenix 2 2026-10-11.
Raw manifest SHA-256:
`5390d82e8ca681938c5facc88028564afc30243da6a198c94174b94e173ded82`.

All cached official 20+ folders in both modes were summarized. Detailed stage
traces cover D26, D27 and D28. A counterfactual rebuilt official Clearing with
Phoenix 1 Mopemope observations removed from both targets and D23+ histories,
including 1,000 conditional clearer bootstraps. No data was fetched and no
Scoring model was refitted. The active payload hash was checked unchanged.

| Folder | Rated charts | Current SSS+ Scoring range | Scoring median | Current Clearing range | Clearing median |
|---|---:|---:|---:|---:|---:|
| D26 | 36 | 26.026–26.840 | 26.500 | 26.187–26.799 | 26.500 |
| D27 | 10 | 27.134–28.193 | 27.500 | 27.236–27.577 | 27.500 |
| D28 | 4 | 28.069–28.838 | 28.273 | 28.427–28.560 | 28.500 |

D27 SSS+ already has an upper outlier: Legendary Dominion is 28.193, based
on three observed scores. 1948 is 27.972. Its upper tail should remain;
the tight D27 upper range is primarily a Clearing issue. SSS+ observations
are highly asymmetric: D26 has 26 zero-target, nine rare-target and one mixed
chart; every rated D27/D28 chart has zero SSS+ observations. A normal-shaped
histogram cannot be assumed to follow from those mixed evidence branches.

### Mopemope is currently combining different chart versions

The exclusion is justified by the user's chart-version report. Cached note
counts cannot independently establish the change: Phoenix 1 has 1,200 notes,
while the Phoenix 2 catalog note count is missing. A manual registry is needed
even if automatic note-count diagnostics remain available.

Chart UUID: `22ee894e-8601-4b55-9d04-c31187f8fa22`, mode Double, current level 26.

| Mopemope D26 evidence | Phoenix 1 | Phoenix 2 |
|---|---:|---:|
| Returned official scores | 100 | 28 |
| SSS+ scores (995,000+) | 3 | 1 |
| Mean of best three scores | 998,670 | 993,143 |
| Highest score | 999,005 | 998,186 |

Removing Phoenix 1 before the maximum merge reduces 108 merged players to
28. Eighty Phoenix-1-only observations disappear; 17 shared players now retain
their lower Phoenix 2 score. Do not remove only Phoenix-1-winning rows after
the merge: their valid Phoenix 2 alternatives must survive.

The official Clearing-only counterfactual changes Mopemope from 26.4125 to
26.5197, with 28 qualified clearers and a q20 ability of 26.02 instead of
25.844. Fifteen D26+ chart q20 thresholds change through corrected histories;
all 50 rated Clearing points change through histories and folder calibration.
The source rule therefore cannot be an isolated displayed-rating patch.

The submitted cache also contains 13 Phoenix 1 and two Phoenix 2 Mopemope
clearers, including 11 Phoenix-1-only clearers. All 11 currently qualify for
submitted Clearing; none loses the 200-record qualification, but eight player
skills change, by up to 0.0333. Apply the same exclusion before submitted
history construction rather than leaving this indirect source dependency.

### Why the ranges narrow

1. **Clearing calibrates width before compression.** Its robust 90th-percentile
   absolute raw offset targets 0.45; the later 50% adjustment halves that
   intended width to about 0.225. This is a policy interaction, not a rounding
   problem. D26's raw q20 abilities span 1.08, but the displayed estimates span
   only about 0.612.
2. **Clearing's sides have different widths.** D27's current lower-side q90
   distance is 0.241, while its upper side is only 0.070. One symmetric
   multiplier cannot set sensible distances on both sides. Per-chart evidence
   shrinkage also pulls sparse hard charts toward the median: Legendary
   Dominion has q20 ability 27.116, but two eligible clearers yield weight
   0.0606 and a Clearing estimate near 27.55. Keep this evidence definition
   for this change; do not interpret wider displayed units as stronger evidence.
3. **D28 Clearing borrows an unsuitable width.** Eligible clearer counts are
   15, 6, 9 and 7. Only one chart meets the current ten-player reference cutoff,
   so D28 borrows D27's scale. Its own q20 span is 0.416, yet its final span is
   0.133.
4. **SSS+ has sequential inward maps.** D26's range before midpoint compression
   is 25.429–27.224, after compression 25.964–26.862, and after `asinh`
   26.026–26.840. The compression is the larger contributor there. The
   outer tail guard is inactive for these D26 points.
5. **D28's Scoring location is borrowed.** Four finite charts do not meet the
   five-chart folder-centering threshold. Its final median is 28.273 rather
   than 28.5. Correcting width alone would retain the wrong location.

Simply doubling all final offsets fails: D20's current SSS+ range of
18.5–21.561 would become 16.5–22.623, while D27/D28 Clearing would still have
tight upper tails. Replacing ratings with normal quantiles, allocating a
fixed number of charts per tier, and snapping endpoints would discard numeric
evidence or create ties. None is recommended.

## Implementation

### 1. Versioned source exclusion registry

Add `config/chart-source-exclusions.json` and a shared pure loader/matcher,
with this first rule:

```json
{
  "version": 1,
  "rules": [{
    "source": "Phoenix1",
    "chartId": "22ee894e-8601-4b55-9d04-c31187f8fa22",
    "mode": "Double",
    "label": "Mopemope D26",
    "scope": "tier-analysis-performance",
    "enabled": true,
    "reason": "Chart changed between Phoenix 1 and Phoenix 2; use Phoenix 2 performance records."
  }]
}
```

Match the stable chart UUID and source, with mode validated against the current
catalog. Labels and current level are descriptive, not matching keys. Normalize
`Phoenix`/`phoenix1` to the same source. Reject malformed, duplicate, contradictory
or unknown enabled chart rules. Equal note counts must not override an explicit
exclusion. A missing note count must not automatically exclude unrelated charts.

Use the matcher at the official observation merge and submitted tier-analysis
collection boundaries. Preserve the established identity matching, Phoenix 2
records and score precedence for all non-excluded observations. An excluded
score must not enter model observations, source-removal inputs, top-three or
last-score calculations, clear membership, 25/30-chart histories, 200-record
qualification or calibration. Identity linking can retain raw tag metadata;
excluded performance must not become an outcome or a clear.

Preserve raw capture hashes, dates, row counts and cap flags. Store separate
effective/excluded counts, rule IDs and reasons. For Mopemope show “Phoenix 1:
100 captured, 100 excluded; Phoenix 2: 28 used.” Phoenix 1's cap remains a raw
diagnostic; an excluded source cannot activate an effective cap fallback. The
Phoenix 2 cap policy stays unchanged. Without usable Phoenix 2 observations,
an excluded historical board cannot silently restore a rating.

Add both a complete config provenance hash and a semantic effective-policy
hash. Effective analysis identity includes the raw manifest/catalog/snapshot
hashes and applicable semantic source rules. Changes to rule labels alone
need not refit numeric models. Rebuild/validate effective metadata and source
boards consistently rather than changing the raw manifest to pretend the
capture was smaller.

Likely integration points: `official_score_merge.py`, `scoring_percentile.py`,
`sss_scoring.py`, `cross_level_clearing.py`, all local target/General/Clearing
builders, and their input-identity checks.

### 2. Correct sparse-folder location separately from curve anchors

Allow a provisional location correction with at least four finite chart
estimates and nonzero spread. D28's actual finite Scoring median then maps to
28.5. Retain the existing five-chart requirement for independent exponential
curve anchors; a four-chart location correction does not invent an anchor or
new model evidence. With fewer than four, keep explicit borrowing/unavailable
behavior rather than forcing a distribution. Apply the same location rule
consistently to other supported official target folders.

### 3. Final-unit width floor with median-pair protection

Calibrate in the units the user sees, after the existing one-time midpoint
adjustment and current SSS+ inward map, but before the final tail guard.
Recover pre-tail values from the generation's immutable stages so neither
compression nor the tail map runs twice.

For each mode/metric/folder with at least four finite, directly estimated
points and usable distances on both sides:

- Let `m = level + 0.5`, and first translate the finite median to `m`.
- Let `b` be half the gap between the two central values; `b = 0` for an odd
  population or tied middle pair. Keep the whole central interval `[-b,+b]`
  unchanged. This protects the exact median when the two sides use different
  scales; a naive split scale shifted D27 Clearing's median by about 0.074 in
  the initial diagnostic.
- Compute `q-` and `q+`, the linearly interpolated 90th-percentile absolute
  distances on each side, with one vote per finite chart. Cap-default values
  cannot supply observed width. Retain population IDs and evidence composition.
- Initial global width-floor parameter: `t = 0.40`. A side already at or wider
  than `t` keeps its spacing. Avoid compressing approved wide folders such as
  D20. Never use zero/near-zero distances as a denominator.
- On a narrow side, for `z = abs(x-m) > b`, use `k = 0.75` and

  `z_new = max(z, b + k * asinh((z-b)/(q_side-b) * sinh((t-b)/k)))`.

  Then restore the sign around `m`. If `t <= b`, or there is no supported
  positive side spread, do not expand that side. The map is continuous,
  increasing, unbounded, preserves ties/order and the protected median pair,
  and does not reduce an existing distance. It targets a robust width while
  retaining numeric gaps rather than assigning normal-rank positions.

Use provisional local width for four-chart folders, with explicit small-sample
diagnostics. Degenerate/unsupported sides borrow a same-mode, same-metric
supported policy only when coordinate units are comparable; otherwise preserve
the point and report unavailable width. Do not expand measurement noise through
an effectively zero denominator. Keep this policy versioned and reproducible.

Apply the ordinary tail safeguard once afterward, retaining the existing
evidence-based global exception budget. Crossing from D26 into D27 requires
only a +0.5 midpoint offset, well inside the ordinary tail's identity radius;
there is no hard D26.9 boundary. The large two-point exception budget still
limits much more extreme departures. Preserve previously selected strong
exceptions when their corrected source evidence still supports them.

Do not remove the 50% adjustment or change player skill, target thresholds,
zero-target best-three, rare-target posterior or cap rules in this work.

### 4. Rebuild only input-dependent fits, then project once

Source exclusion changes performance inputs, so the old official Double
target fits cannot simply be reused. Recompute Double General, SSS and SSS+
fits, necessary score-gap/reference/source-sensitivity artifacts and associated
bootstrap evidence when their effective inputs differ. Because the source rule
also removes submitted history records, fingerprint submitted Double target
cohorts and profile inputs and refit affected ones as needed. Recompute
submitted Double Clearing histories/qualification and official D26+ Clearing.

Reuse unchanged Singles fits, unaffected cohorts and raw captures. Do not fetch
new data or rebuild recommendation/personal-rating artifacts. A width-only
replay needs no model refit; distinguish that operation from source correction.
Rebuild all dependent medians, curves, rare/boundary estimates, calibrated
points, intervals, ranks and final Pumbility means from corrected inputs.

New spread applies to SSS+ Pumbility through its final Scoring component and to
every Pumbility variant through changed Clearing. Continue full-precision
`(scoring + clearing) / 2`; never recenter or widen Pumbility separately.

### 5. Contract, interface and evidence

Allocate the next available local schema, expected 56. Preserve exact schema-55
recovery, source-filter provenance, corrected pre-spread components, center,
middle-pair half-gap, side references, gains, policy parameters, finite/donor
populations, and final tail/exceptions. Validate source-policy identity across
General, target and Clearing components; reject accidental old-fit reuse.

Reproject available interval endpoints through the same fixed monotone map.
Never create intervals for currently one-sided SSS+ estimates. Keep formal
conditional intervals distinct from sensitivity diagnostics and add width/
location-selection uncertainty to their stated exclusions. Source-excluded
Mopemope Clearing intervals must be recomputed before projection.

Expose excluded-source reasons/counts and provisional local or borrowed width
in chart details. An endpoint occupying D28.0/D28.9 does not establish that its
rating is precise. In the provisional D28 Clearing preview, Neo Catharsis's
existing conditional interval transforms to roughly 27.59–29.14. Upper-side
outer-gap expansion can reach approximately 10x in D27 and 14x in D28; this
reflects their heavily compressed units and is a material calibration
sensitivity, not improved evidence.

Keep this a separate local generation and activate it atomically only after
validation. Keep the current generation available for rollback.

## Analysis-only preview, not promised post-refit ratings

The following uses the proposed `t=0.40`, `k=0.75` width floor with median-pair
protection. Clearing includes the official Mopemope source exclusion. Scoring
uses the frozen current fit and therefore does **not** yet include its source
correction. The actual implementation must regenerate Scoring first.

| Folder | SSS+ preview range | Clearing preview range | Both medians |
|---|---:|---:|---:|
| D26 | 26.026–27.024 | 26.015–27.036 | 26.500 |
| D27 | 27.020–28.193 | 27.078–27.966 | 27.500 |
| D28 | 28.062–29.065 | 28.062–28.938 | 28.500 |

D26 has five Scoring points displayed as D26.0, one as D26.9 and one as D27.0;
Clearing has two displayed D26.0, one D26.9 and one D27.0. The specific examples
are PARADOXX Short Cut Scoring about 27.024, Extreme Music School Scoring about
26.942, Leather Clearing about 26.015 and Conflict Clearing about 27.036.
Those counts are diagnostic results, not allocation quotas.

D27 Clearing now has ERRORCODE: 0 about 27.078 and Appassionata about 27.966.
D28's existing Scoring median correction moves 1949 to about 29.065; it remains
an upper outlier rather than being clipped to 28.9. Clearing places Neo
Catharsis near 28.062 and Dead End near 28.938. Neither metric can justify
precise tail spacing from four charts alone.

## Acceptance and stopping

1. Deterministic exclusion tests cover P1-only removal, shared-player P2
   recovery when lower, unchanged unrelated charts, source aliases, restored
   rules, invalid configurations, no-P2 cases, target/source boards and
   submitted/official histories. Confirm exclusion before qualification and
   before every affected fit/cache identity.
2. Numerical tests cover odd/even medians, central ties, monotonicity, no rank
   inversion, final-unit width, no compression on already-wide sides, zero
   spread, sparse borrowing, cap defaults, exactly one 50% adjustment and one
   tail application, interval ordering, truncation and full-precision means.
3. After corrected fits, report every official S/D20+ Scoring folder and D26+
   Clearing folder: full/visible range, median, histogram, side widths, support,
   status, largest gap, outside-folder counts and global extreme budget. On
   these pinned inputs, expect D26.0 and D26.9 examples in both lists, sensible
   D27 lower/upper tails, and visibly wider D28; never fill tiers by quota.
4. Run conditional source-removal and leave-one-chart-out location/width
   sensitivity, including removal of the weakest chart. Report D27/D28 large
   gains and uncertainty plainly. Sensitivity cannot promote provisional
   ratings or turn a source-biased leaderboard into a population census.
5. Verify unchanged algorithms below 20; report indirect corrected-input
   changes rather than asserting numerical invariance. Verify unchanged
   Singles fit identities, Co-op, raw captures and recommendation artifacts.
6. Run directly affected Python/frontend tests, typecheck, build and browser
   checks for both metrics and Pumbility. Perform one general review, repair
   proven regressions once, rerun affected verification and stop. If corrected
   inputs materially contradict this preview or fail acceptance, report the
   discrepancy rather than repeatedly tuning constants to fill named tiers.

Research artifacts (private, aggregate outputs only):
`.local-data/version-exclusion-spread-research/research.json`,
`calibration-comparison.json`, `submitted-impact.json`, and their replay scripts.
