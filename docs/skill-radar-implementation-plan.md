# Skill Radar implementation plan

Status: implemented locally in the Skill Radar worktree. Production deployment is separate.

Add /skill-radar using the PIU Training Tracker's BPM progression calculation,
presented as a radar with targeted chart suggestions. User choices are confirmed.
The original implementation requirements follow below.

Local preview: `/skill-radar`. Generate profiles from cached inputs with
`scripts/build_local_skill_radar.py --source-root <existing-workspace>`.
For a Windows worktree sharing dependencies, start Next with
`npm run dev -- --webpack --hostname 127.0.0.1 --port 3001`.

## Confirmed behavior

| Item | Rule |
| --- | --- |
| Traces | Red Singles; green Doubles. |
| Players | Skill Radar requires 100+ current submitted Phoenix 2 chart records across all modes and levels, including broken plays. Filter its dedicated username list and enforce the same rule on direct profile access. Other pages retain their existing lists. |
| Alignment | D20.5 PG equals S19.5 PG. S10/D11 maps to 0; S25/D26 to 100. |
| Missing scores | Pad missing top-five slots with zero. Three usable scores plus two zeros still divide by five. Empty buckets show zero skill and count as weakness candidates. |
| Groups | Three weaknesses and three strengths total, across both modes, in any mix. |
| Suggestions | Up to ten charts in official level max(16, floor(actual PG +1.5)), ordered by calculated Pumbility difficulty. Include played and already-SSS+ charts. |
| Personal scores | Show scores and plates using the tier list's formatting. |
| Rating details | Select an S/D radar rating to open its five contributing charts, including the exact selected score/plate and cent-rounded earned Pumbility. Show missing slots as zeros. |

Use SSS+ Pumbility ratings for suggestions. Keep the initial UI focused without
an additional scoring-goal control.

## Calculation and data

