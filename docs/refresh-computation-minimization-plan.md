# Refresh computation minimization implementation plan

## Objective and acceptance

Reduce the work needed to update the website while preserving its retained
General and SSS+ ratings, Clearing, Pumbility, recommendations and Radar. Remove
computations that serve retired outputs or unused research diagnostics. Reuse
unchanged calculations instead of weakening the mathematical checks that affect
published values.

This is a plan for subsequent refreshes. The active recovery
`analysis-20261026T01`, its pinned deployment, inputs, checkpoints, samples, seeds
and publications remain intact. Scheduled monitoring stays paused. This planning
task does not trigger a refresh, change numerical policy or deploy code.

Success means identical full-precision retained product values on identical
inputs, fewer actual numerical operations, correct freshness metadata and the
existing atomic publication guarantees. A changed method/audit envelope may
describe the smaller producer; it must never claim omitted work was performed.

## What the website needs

| Surface | Required work and update boundary |
| --- | --- |
| General scoring | Submitted-score processing plus the existing official scoring overlay, calibration, support rules, intervals and ranks. |
| SSS+ scoring | Its own official/submitted Singles/Doubles fits, refinement, calibration, sensitivity safeguards and uncertainty-dependent tail/width rules. |
| Clearing | Submitted-score Clearing plus the existing high-level official overlays, including the combined Phoenix evidence used for D26+. |
| Pumbility | The corresponding scoring/Clearing combination, component availability, evidence status and ranks. |
| Global recommendations | Separate Singles/Doubles mode models, Overall assembly and verified player-input shards; publish with the tier generation. |
| Individual recommendations | On-demand selected-player refresh and serving-model compatibility. A daily global publication does not rebuild every cached player response. |
| Radar | Read-time calculation from saved player scores, metadata and SSS+ Pumbility; it does not train a model or fetch upstream scores. |
| Official Comparison | A separate builder and index/profile generation. Preserve its independent freshness contract; it is not currently rebuilt by the daily tier job. |

The Phoenix 1 static archive change is already deployed. Keep it; do not add a
second archive mechanism or reactivate Phoenix 1 network requests.

## Computations to remove and retain

| Computation | Product dependency | Decision |
| --- | --- | --- |
| Four internal SSS cohorts and SSS-only refinement/centering | Retired SSS values. Retained SSS+ uses its own fits, curves and ranges; today's requirement is the legacy baseline/schema chain. | Remove from the new retained-goal producer after separating required Clearing work and proving parity. |
| Source-removal optimizer fits | Diagnostic source-sensitivity fields; no retained point, interval, support, eligibility or tail consumer was found. | Remove from routine publication; retain an explicit on-demand analysis path if needed. |
| Training-only General holdout benchmark | Diagnostic comparison scores; it does not select target regularization or produce displayed General ratings. | Remove from routine publication and mark the diagnostic as not computed. |
| SSS+ regularization selection and main fits | Select model parameters and determine effects, eligibility, calibration and rare estimates. | Retain. |
| SSS+ regularization-sensitivity fits | Produce provisional flags that can reject point-changing tail exceptions. | Retain. |
| SSS+ 200-sample bootstrap | Produces intervals and controls some final tail and level-20 width decisions. | Retain, including seeds, ordering, convergence policy and exact compatible-cache reuse. |
| Score-gap calibration, support, connectedness and convergence checks | Affect one-sided/rare estimates and whether values are publishable. | Retain. |
| General and Clearing bootstrap | Produces retained uncertainty values and diagnostic labels. | Retain under the exact-output scope. Removing these intervals is a separate product-policy decision. |
| Integrity, schema, predecessor and publication checks | Prevent mixed generations, corrupt inputs and false freshness. | Retain. |
| Repeated deterministic validation replay | Repeats projection, calibration grids, copies, ranks and hashes, rather than the 200 target bootstrap solves. | Keep in the initial change. Optimize only if measured cost justifies a separate bounded change. |

