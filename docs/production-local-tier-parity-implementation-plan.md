# Production and local tier list parity implementation plan

Date: October 17, 2026.

Complete production Pumbility parity across readers, displays, demo data, version identities, tests, and the existing production report exporter. Preserve the new local cross-level Scoring and Clearing methods. The active local data already has 1,306 correct maximum composites and 1,375 unrated composites; these fixes do not require fitting either model again.

Implementation completed locally on October 17, 2026. Verification passed for 260 affected Python tests, 84 frontend tests, TypeScript, the production build, and browser checks of current maximum, production-format maximum, historical average, demo, and interval text. One general review produced one focused version-dispatch correction. Numerical and artifact preservation evidence is in `output/production-local-tier-parity-2026-10-17/verification.json`. The separate 12-page report is `output/pdf/tier-list-insights-production-current.pdf`. No unresolved implementation blocker remains.

## Confirmed gaps

The deployed baseline is [production PR 109](https://github.com/jfung9021/pumbility-farmer/pull/109), merged as `bad17e5`. The working checkout starts at `934dcf1` and includes the intentional local model changes. Three subagents independently inspected frontend behavior, contracts, and calculations/tests. The following findings have source evidence or a reproduced failure.

| Gap | Evidence | Required result |
|---|---|---|
| Demo Pumbility still averages | `lib/demo-data.ts:185`; all 18 rated composites in each Phoenix demo differ from maximum | Demo values, bands, ranks, and explanations use maximum |
| Current production payload is rejected locally | The captured schema 41 / tier-method 11 payload fails `validateLocalAnalysisPayload`; changing the tier version alone still fails because unmarked maximum values are interpreted as averages | Accept the deployed contract without changing its numbers |
| Production maximum values would receive average labels | `app/tier-list/page.tsx` recognizes only the newer `aggregation` marker in its detail/footer labels; production declares maximum through `calculation` instead | Rendering and validation resolve the same declared rule |
| Writer identities lag the implemented rule | Base `TIER_METRIC_VERSION` remains 10 and `SCRIPT_VERSION` remains 6.15; production uses 11 and 6.16. Overlay-only commands inherit the base script label | New outputs identify the actual producer and policy accurately |
| Two Python tests retain average expectations | Reproduced failures in `tests/test_combined_scoring.py:99` and `tests/test_local_tier_builder.py:170` | Current-writer assertions expect maximum and retain their isolation checks |
| Cross-level Scoring has an inaccurate CI tooltip | `app/tier-list/page.tsx:321` still describes profile curves, folder centers, and selected scales; 1,086 local charts meet this render condition | Explain the actual fixed-effects and median-anchor bootstrap |
| Existing report tools cannot consume current production | `scripts/tier_report_data.py:107` raises `IndexError` on three-coordinate score profiles; later checks assume averaged Pumbility and submitted Clearing. Production includes 65 rated official-Clearing rows without submitted `q10Skill` | Existing production export supports its current methods, with accurate validation, plots, and prose |

The report issue affects local export tooling; it is not a missing website control. Existing report code also fixes its profile axis at five coordinates and describes cancellation between the two components, which is inappropriate for a maximum.

## Behaviors to preserve

All production chart IDs are present locally. Metric/mode switching, search, official-level selection, BPM filtering, grouping, layout switching, player score overlays, videos, details, keyboard/focus behavior, unrated sections, and URL navigation remain present. Keep the local `Only 20+` option.

Keep the requested official Phoenix 1/2 maximum-score merge, cross-level Scoring calibration, and exclusion of current levels below 20. Keep submitted Clearing with the top 30 hardest unique clears and at least 200 valid same-mode records. Missing leaderboard appearances remain unknown.

Seven production-rated D27/D28 charts have no qualified submitted clearers locally. Preserve their unrated Clearing values under the selected local method. Co-op uses the same method in both environments, but 24 estimates differ between the captured inputs; preserve the existing local Co-op values. This work does not refresh inputs to force numerical agreement with production.

## Generation compatibility

Resolve Pumbility using explicit policy metadata first, followed by narrowly recognized historical declarations and schema/method combinations. A tier version alone is insufficient: version 11 exists in both the deployed maximum generation and an earlier local schema 43 average generation.

| Stored generation | Pumbility rule | Read and display behavior |
|---|---|---|
| Historical tier-method 10 with the original average contract | Average | Retain accurate historical interpretation |
| Production schema 41 / tier-method 11 with the known `max(...)` calculation declaration | Maximum | Accept without adding markers to stored data |
| Historical local schema 43 / tier-method 11 with the average calculation declaration | Average | Retain rollback compatibility |
| Current local schema 43 with explicit Pumbility version 2 / maximum policy | Maximum | Preserve current behavior |
| New base/official outputs | Maximum | Emit tier-method 11 and explicit Pumbility version 2 |

For all classes, both components must exist. Validate component values, support counts, and evidence according to the applicable generation. Reject contradictory declarations, unsupported combinations, and composites that disagree with their declared formula. Do not rewrite historical numerical values when reading them.

Historical reading and checkpoint reuse have different purposes. Keep current runtime checkpoint validation strict: an old average checkpoint must be recomputed when resuming a maximum writer, even when the UI can display the old artifact.

## Implementation order

### 1 Add compatibility fixtures and a shared frontend resolver

Minimize the captured public production aggregate into deterministic fixtures while retaining the calibration and provenance fields needed for validation. Include a genuine historical average and the prior local schema 43 average contract. Fixtures must contain public chart aggregates and method metadata only.

Add a small typed resolver, for example `lib/pumbility-policy.ts`. Use it in `lib/local-analysis.ts` for policy selection and component consistency checks. Use the same result in `app/tier-list/page.tsx` for the detail label and methodology footer. Pass the resolved generation rule into chart details rather than inferring it solely from individual row markers.

Retain the existing Scoring/Clearing validators. Extend only the recognized tier version and Pumbility dispatch cases. Do not turn compatibility into unconditional acceptance of version 11.

Acceptance: the unmodified production payload validates; production maximum, historical average, and current local maximum receive matching labels. Unknown and contradictory variants still fail.

### 2 Align writer and artifact identities

Set the base `TIER_METRIC_VERSION` to 11. Retain the explicit Pumbility version 2 policy and the separate schema 43 Clearing identity. Update `pumbility_contract.py` to a fresh implementation identifier, such as `6.17.0-cross-level-tiers-max-pumbility`, so this code is distinguishable from production 6.16 and the old 6.15 writer.

Update affected runtime fixtures and producer assertions together. Preserve the exact policy checks in `analysis_runtime.py`, including both checkpoint stages. A positive-freshness check should reject an old 6.15 generation for the new producer. Current local freshness is zero, so the stale version has not caused an observed local freshness failure.

Add a bounded tier-only metadata/composite refresh path to `scripts/build_local_recommendations.py`. Stamp the actual current producer identity and retain the inherited base identity as provenance; simply copying the old `summary.scriptVersion` is insufficient. This path should recompute derived Pumbility fields if needed without fitting Scoring or Clearing, writing recommendation generations, or changing personal-score artifacts.

Acceptance: new base and overlay outputs identify their method consistently; current Scoring/Clearing values and all protected artifacts remain unchanged.

### 3 Correct demo behavior and the Scoring tooltip

Update `lib/demo-data.ts` to use maximum with compatible summary/row policy metadata. Preserve the requirement for both components and the existing evidence semantics, and calculate bands/ranks from the resulting value. Update the demo test in `tests/frontend-api.test.ts`.

Give cross-level Scoring its actual conditional-player-bootstrap tooltip: fitted chart effects and median anchors are held fixed. Retain the existing profile-scale tooltip for historical profile generations. Keep the fuller chart-detail explanation consistent with the small interval summary.

Acceptance: `?demo=1&metric=pumbility` shows maximum values and labels; both scoring methods receive their own interval explanation. Historical fixtures explicitly construct their historical composites instead of silently inheriting current demo values.

### 4 Correct affected tests

Port the maximum expectations into `tests/test_combined_scoring.py` and `tests/test_local_tier_builder.py`. Keep their existing model equality, source isolation, and recommendation preservation assertions. Add focused coverage for either component winning, equal components, missing components, unrounded calculation, weaker evidence, and resulting bands/ranks where existing tests do not already cover them.

Add the generation matrix to the frontend tests. Include malformed metadata and stale composite values as rejection cases. Add coverage for accurate overlay provenance and preservation in the local builder tests.

### 5 Repair the existing production report export

Update `scripts/tier_report_data.py`, `scripts/tier_report_figures.py`, `scripts/build_tier_insights_report.py`, and the corresponding explanation in `docs/tier-insights-report.md`.

Read profile percentiles and weights from validated method metadata. Support both historical five-percentile profiles and the deployed three-percentile profiles, with matching axes and legends. Validate each Pumbility value against the declared average or maximum rule.

Dispatch Clearing verification and explanations between the existing submitted and official-clearer methods. Official rows must not pass through a formula requiring submitted `q10Skill`. Keep existing official cap, shrinkage, and calibration semantics. Replace mean/cancellation statements with generation-appropriate descriptions and ensure example selections remain valid under maximum.

Add focused report-data tests using historical five-profile/average and current three-profile/maximum inputs, including official overlays. Preserve the historical report's successful 2,606-row verification. Generate one new report for the frozen current-production input and visually inspect its relevant pages and figures.

The exporter remains a production-method report. New local cross-level schema 43 report support would require separate narratives and figures; reject that unsupported model clearly instead of applying the old formulas. Preserve the existing historical PDF and write the current-production candidate to a separate output path.

### 6 Verify the complete local candidate

Before changes, capture the active local aggregate and hashes of recommendation and personal-score artifacts. After the identity/composite refresh, require:

- Exact equality of Scoring row fields and all Clearing dictionaries.
- Exact equality of Co-op rows and protected artifact hashes.
- All 1,306 rated composites equal the unrounded maximum, with correct bands/ranks, support, and weaker evidence.
- All 1,375 unrated composites remain null under the existing availability policy.
- Current local schema 43, deployed schema 41 maximum, and supported historical average fixtures validate and render correctly.
- Existing controls and `Only 20+` remain functional.

Use pinned captures for these counts; changes to source inputs require a separately identified comparison.

## Verification commands and stopping rule

Run the directly affected Python suites, including the two omitted by the earlier check:

```powershell
.venv/Scripts/python.exe -m unittest tests.test_tier_difficulty tests.test_combined_scoring tests.test_local_tier_builder tests.test_official_tiers tests.test_official_cross_level_scoring tests.test_cross_level_clearing tests.test_recommendations tests.test_analysis_runtime tests.test_cross_level_runtime
npm run test:frontend
npm run typecheck
npm run build
```

Also run the focused new report tests. The reproduced production-report failure is:

```powershell
.venv/Scripts/python.exe -c "from pathlib import Path; from scripts.tier_report_data import build_report_data; build_report_data(Path('tmp/tier-list-parity/production-tier.json').resolve())"
```

That command should succeed after the report repair. Verify the historical capture separately. Browser smoke checks cover the current local page, production-format maximum labels, a historical average fixture, demo mode, and the corrected interval tooltip. Serve fixtures through an isolated test surface rather than replacing the user's active dataset.

After implementation, perform one general review. Repair only proven regressions, rerun the affected checks, and stop when the acceptance criteria pass. No additional reviewer agents or recursive review cycles are required. Implementation is local; deployment, data recapture, and model retuning are outside this plan.

## Audit outcome

The three audits found no additional missing production controls and no active local Pumbility arithmetic defect. The required work is compatibility and identity alignment, demo and explanation corrections, stale test repair, and the existing report exporter. Intentional model and input differences remain visible and are not filled using incompatible production ratings.
