# Tier Lists redesign proposal

Research and design proposal · 21 October 2026 · subsequently implemented locally

Make Tier Lists a calm, modern chart browser with a recognizable tier board, readable song identities and a compact set of primary controls. Retain the difficulty badges' familiar color and number as the compact chart-level identifier; their exact shape and finish can evolve. Use one coordinated palette across every tab while preserving the other tabs' object placements and workflows.

The recommended direction is **Quiet board**: retain jacket-based tiers and colored level bubbles, add visible song names, consolidate the controls and move occasional configuration into labeled disclosures. Browsing items do not repeat “official” difficulty descriptions or show Provisional/Limited data badges. Evidence information remains available in chart details. A **Filter rail** alternative is worth considering if task testing shows that repeated multi-filter browsing is the dominant activity. These are design proposals, not findings that either prototype already improves task performance.

**Expanded scope:** the later [shared-player, navigation and score-color implementation plan](shared-player-navigation-score-colors-implementation-plan.md) supersedes the original local player controls and top-navigation arrangement. Use one browser-saved personal player, optional Home setup, player gates for Recommendations/Radar and a shared Menu drawer. Other pages retain their content placements apart from the explicitly approved player/header changes. The preview now demonstrates plain artwork and global Menu placement; the [local implementation](ui-redesign-local-implementation.md) provides the full route-based experience.

## What was investigated