The important correction is that internal SSS is not a mathematical input to
retained SSS+ calibration. It is required by the current legacy schema and
validation chain. A new baseline can remove that structural dependency.

For four fresh, nonempty SSS cohorts, removing that branch avoids **800 bootstrap
refits and 32 preliminary optimizer solves**, plus its refinement and diagnostic
work. This is an operation-count reduction, not a promised wall-time percentage.
The four retained SSS+ cohorts still require their current numerical work when
their actual inputs change.

## Implementation sequence

### 1 Define the retained output contract

Create one comparison helper over existing validated fixtures/artifacts that
selects the product fields for General, SSS+, Clearing and Pumbility. Include
full-precision estimates, intervals, differences, support/eligibility, evidence
and provisional flags, cap/fallback assignments, ranks, relative bands, tail
exception selections and level-20 guards. Include chart/catalog membership,
source exclusions and comparability.

Keep compact recommendation inputs and representative recommendation/Radar
outputs in the comparison. Compare numerical serving identity separately from
run/check timestamps. Explicitly allow only retired SSS fields, unused research
diagnostics and documented producer/provenance envelope differences to disappear.

Use existing persisted fit results to replay assembly. Do not repeat a complete
production bootstrap run just to create a baseline. Add small deterministic
fixtures for supported/unsupported folders, one-sided/rare charts, extreme tails,
level 20, D26+ Clearing, source exclusions and missing evidence.

### 2 Remove the retired SSS branch

Introduce a versioned retained-goal pipeline and baseline that accepts General
and required Clearing directly, without synthesizing an SSS result to satisfy
schema 51. Keep historical schemas, builders and validators readable for old
artifacts and pinned recovery jobs.

Change the new preparation/fit descriptors from eight cohorts to the four SSS+
cohorts: official/submitted evidence, each in Singles/Doubles. Remove the SSS-only
fit, bootstrap, refinement, centering, interval and rank work from this producer.

Before removing the legacy `centered-sss` stage, extract the official D26+
Clearing update and General Pumbility/composite/rank updates that are bundled
inside `apply_refined_tiers`. Preserve their existing inputs, sample/seed policy,
ordering and the subsequent combined-official Clearing overlay. Do not assume a
later overlay makes the earlier operation redundant without the retained-output
comparison proving it.

Update SSS+ baseline validation and immutable predecessor hashes to describe the
new producer truthfully. Derive progress totals and continuation identities from
the new work descriptors; the old 18-unit counter is not a fixed fit count.

If parity exposes a real dependency on an SSS result, identify that exact field
and stop this removal rather than changing a retained numerical rule to force it
through. An internal SSS point-only path is a possible separately bounded
fallback, not a reason to silently weaken SSS+ evidence.

Primary files: `production_tiers.py`, `sssplus_tiers.py`,
`sssplus_median_calibration.py`, `refined_tiers.py`, `source_aware_tiers.py`,
`tier_public.py`, the relevant Python/TypeScript validators and numerical recipes.

### 3 Remove research diagnostics from routine publication

Separate target numerical results from optional diagnostics in `target_fit_cache`
and the hosted producer. Stop calling source-removal fits and the training-only
General benchmark during routine refreshes. Keep a named diagnostic policy and
use explicit `not-computed` metadata or omit optional fields under the new schema;
do not reuse an old diagnostic timestamp or assert the benchmark ran.

Retain the validation fits that select regularization, the alternate fits that
produce the `regularization-sensitive` flag, all support/convergence checks, and
the score-gap calibration used in published estimates. Removing the General
benchmark must not remove the actual General model or recommendation models.

Preserve existing compatible main-fit caches and captured point context. An
optional diagnostic policy change must not force identical SSS+ point/bootstrap
results to be recomputed. Historical diagnostic artifacts remain available.

Primary files: `target_fit_cache.py`, `sss_scoring_model.py`,
`production_tiers.py`, `numeric_recipe.py` and producer/schema validators.