Port the pure calculation and parity fixtures from
[PIU Training Tracker, commit 237e9f0](https://github.com/jfung9021/pump-it-up-trainer/blob/237e9f00b728c27150cb927dba8b8f79195526db/src/domain/bpm-progression.ts).
Do not call its authenticated progression endpoint.

Use these nine BPM buckets, in order:

    <150 | 150–159 | 160–169 | 170–179 | 180–189
    190–199 | 200–209 | 210–219 | 220+

Bounds are lower inclusive and upper exclusive. The whole song BPM interval must
fit: 150–160 is excluded; 150–159.999 fits. Exclude missing or invalid BPM and
intervals crossing buckets, for both skill evidence and suggestions.

Use successful, nonbroken personal Phoenix 2 records from **all chart levels**.
Keep the best earned Pumbility per unique chart with the tracker's exact tie
rules. Prefer supplied Pumbility; otherwise use its official-level, score and
plate pricing. Preserve supplied-value rounding and fallback flooring to cents.
Valid supplied Pumbility can contribute even if score or plate is absent.

Per mode and bucket, select the top five unique charts, pad with zeros, then:

    T = sum(selectedPumbilityCents) / 100
    b = (T / 5) / (760 / 750)
    effectiveLevel = b <= 375 ? b / 7.5 - 27 : 23 + (b - 375) / 15
    rawPG = max(0, effectiveLevel + (Double ? 1 : 0))

    displayPG = max(0, rawPG - (Double ? 1 : 0))
    radarScore = clamp(100 * (displayPG - 10) / 15, 0, 100)
    suggestedLevel = max(16, floor(rawPG + 1.5 + 1e-9))

Nonpositive totals return zero. Keep full precision after inversion; round labels
only. Parity anchors: five S24 SSS+ PG records give PG24; four plus a zero give
approximately PG14.6. PG names the equivalent reference performance; contributing
records need not themselves have a PG plate.

Maintain two metadata sources:

- **Skill evidence:** complete Phoenix 2 chart/BPM metadata, including below 16,
  joined with private player daily-shard and live-state records.
- **Suggestions:** a compact catalog of final
  pumbilityVariants.sssplus.estimatedDifficulty values, currently rated 16+ charts,
  plus chart title, mode, official level, BPM, artwork, video and evidence.

The rated catalog cannot replace all-level metadata. Earned Pumbility uses
official levels; suggestions use calculated tiers. Reuse player lookup, consent,
score-state merge and refresh jobs. Keep internal IDs and histories server-side.

## Suggestions and grouping

Match mode, BPM bucket and the exact suggested official level. Use a 1e-9
tolerance for floating-point noise at whole-level boundaries. Sort by
full-precision calculated Pumbility rating, song name, then chart ID. Take up to
ten unique charts. Calculated ratings may be below or above that official level.
Existing scores never exclude charts. Show fewer when fewer qualify.

S18.0 PG selects S19 charts; S21.2 PG selects S22 charts. D19 raw PG displays
like S18, but selects D20 charts. The visual offset never lowers D targets.
Targets below 16 use the level 16 folder, the first available rated folder.

Partition all 18 combinations:

1. Order by full-precision display PG, then BPM order, then Singles before
   Doubles. Select the lowest three for **Work on your weaknesses**.
2. Exclude those three. Select up to three nonempty, positive-skill combinations
   by descending display PG, retaining the same tie order, for **Strengthen your
   strengths**. Do not label an unplayed zero bucket an established strength.
3. Put every remaining combination in **Work on everything else**.

No combination appears twice. Sparse buckets retain zero-padded PG and remain
eligible. Extra empty or sparse buckets keep a brief Needs plays status wherever
they appear; they do not enlarge the three-slot weakness section. Every bucket
can receive suggestions. Rank using display PG rather than clipped radar scores
to preserve differences above 100 and among low measured skills.

## Clean desktop and mobile UI

Add a homepage entry and shared navigation link. Reuse SiteHeader, PlayerPicker,
ExplanatorySection, ChartVideoLink and tier-list score formatting. Preserve the
public player URL key and browser Back/Forward behavior.

- **Desktop:** radar beside grouped bucket summaries and the selected ladder.
- **Mobile:** radar above the same groups in one column. Wrap navigation rather
  than shrinking its labels further.
- Keep all 18 summaries reachable. Expand one ten-chart ladder at a time; open
  the first weakness initially. Avoid up to 90 expanded chart cards.
- Summaries show mode, BPM, radar score and actual PG. Show Needs plays · 3/5 only
  when incomplete. Put the target minimum in the expanded ladder heading.
- Use compact chart rows: jacket, title, official level, calculated difficulty,
  personal score/plate and video action. Put exact song BPM in chart details.
- Use responsive SVG, readable BPM labels and sparse 0–100 rings. Distinguish
  modes with solid/circle and dashed/square styling as well as red/green.
- Title the chart **Skill by BPM** and keep axis labels to ranges; show smaller
  matching S/D ratings out of 100 outside the plot. Hide a mode's rating with
  its trace. Omit the footer caption.
- Summaries are the accessible value list and reliable keyboard/touch selection
  route. Zero markers overlap at the center; SVG hover cannot be the only route.
  Avoid a second duplicate value table.
- Empty buckets draw at zero. Failed requests show an error, not a zero profile.
  Hiding a trace does not change rankings or suggestions.
- Rating dialogs support mouse, touch, Enter/Space, Escape, and restored focus.
  Expose only the selected five public chart contributions, without internal
  player IDs, timestamps or full play histories. Suggestions remain separate.
- Keep copy to labels, numbers and short statuses. Put formulas, offset details,
  sparse-data effects and catalog limits in a closed **About Skill Radar** at the
  bottom. No **About player scores** or inline method essays.

## Implementation sequence

1. Port tracker aggregation and fixtures; implement mapping, grouping and
   suggestions in a pure skill_radar.py module.
2. Publish a compact suggestion catalog with its matching tier generation. Cache
   results by player revision, metadata/catalog generation and method version.
3. Add production GET /api/skill-radar?playerKey=... and a matching local Next
   route. Generate local artifacts through the same Python engine.
4. Return public player identity, freshness/generation, 18 bucket aggregates and
   at most ten suggestion rows per bucket, including existing score/plate. Use
   existing visibility and no-store patterns. Never send full analysis/history.
5. Build the responsive page, focused chart rows, selection state and navigation.

## Acceptance checks

- Tracker parity, cent rounding, deduplication, broken records and BPM edges.
- All-level skill evidence works alongside the rated 16+ suggestion catalog.
- Five real contributions and three-plus-two-zeros produce expected PG values.
- D20.5 aligns with S19.5; offset applies once and never enters raw thresholds.
- S18.0 PG returns up to ten S19 charts ordered by Pumbility rating, including existing
  SSS+ results. Personal score/plate display matches the tier list.
- Zero/sparse buckets, ties, fewer than three established strengths, shortages
  and missing ratings partition without duplication or invented values.
- Cache invalidation, public-field allowlists and production/local parity pass.
- At 320px, 390px and desktop widths: no page overflow, nine readable labels,
  wrapping titles, visible focus and usable touch targets. All 18 buckets are
  reachable without hover, including overlapping zeros.
- One ladder starts expanded; explanations start collapsed. Run affected tests,
  type checking, build and one desktop/mobile interaction pass. Stop when
  acceptance passes. Production deployment requires a separate request.