Three subagents independently investigated comparable products, usability/accessibility guidance and the local interface source. The main investigation inspected the live [Tier Lists](https://pumbility-farmer.vercel.app/tier-list), its Compact and Detailed presentations, filtering and chart evidence dialog. It also inspected the shared layout of [Recommendations](https://pumbility-farmer.vercel.app/recommendations) and [Skill Radar](https://pumbility-farmer.vercel.app/skill-radar), with source inspection covering Player Comparison.

The live Tier Lists was inspected at a desktop viewport and at **390 × 844 CSS pixels**. Comparable products were inspected on desktop; their mobile behavior was not tested. The mobile arrangements below are proposed adaptations. This was a UI investigation, not a runtime performance profile or a study with representative users.

Production currently exposes General, SS, SS+, SSS and SSS+ goals. This checkout renders a smaller available goal set. A redesign must accommodate the goals supplied by the running application rather than hardcode this checkout's two-button presentation.

## Why the current tab feels demanding

| Observed condition | Implication for the redesign |
| --- | --- |
| At 390 × 844, the first tier heading began at approximately **y = 853 px** from the document top, with the saved-data warning present. No chart was visible in the initial viewport. | Reducing the height before results is a concrete design objective. The target should be achieved by reorganizing controls, not shrinking their labels. |
| The first large panel is an optional player selector, followed by metric, goal, mode, search/level/BPM, grouping and layout. | Optional personalization takes space before the main browsing task. Related choices need grouping and different visual emphasis. |
| Several decisions use nearly identical segmented controls. | Metric, goal, grouping and density compete for attention even though they affect different aspects of the experience. |
| Source styles set some display-control labels to 8 px uppercase on desktop and a 6–8 px clamp on mobile. | Labels are hard to scan. A 40–42 px button can still contain unreadably small text; target size alone does not solve legibility. |
| Compact charts show a jacket and official-level badge without a visible song title. | Recognition depends on familiarity with artwork. Accessible names help screen-reader users but do not provide visible identification. |
| Detailed rows repeat artist/notes, official and estimated levels, status, target, source, usable records, rank, delta, interval and an evidence action. | There is a large jump between image-only browsing and information-heavy rows. A readable middle presentation is missing. |
| Current results controls have no combined filter summary, chart count or reset for all filters. | Users have less feedback about a narrow or empty result set. Only BPM has its own Reset. |
| Lime appears in the background grid/glow, branding, labels, selected controls, slider and borders. | Saturation no longer reliably signals priority. Artwork, tier colors and the accent all compete. |

Relevant source locations: [control stack](C:/Users/jfung/Downloads/piu_misgrade_bundle/app/tier-list/page.tsx:953), [compact cards](C:/Users/jfung/Downloads/piu_misgrade_bundle/app/tier-list/page.tsx:511), [results controls](C:/Users/jfung/Downloads/piu_misgrade_bundle/app/tier-list/page.tsx:1043), [display-control typography](C:/Users/jfung/Downloads/piu_misgrade_bundle/app/globals.css:384), [mobile typography](C:/Users/jfung/Downloads/piu_misgrade_bundle/app/globals.css:874), and [palette/background](C:/Users/jfung/Downloads/piu_misgrade_bundle/app/globals.css:1).

These observations support an information-hierarchy diagnosis. They do not establish whether delayed rendering, network requests or main-thread work also contribute to the reported clunkiness.

## Comparable interfaces and what to borrow

These are pattern references, not endorsements of each entire interface. None was shown by this investigation to outperform Pumbility Farmer in a controlled study.

| Product inspected | Useful observed pattern | Application here | Caution |
| --- | --- | --- | --- |
| [BeatConnect](https://beatconnect.io/) | Prominent search, neutral dark surface and a labeled Advanced action opening grouped filters in a right drawer with Apply/Clear. | Keep core browsing visible; disclose BPM and occasional filters. | Announcements, image-heavy cards and many glyphs add their own clutter. |
| [PIU Scores D18 pass tiers](https://piuscores.arroweclip.se/TierLists/Double/18?TierListType=Pass) | Separate ranking/folder choices and presentation controls; Comfortable, Compact and Table views; filters/columns drawer; tier counts. | Closest functional reference for separating list context, filters and display. | Its rich tags, login/import material and numerous controls are themselves busy. |
| [Sanbai Ice Cream Level 17](https://3icecream.com/difficulty_list/17) | Clear tier rows and a shared selected-song pane that updates when a jacket is chosen. | Browse many charts, inspect one chart's evidence at a time. | Image-only identification and a large fixed side pane would conflict with this proposal's mobile and legibility goals. |
| [PIU Center S21](https://www.piucenter.com/difficulty/S21) | Focused level scope, adjacent folder navigation and plainly grouped song links. | Keep the main scope clear and methodology outside the scanning surface. | Small colored links and dense wrapping are poor typography references. Previous/next level shortcuts are optional, not required for the first redesign. |
| [osu! beatmap listing](https://osu.ppy.sh/beatmapsets) | Strong title/artist hierarchy in compact horizontal cards. [Official search documentation](https://osu.ppy.sh/wiki/en/Beatmap_search) describes one broad search field. | Use one obvious song/step-artist search and consistent metadata order. | Interactive search was sign-in gated in the observed state. Search syntax and decorative card backgrounds are not proposed additions. |
| [TierMaker editor](https://tiermaker.com/create/anime-tier-list-300-anime-17194) | Stable tier-row grammar with color concentrated at row labels and operational controls separate. | Preserve the familiar spatial organization of tiers. | It is an authoring tool. Dragging, editing tiers, ads and unrelated calls to action do not belong in this analytical browser. |

The strongest combination is BeatConnect's control hierarchy, PIU Scores' separation of display from filtering and Sanbai's selected-chart detail pattern.

## What the usability evidence supports

| Evidence | Design implication | Strength and limit |
| --- | --- | --- |
| [NN/g progressive disclosure](https://www.nngroup.com/articles/progressive-disclosure/) | Keep frequent choices visible and specialized choices one clearly labeled step away. | Established usability guidance. It does not tell us how often this app's users change BPM or goals. |
| [Carbon filtering guidance](https://www.carbondesignsystem.com/building-blocks/core/patterns/filtering) | Keep applied filters visible when controls close, support clearing and use a structured panel for multiple categories. Batch application suits deliberate multi-filter work; fast simple filters can update immediately. | Design-system guidance. Our exact toolbar arrangement and wording are proposed applications. |
| [NN/g visual hierarchy](https://www.nngroup.com/articles/visual-hierarchy-ux-definition/) | Reduce competing saturation and use proximity, text size and contrast to establish priority. | Explains visual hierarchy; it does not prescribe a particular sage color. |
| [Tuch and colleagues, 2012](https://research.google/pubs/the-role-of-visual-complexity-and-prototypicality-regarding-first-impression-of-websites-working-towards-understanding-aesthetic-judgments/) | Favor familiar structure with lower visual complexity. | Two screenshot experiments found higher perceived appeal for low-complexity, familiar layouts. They measured aesthetic impressions, not task success or long-session comfort. |
| [W3C contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html), [target size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html) and [reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html) | Preserve legible text, usable hit areas and narrow-screen access while simplifying. | Accessibility requirements and informative explanations; visual minimalism is not permission to hide necessary information. |

The user's discomfort with the green is a sufficient product-design reason to reduce it. This proposal makes no medical claim that one theme prevents eye strain. Visual comfort should be tested separately from task completion.

## Recommended desktop arrangement

Use the shared shell defined in the expanded implementation plan. Inside Tier Lists, use this sequence:

1. **Short title row:** “Tier Lists” and updated time. Player selection belongs to the shared shell. Preserve visible warning/error status when applicable; do not hide stale-data warnings in a menu.
2. **List context:** Singles / Doubles / Co-op, followed by labeled **Metric** and contextual **Goal** selectors. Use one visible selected value for metric/goal instead of stretching every option across a full row. Show Goal for both Scoring and Pumbility.
3. **Browsing toolbar:** song/step-artist search, Official level, **Filters** and **Display**. Keep level visible because folder browsing is central to the existing product. Filters contains BPM; Display contains grouping and presentation.
4. **Results context:** chart count, current mode/metric/goal, active removable filter chips and **Clear filters** when relevant. Personal scores follow the shared active player; retain relevant score-sync status without another player dropdown.
5. **Tier sections:** small headings such as **S22.8** with subtle separators. Do not show a chart count beside each tier. Omit repeated “Estimated” or “Est.” prefixes; the selected metric and grouping establish the meaning. Keep the existing grouping definitions and ordering.
6. **Existing methodology disclosures:** retain them beneath the results.

Use instant updates for the existing local search and level filter, without moving focus or returning to page top. Let users change both BPM endpoints in the panel and apply them together. A reset should clearly distinguish clearing search/level/BPM from resetting display preferences or removing a player. **Clear filters** preserves metric, goal, mode, player and chosen presentation.

Filters and Display should be different labeled actions because one changes the matching charts and the other changes how the same charts appear. Do not combine them in an unlabeled gear menu. Keep the current Display values discoverable in that panel; an expanded summary can read “Difficulty · Grid.”

## Recommended mobile arrangement

Use the compact shared Menu header from the expanded implementation plan. Reflow the Tier Lists interior as follows:

- Compact title and update status.
- One row of mode choices.
- Metric and Goal in a labeled two-column row; Clearing and Co-op use only applicable controls.
- Full-width search.
- Official level, Filters and Display on the next row. At 320 px, wrap this row instead of reducing the type size.
- Count/context, then any active chips, then charts.

Keep the mobile tile grid at **four columns** to preserve a compact overview. Use narrow gaps, square jackets, compact colored number badges and song titles limited to at most two lines. Do not reserve two lines of height for short titles: BPM follows the actual title with a small consistent gap. Spare row height belongs below the metadata, not between a one-line title and BPM. Keep full titles in accessible names, hover/focus tooltips and chart details. Size the complete tile as the tap target; the badge is not a separate control. The readable-list alternative remains available.

The first redesign target is **at least one complete chart item visible without scrolling at 390 × 844**, in the normal loaded state with no panels open. Separately verify the saved-data warning state and 320 px reflow. This is a proposed project target, not an accessibility standard.

Open Filters in a full-width sheet, growing to full height when content or the keyboard needs it. Include a visible close control, Reset and Show results. Keep labels, values and actions reachable above the keyboard and device safe area. For the current small BPM scope, a simple disclosure can be sufficient in a low-risk first iteration; test it before adding sheet machinery.

Avoid making the whole control stack sticky. If repeated filtering later warrants sticky controls, pin only a compact search/filter strip and verify that it does not obscure focused content. Do not add a bottom navigation bar. Other pages keep their content arrangements except for the approved shared-header and player-control migration.

## Chart presentation and details

**Quiet board is the recommended default.** Retain jacket-based tier sections, a readable song title and a compact colored difficulty badge: red for Singles, green for Doubles and gold for Co-op, with the level/player count inside. Preserve the color and number without adding text such as “official” or mode letters inside the badge. A rounded rectangle, pill or softened circle can provide the same quick visual cue with a more refined finish. Include mode and level in the accessible name. Keep **four tile columns on mobile**, with compact gaps, smaller badges and titles limited to two lines; preserve full names in tooltips and details. Let desktop grids fit additional columns according to available width. Check narrow screens and text scaling without overlapping tiles or cutting off badge numbers.

In difficulty grouping, the section heading already supplies the rating as **S22.8**, **D23.1** or the applicable Co-op number. Each browsing item shows its jacket, song name, difficulty badge, BPM beneath the title and optional player score. BPM is essential browsing information and stays visible without opening details. Show a single value such as **180 BPM** or a range such as **170–173 BPM**; missing BPM uses a dash rather than zero. In Tier Bands, also show the selected rating as a plain value such as **S22.8**, because the band heading does not identify it. Do not prefix these values with “Estimated” or “Est.” Keep Provisional, Limited data, contributor counts and source/uncertainty explanations inside chart details; do not repeat them as badges or warning icons throughout the board. Keep the Unrated section for charts without a rating, without a redundant per-item status label.

**Readable list** is the alternate presentation: small jacket, song title with BPM beneath it, colored difficulty badge, selected rating when needed and optional personal score/plate. Use the same badge colors and meanings as the grid and other tabs. Align repeated numeric values and keep row structure consistent. Place calculation/evidence metadata in the existing detail disclosure rather than on every row.

Inside selected-chart details, order information by the task:

1. Song identity and chart identifier.
2. Current metric and score goal, when applicable.
3. Official level versus selected rating, labeled simply **Difficulty**, plus a clearly defined difference where supported.
4. Personal score/plate and video action.
5. Calculation and evidence disclosure containing status, usable records, source, applicable interval and methodology.
6. Applicable what-if controls near their calculation.

A desktop dialog can remain a dialog for the first iteration; a permanent side pane is not necessary. On phones, use the available width and a stable close action. Preserve lazy loading, errors, Escape, focus handling and return to the originating chart. A blocking sheet must follow the [W3C modal-dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/); a nonmodal desktop panel must not trap focus.

## A quieter palette without moving other tabs

Use the same neutral charcoal base and desaturated sage accent across Tier Lists, Recommendations, Skill Radar, Player Comparison and Home. Share the background, surface, text, border, input, button, selected-state and focus tokens so Tier Lists does not look like a separate theme. Remove the lime background grid and reduce the green radial glow consistently. Song artwork, familiar colored difficulty bubbles and semantic data marks can carry more of the visual interest.

The following are candidate **dark-theme tokens**, not a completed theme implementation:

| Role | Candidate | Use |
| --- | --- | --- |
| Page | `#121417` | Neutral canvas |
| Raised surface | `#1A1D21` | Inputs, menus and dialogs |
| Main text | `#E6E8EB` | Titles, chart names and values |
| Secondary text | `#A5ACB5` | Supporting labels and metadata |
| Accent | `#A9C3A6` | Restrained identity, selected marker and focus |
| Selected surface / text | `#2D382F` / `#DCE9D9` | Small active controls |
| Structural separator | `#343941` | Decorative boundaries |
| Control boundary when needed | `#727A85` | Identifying interactive fields |

Calculated solid-color contrast examples are 15.03:1 for main text on the page, 7.38:1 for secondary text on the raised surface, 9.72:1 for selected text on its fill and 3.90:1 for the control boundary against the raised surface. These calculations do not certify every rendered component, opacity combination, hover state or semantic color.

Keep contrast high enough to read; reducing saturation should not mean fading labels. Use 14–16 px for ordinary controls and readable chart titles, approximately 12–13 px for secondary labels, and 16 px editable fields on phones. Reduce boxes and repeated borders before reducing spacing or type size.

### Modern visual character

Use stronger title typography, compact raised mode selectors, consistent 8–12 px corner radii and restrained depth on interactive elements. Give the selected navigation item a soft fill. Keep tier sections unboxed, with a plain difficulty heading and a fine horizontal separator; omit the vertical accent before each heading. A very faint accent wash near the page top adds character while the main reading surface stays neutral.

Let square song jackets provide most of the color and personality. Display the original artwork plainly, without added gradients, shine, tint/filter effects, shadows or hover lift/zoom. The preview uses flat jacket placeholders; production should use the real artwork without decorative treatments. Refine the difficulty badges with a compact softened shape, subtle edge highlight and readable number, preserving red/green/gold meaning. Keep count labels, repeated “Estimated” wording and evidence-status badges out of the browsing surface.

Keep hover/focus feedback on the surrounding controls and title: a clearer control edge, visible keyboard focus and a restrained title-color change. The artwork itself stays static and unmodified. Avoid continuous animation and respect reduced-motion preferences. Apply the same typography, surface depth, corner treatment, controls and accent behavior across all tabs without moving the other tabs' objects. This is a visual refinement of the approved structure, with no additional controls or information panels.

Keep chart-type colors, Singles/Doubles radar traces and tier semantics consistent wherever they appear. Preserve the familiar colored difficulty bubbles with their numeric identifiers. Separate these shared semantic tokens from the shared brand accent: green indicating a Doubles chart keeps the same meaning on every tab even when the surrounding chrome uses a softer sage accent.

| Other area | Allowed minor treatment | Placement to preserve |
| --- | --- | --- |
| Shared header | Shared palette and compact Menu drawer; global player management | Existing brand identity and page-content width |
| Recommendations | Shared palette and grade/plate colors; remove local player selector | Title, view/mode controls and recommendation/Top 50 content |
| Skill Radar | Shared palette and score colors; use global player | Heading, radar and strength/weakness groups; retain sync context |
| Player Comparison | Shared palette and score colors; chooser triggers replace dropdowns | Section navigation, two independent player cards, BPM and score sections |
| Home | Shared palette and short optional player setup | Existing hero/destination structure with minimal placement changes |

A single `--acid` edit will not complete this change: lime RGBA values and green surfaces are also hardcoded in shared styles. Keep the palette pass bounded to identified brand/decorative uses. The interactive preview follows the host's light or dark appearance to demonstrate both neutral surfaces; a new app-wide theme switch is not part of this proposal.

## Feature preservation contract

| Capability | Required behavior after redesign |
| --- | --- |
| Modes | Keep Singles, Doubles and Co-op and their existing independence. |
| Metrics | Keep Scoring, Clearing and Pumbility; always identify the selected metric. |
| Difficulty badges | Preserve the familiar color and number in grid and list; their shape and finish may evolve. Use the same color meanings across tabs, without mode letters or redundant official-difficulty prose in the badge. |
| Per-chart BPM | Keep BPM visible beneath every chart title, including the four-column mobile grid. Preserve single-value/range semantics and show a dash for unavailable BPM. |
| Goals | Keep every available goal and the current SSS+ default. Pumbility must retain goal selection because its Scoring component depends on it. |
| Co-op | Scoring only, estimated grouping only; “level” becomes player count. Do not display an official-level delta for Co-op. Explain incompatible choices. |
| Search, level and BPM | Preserve existing matching semantics, including Only 20+ and whole-range BPM behavior. Changing layout must not change the chart set. |
| Grouping | Preserve the current difficulty and Tier Bands meanings, thresholds, precision and ordering. Label the grouping choice **Difficulty**; display headings/values without repeated “Estimated” or “Est.” prefixes. Tier Bands use the difference from the official midpoint, not simply the integer official level. |
| Player scores | Use one saved personal player across tabs; preserve scores, actual plates, sync time and loading/unavailable states. Missing scores remain unknown, not zero. Apply shared semantic colors. |
| Evidence | Preserve data status, source, uncertainty, contributor information, detailed calculation and applicable what-if controls in chart details. Provisional/Limited data badges and warning icons are not part of the browsing surface. |
| State | Preserve per-mode filters and mode/metric/goal behavior. Centralize player URL semantics according to the expanded plan, including temporary shared links and meaningful Back behavior. |
| Trust states | Retain page-level saved-data/freshness warnings, goal-unavailable states, Unrated grouping and missing-score behavior. Chart evidence distinctions remain accessible inside details. |

Implementation references: [goal visibility](C:/Users/jfung/Downloads/piu_misgrade_bundle/app/tier-list/page.tsx:708), [goal projection](C:/Users/jfung/Downloads/piu_misgrade_bundle/lib/scoring-variants.ts:82), [metric/group sorting](C:/Users/jfung/Downloads/piu_misgrade_bundle/lib/tier-metrics.ts:121), [per-mode filter state](C:/Users/jfung/Downloads/piu_misgrade_bundle/app/tier-list/page.tsx:720), [URL state](C:/Users/jfung/Downloads/piu_misgrade_bundle/app/tier-list/page.tsx:793), and [existing dialog handling](C:/Users/jfung/Downloads/piu_misgrade_bundle/app/tier-list/page.tsx:552).

## Options and delivery order

| Option | Advantage | Cost / tradeoff | Recommendation |
| --- | --- | --- | --- |
| Palette and type only | Smallest structural change; addresses visual intensity | Does not solve the long control stack or image-only identification | Include as the shared foundation, not the complete solution |
| Quiet board | Keeps the existing tier-list character while improving orientation and reading | BPM/display configuration and shared player management require a labeled action | Preferred direction |
| Filter rail with readable rows | Efficient for repeated desktop filtering and comparing aligned values | Uses horizontal space; requires a mobile disclosure; can make a simple task feel more elaborate | Keep as an alternative if task testing supports it |

Suggested implementation sequence:

1. **Visual foundation:** apply one shared neutral/brand palette across all tabs, remove decorative lime and improve Tier Lists typography/focus. Keep other tabs' geometry unchanged and retain shared semantic bubble colors.
2. **Tier Lists structure:** consolidate primary controls; disclose BPM/display; use the shared player; add active-filter feedback and reset; preserve state and numerical behavior. Follow the expanded plan for the shell/player migration.
3. **Chart browsing:** add named grid and readable list; reuse and reorganize details; verify keyboard and phone interaction.
4. **Validate and stop:** run the bounded functional checks and task comparison below. Repair proven regressions once, without an open-ended redesign/review cycle.

Use Tier Lists-scoped styles or a CSS Module for new layout. The installed Next.js guide supports CSS Modules and notes that global CSS affects every route. Deliberate shared changes cover the palette, score colors and approved shell/player migration. No ranking/model migration is needed for this UI proposal.

Do not add saved filter presets, chart collections, search operators, custom tier editing, drag-and-drop, new dashboards or analytics cards in this work. The requested “S” is a naming correction: use **Tier Lists** as the visible tab/page label.

## How to judge whether it is better

Before implementation is considered complete, verify:

- At least one complete chart is visible initially at 390 × 844 in the normal loaded state; warnings remain visible when needed.
- Controls and chart identities reflow at 320 px and with zoom; do not rely on reducing labels to fit. [W3C reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html)
- Mobile grids retain four columns at 320 px and 390 px, with readable numeric badges, nonoverlapping tile targets and full song identities available through details/tooltips. Ordinary browsing controls retain their existing text size.
- Normal text meets 4.5:1 contrast; qualifying large text meets 3:1. [W3C text contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)
- Required control/state cues meet applicable 3:1 non-text contrast. Decorative separators need not all meet that threshold. [W3C non-text contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html)
- Pointer targets meet WCAG's 24 × 24 px minimum or its defined exceptions. Use **44–48 px hit areas as the project comfort target** for primary touch controls and close/remove actions. [W3C target size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)
- Difficulty bubbles retain readable numbers and accessible mode/level names. Selected state does not depend on color alone; keyboard focus stays visible. Evidence status is readable in details. [W3C use of color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html)
- Active filtering is understandable with panels closed. Users can remove one criterion and reset filters without reopening a panel.
- Search, filtering and presentation changes preserve the relevant state; opening/closing chart details returns to the same chart and list position.
- Every preservation-contract item works, especially Pumbility goal selection, Co-op applicability, Unrated and missing-score behavior.
- Screenshot comparison confirms the same palette across all tabs and preservation of content placements outside Tier Lists, apart from the approved shared header, player controls and optional Home setup.

Run a small formative comparison with approximately 6–8 representative players, including phone users, desktop users, experienced jacket recognizers and less familiar browsers. Counterbalance which UI they see first. Use the same chart set and tasks:

1. Find a named chart without recognizing its jacket.
2. Browse Singles S23, then compare the selected Scoring and Pumbility contexts at SSS+.
3. Limit charts by BPM, close the controls and explain which filters are active.
4. Add personal scores, find a score/plate and identify a missing-score state.
5. Inspect a provisional chart's evidence and return to browsing.
6. Clear filtering, switch to Co-op and choose a player count.

Record completion, wrong turns, time, backtracking and perceived visual comfort separately. A useful pilot target is at least 80% unassisted success on each core task, with no lost features or misleading context. This is a project decision threshold, not a statistically conclusive claim from such a small sample. If experienced users lose too much overview, retain an explicit dense jacket option rather than forcing all users back to tiny labels.

## Scope and incidental observations

This work produces a proposal and an interactive design preview. It does not implement or deploy the redesign. The preview uses illustrative values and jacket placeholders; it demonstrates search, BPM filtering, display choices, player overlay and selected-chart disclosure. It is not a complete replica of the app's rating calculations, per-mode state, API behavior or production modal sheets.

The live site showed a failed-daily-refresh warning while displaying saved data. That is a proven visible status, not a diagnosis of the refresh system, and was not repaired. The production/local score-goal difference is recorded above and was not reconciled. No runtime bottleneck was proven. These observations do not block reviewing the redesign proposal.

## Proposal verification

The preview was rendered and inspected in the browser. Checks covered search, applying/removing BPM filters, reset, grouping/layout selection, Pumbility goal visibility, Clearing/Co-op applicability, player-score overlay, chart details and focus return. Layout checks covered desktop, 390 px and 320 px browser widths; no horizontal overflow or clipped controls was detected in the inspected states. Both light and dark appearances rendered, the browser reported no captured warnings/errors, and inline JavaScript passed a syntax check. The revised grid and list were also checked for colored level bubbles, removal of repeated official/estimated wording and evidence badges, and evidence remaining in a closed disclosure. Singles, Doubles and Co-op bubble colors rendered correctly. The fragment is approximately 30 KB and contains no external data requests.

The modern visual refinement was also inspected in light and dark appearances and at 390 px and 320 px widths. No horizontal overflow or clipped controls was detected in the inspected states. Colored badges still contain just the number/player count, tier counts remain absent and search/reset work in the alternative list layout. No captured browser warnings/errors appeared.

The compact mobile revision was checked at 390 px and 320 px: grids retained exactly four columns, every chart displayed BPM, badge numbers stayed within the layout and titles used a two-line clamp. Opening a truncated chart restored its full name in details. Desktop retained its wider responsive grid. No captured browser warnings/errors appeared in these checks.

The subsequent title-spacing correction removed the mobile title's reserved two-line height. At 390 px and 320 px, both one-line “Odin” and two-line “Kokugen Kairou Labyrinth” had a measured 2 px gap before BPM, four grid tracks and no horizontal overflow in the inspected board. Spare row height remains below the metadata. No captured browser warnings/errors appeared.

No application code, ranking calculation, deployment or user data was changed by this investigation. The mockup checks do not constitute production accessibility certification or validation with players.