### 4 Reuse unchanged official evidence

After a bounded live Phoenix 2 epoch probe and validated catalog/scope comparison,
reuse the previous complete capture when its source contract is unchanged. The
current implementation resumes pending captures but re-fetches completed Phoenix
2 boards on a later run. Preserve mixed-epoch rejection and an explicit forced
capture path.

Define retry behavior for new charts, empty/not-mirrored boards and coverage gaps.
An unchanged epoch cannot permanently hide a newly available board. Fetch the
affected missing/new states or take the full path when compatibility is not
established. Pin the validated catalog and capture references once per owning job
and reuse them across its bounded continuations.

Keep original source epochs and fetch timestamps in the archived evidence. A
successful recheck records a new check time without pretending the old data was
newly captured. Phoenix 1 continues to come from its permanent archive.

Primary files: `official_scoring_sources.py`, `analysis_runtime.py` and capture
workflow tests. No new upstream API capability is assumed.

### 5 Reuse numerical results across check envelopes

Keep full immutable capture/provenance hashes, and add a separately versioned
identity for the actual inputs to each numerical component. Today the complete
official manifest includes fetch envelopes and is embedded in broad pipeline and
General lookup keys, so identical values can miss those broad caches.

The semantic identity must retain observations, ordered player/chart membership,
identity-resolution/source decomposition, catalog fields used by the model,
availability/truncation states, exclusions, support/comparability rules and the
numerical policy/implementation. A source timestamp alone must not be mistaken
for proof of identical observations.

Key General's cache by its actual builder inputs, including official scoring
observations. The intermediate `general_arguments` dictionary omits the manifest
for one identity field, but the actual builder receives the complete arguments
and uses the official scoring overlay. Replace only the manifest's fetch/check
envelope with a validated semantic identity; keep all official observations,
optional Clearing arguments and submitted records/metadata as dependencies.

Look up a compatible completed computation using a small validated dependency
descriptor before rebuilding the full merged/prepared context. Preserve the
existing exact binary target-fit and recommendation component caches rather than
adding a parallel cache system. Reuse unchanged numerical content under a fresh
publication envelope with accurate computation/check/publication times.

Primary files: `tier_computation_cache.py`, `production_tiers.py`,
`target_fit_cache.py`, `recommendation_component_cache.py` and publication adapters.

## Verification

The following checks are the acceptance boundary for the implementation:

1. **Retained product parity:** old/new producers on identical validated inputs
   yield exactly equal retained full-precision values, intervals, evidence,
   support, ranks, exception sets, Clearing/Pumbility, recommendation inputs and
   Radar values. Do not normalize away meaningful flags or uncertainty fields.
2. **Work removed:** instrument optimizer/producer calls in tests and assert zero
   SSS cohort/SSS bootstrap calls, four retained SSS+ cohorts with the current
   200-sample policy, and no routine source-removal/General-benchmark fits.
3. **Required evidence preserved:** retain tests showing that SSS+ CI and
   regularization-sensitivity changes can change an exception/width decision.
   Keep corruption, nonconvergence, insufficient support and mixed-source failure
   tests; those checks are not expendable research diagnostics.
4. **Reuse and invalidation:** a timestamp/check-only change performs no numerical
   refit; a threshold crossing, observation removal, membership/source-policy or
   relevant catalog change invalidates the affected computation. Same-band score
   changes may reuse binary target fits while still updating raw-score consumers.
5. **Capture:** unchanged epoch/catalog reuses compatible complete evidence;
   new/missing-board retries, changed epoch and forced capture still fetch the
   appropriate data. Partial/error captures preserve the last complete manifest.
6. **Recovery and storage:** interrupted checkpoints resume exactly; corrupt
   optional caches miss safely, while corrupt pinned artifacts fail. Legacy jobs
   keep their producer and continuation contract. Reuse never skips required
   checksum or source-revision verification.
