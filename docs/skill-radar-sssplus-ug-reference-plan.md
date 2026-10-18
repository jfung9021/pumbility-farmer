# Skill Radar: SSS+ UG reference plan

Status: implemented locally after three-subagent planning review. Production deployment is separate.

## Confirmed scope

Use **SSS+ with an Ultimate Game (UG) plate** as the website's Skill Radar
reference. Preserve every player's actual score, plate and earned Pumbility.
The change covers radar values, equivalent-level labels, recommendations,
strength/weakness ranking, contributor summaries and related analysis tables.

SSS+ begins at **995,000**. The current reference already uses this grade band;
the change is from the PG reference bonus to the UG reference bonus.

Keep Phoenix 2-only evidence, all chart levels, the best five unique earned
Pumbility contributions, zero padding, cent rounding, nine BPM buckets,
100-record eligibility, ten suggestions and the existing chart selection rules.
Real PG plates remain PG, including their earned bonus and chart-row labels.
The separate Training Tracker repository and scoring/clearing tier algorithms
are outside this change.

## Calculation

UG bonuses differ by mode. Derive the multipliers from the existing grade and
plate tables in `phoenix2_pumbility.py`; do not reuse the PG multiplier.

```text
referenceMultiplier(Single) = 758.5 / 750
referenceMultiplier(Double) = 758 / 750

T = sum(selected contribution cents) / 100
b = (T / 5) / referenceMultiplier(mode)
effectiveLevel = b <= 375 ? b / 7.5 - 27 : 23 + (b - 375) / 15
rawEquivalentLevel = max(0, effectiveLevel + (Double ? 1 : 0))
displayEquivalentLevel = max(0, rawEquivalentLevel - (Double ? 1 : 0))
radarScore = clamp(100 * (displayEquivalentLevel - 10) / 15, 0, 100)
suggestedLevel = max(16, floor(rawEquivalentLevel + 1.5 + 1e-9))
```

Nonpositive totals return zero. Preserve full precision after inversion.
S10/D11 remains zero; S25/D26 remains 100 on the new equivalent-level scale.
Rank groups using uncapped `displayEquivalentLevel`; choose recommendation
folders using uncapped `rawEquivalentLevel`.

## Implementation order

1. **Backend calculation** — In `skill_radar.py`, replace `pg_equivalent` with
   a reference-neutral helper using the mode-specific UG multiplier. Leave
   actual earned-Pumbility pricing and contribution selection intact.
2. **Contract and caches** — Rename `rawPg`/`displayPg` to
   `rawEquivalentLevel`/`displayEquivalentLevel`. Add profile metadata
   `reference: {grade: "SSS+", plate: "UG", minimumScore: 995000}`. Set
   `METHOD_VERSION = 2`, `PROFILE_VERSION = 5`; update the TypeScript contract,
   strict field allowlists and duplicated contribution inversion in
   `lib/skill-radar.ts`. Reject old-method profiles. In
   `scripts/build_local_skill_radar.py`, replace hard-coded method version 1
   with the shared constant. Existing runtime cache keys already include the
   method/profile versions.
3. **UI and documentation** — Update radar tooltips, bucket summaries, rating
   dialogs and the closed About section in `app/skill-radar/page.tsx`. Use compact
   `S21.2 UG` labels and explain the SSS+ reference only in About. Keep actual
   contributor plates visible. Update the original implementation document;
   retain its tracker attribution for unchanged pricing/aggregation rules.
4. **Fixtures and local data** — Update Python/frontend fixtures together.
   Regenerate all eligible local profiles and the suggestion catalog from cached
   Phoenix 2 submitted records through the existing builder. Recalculate the
   previous FEFEMZ and theoretical-ceiling tables using the UG inverse.
5. **Verification and release preparation** — Run the checks below and inspect
   the local page once at desktop and mobile widths. Production profiles rebuild
   on request; a future release must coordinate frontend and backend versions.
   No score pulls, tier-model refits or deployment are required for this plan.

## Analysis and acceptance

- Five S23 SSS+ UG performances give equivalent S23; five D24 SSS+ UG give
  equivalent D24. Their aligned values both equal 23. Test S25/D26 at 100.
- Cover both piecewise branches, the 375 breakpoint, the 994,999/995,000 grade
  boundary, empty buckets, three contributions plus two zeros and cent flooring.
  Some UG-priced records invert just below their integer level after flooring;
  preserve this precision rather than requiring false exact equality.
- Snapshot the selected contributor IDs, scores, plates and earned Pumbility
  before and after: they must match. Genuine PG records may exceed the chart's
  UG-equivalent level. Never rewrite actual plates to make the reference fit.
- Check recommendation folder boundaries, the level-16 floor, ten-chart limit,
  cross-mode group ordering and uncapped ordering above the visible 100 limit.
- New Python output must pass TypeScript reconstruction; old PG-method fixtures
  must fail. Run focused radar Python/frontend tests, shared Pumbility pricing
  tests, typecheck and build. Validate regenerated eligible profiles and the
  contributor dialog at desktop/mobile sizes. Stop after these checks pass.
- The FEFEMZ mock retains SG for nonperfect official scores and PG only for
  1,000,000. Those assumptions are separate from the UG reference. In a formula
  check, his ratings rise approximately 0.3–0.7 points; no target folders change.
- An actual theoretical maximum still assumes real perfect-score PG performances,
  expressed on the new UG scale. A table assuming SSS+ UG on every chart must be
  called a benchmark, not an absolute ceiling. This migration does not make 100
  attainable in every BPM bucket: the catalog's Singles <150 ceiling moves from
  85.3 to about 86.0 with actual PG maxima preserved.

## Audit map

| Purpose | Files |
| --- | --- |
| Reference inversion and dependent policies | `skill_radar.py` |
| Contract and frontend inversion | `lib/skill-radar.ts` |
| Labels, tooltips, dialog and About | `app/skill-radar/page.tsx` |
| Cache identity and local generation | `skill_radar_runtime.py`, `scripts/build_local_skill_radar.py` |
| Tests and fixture | `tests/test_skill_radar.py`, `tests/skill-radar.test.ts`, `tests/fixtures/skill-radar-profile.json` |
| Current behavior documentation | `docs/skill-radar-implementation-plan.md` |

Retain the real PG/UG plate tables and code validators in shared Pumbility and
score modules. The separate S/FG player-skill reference used by other analysis
is unrelated and remains unchanged.

## Local implementation verification

- 30 Python radar/pricing tests and 10 frontend radar tests pass.
- Typecheck and the production webpack build pass.
- All 199 eligible profiles regenerated offline; all 3,582 buckets pass the
  TypeScript validator and preserve their exact previous contribution data.
- The local API returns HTTP 200 with method 2 and the SSS+ UG reference.
- FEFEMZ and theoretical-ceiling analyses were recalculated through the new helper.
- Desktop/mobile interaction checks remain unverified: browser URL policy blocked
  access from its saved connection-error page. No browser-policy workaround was used.

Desktop/mobile verification subsequently passed during the
[BPM-ceiling calibration](skill-radar-bpm-ceiling-calibration-plan.md).