7. **Publication and frontend:** retain the atomic tier/global-model join, lazy
   list/detail generation agreement, recommendation stale/current-model metadata
   and Radar checks. Public goals stay exactly General/SSS+; SS/SS+/SSS remain
   absent. Run affected backend suites, frontend tests, typecheck and build.
8. **Measured work:** record cold changed-input and warm unchanged-input timings,
   cache hits and numerical operation counts on bounded fixtures or a future
   authorized run. Do not promise a completion time from the reduced fit count.

Existing test foundations include `test_production_tiers`, `test_target_fit_cache`,
`test_target_fit_budget`, `test_sssplus_tiers`, `test_sssplus_spread`,
`test_submitted_sssplus_calibration`, `test_source_aware_tiers`,
`test_tier_computation_cache`, `test_official_refresh_workflow`,
`test_recommendation_component_cache`, `test_skill_radar`, `test_retired_tier_goals`
and their frontend validation fixtures.

## Release boundaries and stopping rule

Implement as three scoped changes: retained-goal producer removal, routine
diagnostic removal, then unchanged-source/numerical reuse. Each change carries
the affected parity/recovery tests. Separate new producer/validator identities
from historical ones; do not invalidate or delete previously computed models,
samples, seeds or checkpoints merely because scheduling is smaller.

After implementation, perform at most one general integration review. A proven
regression permits one focused repair and the affected checks, without restarting
general review. Stop when the acceptance checks pass.

A future authorized release should verify its merged production commit, retain
the previous complete publication for rollback, and activate only on a new job
after the current recovery reaches a documented safe terminal/head-release
transition. Do not run competing refreshes or rewrite the active recovery to
adopt the smaller producer. No monitor, new schedule or diagnostic automation is
created by this plan.

## Work outside this first implementation

Removing retained SSS+ uncertainty or changing its tail/width criteria would
change displayed numbers and requires a separate numerical/product decision.
Removing General/Clearing intervals would change retained uncertainty fields.
Reducing model validation used to select regularization would change the model.
None is included in the exact-parity implementation.

Repeated checkpoint uploads, growing capture serialization and deterministic
validator replay have identifiable overhead, but their performance share has not
been established. Profile them after the primary removals before introducing new
chunking or validation-receipt infrastructure. Do not remove boundary validation
or add concurrency just because more optimization is possible.

Official Comparison remains an independent generation. Automatically rebuilding
it after capture would be a separate freshness feature, not a prerequisite for
the computation reductions above. Keep its source/publication dates honest.

## Dependency evidence

- `production_tiers.py:697`: eight current SSS/SSS+ source/mode preparation cohorts.
- `production_tiers.py:440`: legacy SSS stage and subsequent retained stages.
- `refined_tiers.py:53`: required high-level Clearing update bundled into that stage.
- `sssplus_tiers.py:35`, `sssplus_tiers.py:143`: SSS+ uses its own fits/refinement.
- `sssplus_median_calibration.py:35`: SSS+ curves and ranges, independent of SSS values.
- `production_tiers.py:466`: rare correction uses SSS+ official fits.
- `target_tail_policy.py:66`, `target_tail_policy.py:78`: sensitivity/CI affect point-changing exceptions.
- `level20_scoring_width.py:159`: upper CI participates in the extreme lower guard.
- `target_fit_cache.py:295`, `sss_scoring_model.py:429`: diagnostic source fits and General benchmark.
- `production_tiers.py:206`: current strict 200-bootstrap publication contract.
- `official_scoring_sources.py:295`, `official_scoring_sources.py:491`: hosted capture and pending-only resume.
- `tier_computation_cache.py:32`, `production_tiers.py:511`: broad identity and preparation before cache lookup.
- `recommendation_refresh.py:1094`: selected-player full fetch protects late-arriving best records.
- `skill_radar_runtime.py:65`: read-time Radar inputs; no numerical training job.
- `scripts/build_official_player_comparison.py:15`: independent comparison generation.
