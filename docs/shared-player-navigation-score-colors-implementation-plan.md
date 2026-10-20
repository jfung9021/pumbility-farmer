# Shared player, navigation and score-color implementation plan

21 October 2026 · researched plan · implemented locally; not deployed

The local app is available at http://127.0.0.1:3000. See [implementation and verification notes](ui-redesign-local-implementation.md) for delivered scope and checks.

## Recommended direction

Use **Tier Lists** as the visible tab/page label throughout the app; keep the existing `/tier-list` route. This is a naming correction only.

Use one browser-saved personal player across Tier Lists, Recommendations and Skill Radar. Introduce a short optional setup on Home, a shared player chooser and a labeled Menu button opening a navigation drawer. Tier Lists stays public. Recommendations and Skill Radar show an in-page player gate until a player is selected. Keep the other pages' existing content arrangements, except for removing their repeated player selectors and consolidating player management in the shared shell.

Use the same neutral surfaces and restrained sage accent across all pages. Add familiar grade and plate colors as small semantic accents wherever real player results appear. Continue the approved Tier Lists direction: four mobile tile columns, colored numeric difficulty badges, visible BPM, and concise tier headings without counts, vertical accents or repeated “Estimated” wording.

Render chart artwork plainly and statically: no added gradients, overlays, shine, color filters, shadows or hover lift/zoom. Preserve the colored difficulty badge and visible focus indication on the surrounding interactive tile. The preview uses flat placeholders; production uses the unmodified jacket images.

This extends the [Tier Lists redesign proposal](tier-list-ui-redesign-proposal.md). It supersedes that proposal's earlier local player controls and unchanged top navigation. It does not authorize changing ranking calculations, eligibility rules or the arrangements of radar plots, recommendation lists and comparison sections.

## Investigation and concrete constraints

Three subagents investigated shared-player architecture, onboarding/navigation usability, and grade/plate semantics and renderers. Their findings were combined with source inspection, primary-source research and a targeted check of the attached BPM-spacing example.

| Existing behavior found in the checkout | Consequence |
| --- | --- |
| [Root layout](../app/layout.tsx) has no player provider. [SiteHeader](../app/_components/site-header.tsx) renders ordinary route links. Each personal page owns its player query, selection and roster. | Put durable player context above the pages; keep route links as links. The UI calls them tabs, but they are separate routes. |
| [Recommendations](../app/recommendations/page.tsx), [Tier Lists](../app/tier-list/page.tsx) and [Skill Radar](../app/skill-radar/page.tsx) use the same recommendation-player public keys. | Reuse that identity; do not match players by display name. |
| Radar's directory is a filtered subset of that directory, with a current minimum of 100 submitted Phoenix 2 scores. Recommendations also have per-mode eligibility. | Selection and eligibility are separate states. A selected player must survive an unavailable feature or mode. |
| [Official Comparison](../lib/official-comparison.ts) uses a separate official-player index and `op_` keys; its current requirement is 200 official records. | Its left/right comparison subjects cannot silently become the saved personal profile. |
| Pages already have player-aware fetches, caches, refreshes and URL behavior. | Remove duplicated selection UI while preserving their data lifecycles and stale-response protections. |
| Local development uses Next handlers; deployed routes also use Python functions through [vercel.json](../vercel.json). | Use existing public contracts initially. A new local-only endpoint would not cover production. |
| The preview reserved two lines of title height even for “Odin.” | Remove the reserved height so BPM follows the actual title. This is a confirmed preview-layout issue, not a diagnosed production performance defect. |

These are implementation constraints, not evidence that the app's ranking model or backend is defective. No production runtime bottleneck was established by this investigation.

## Player experience

### Optional start on Home

On a first visit to `/`, place a compact choice in the existing hero/control area:

- **Choose player** opens the shared chooser.
- **Browse without a player** goes directly to Tier Lists.
- One short explanation: “Save a player in this browser for recommendations, skill radar and personal scores.”

Keep existing Home destinations available. Do not introduce tutorial slides, a mandatory full-screen setup or a redirect when somebody opens a Tier Lists link directly. Returning players see their saved name and a Continue action; visitors who skipped setup should not repeatedly see an expanded onboarding prompt. This applies [NN/g's guidance to teach in context and reduce onboarding overhead](https://www.nngroup.com/articles/onboarding-tutorials/); the specific composition is our design recommendation.

### Access rules

| Destination | No selected player | Selected player |
| --- | --- | --- |
| Home | Optional selection and public destinations | Saved-player summary and destinations |
| Tier Lists | Full chart browsing, filters and details; no personal-result blocks | Same browser plus personal scores |
| Recommendations | Keep route/filters; show “Choose a player to see recommendations” and one chooser action | Existing content when eligible; otherwise the actual mode/data requirement |
| Skill Radar | Keep route; show “Choose a player to see skill radar” and one chooser action | Radar when eligible; otherwise explain the current data requirement and available sync/change action |
| Player Comparison | Public comparison workflow with independent left/right subjects | Same workflow; saved personal player is not automatically mapped to official identities |

“Requires a player” is a product dependency, not an authentication boundary. Use “Choose player,” “Change player” and “Browse without a player.” Keep Recommendations and Skill Radar visible and actionable in navigation for guests. Their destination gate explains what to do; hidden destinations or unexplained disabled links would obscure the feature.

While saved state is hydrating, show a short neutral loading state in personalized regions to avoid flashing a guest gate. Directory failures should show Retry and preserve the saved key. Tier Lists's public content should remain available independently of player validation.

### One chooser and one management location

Put a Player block near the top of the shared drawer: current name or “No player selected,” Change/Choose, Browse without a player, and the existing sync action when applicable. Keep player names, score/player buttons and dropdowns out of page title bars and the top-right header. Long names in the drawer retain their full accessible text.

The chooser searches the existing recommendation-player directory. Typing edits a draft query; it must not clear the currently selected player. Choosing a result and confirming “Use player” commits it. Cancel retains the previous selection. Show loading, no match and directory-unavailable states explicitly. Do not invent an arbitrary username entry flow that the existing APIs cannot resolve.

Opening the chooser from a gated route resumes that route after selection. Opening it from the drawer closes the drawer first, so there is only one modal layer. Clear selection immediately removes personal results, cancels/invalidates outstanding client work and shows a gate on personal routes. Preserve route filters and display choices.

### Comparison subjects remain independent

Keep the two comparison cards in their existing places. Replace each embedded dropdown with a selected-name summary and **Choose** or **Change** action. Reuse the chooser's presentation and keyboard behavior, but supply the official directory and a target role of `comparison-left` or `comparison-right`.

A comparison choice changes only its corresponding URL subject. It must never overwrite the personal player. Do not auto-match a saved personal profile to an official profile by name; the current data has no verified cross-source identity mapping.

## Navigation recommendation and tradeoff

Use a compact shared header with a visible **☰ Menu** label and the existing brand. Player management lives inside Menu. The page heading remains clearly visible in the content. The drawer contains a flat list of Home, Recommendations, Tier Lists, Skill Radar and Player Comparison, with an unmistakable current-page treatment.

Start with an **overlay drawer on both desktop and mobile**. It replaces the top link row without permanently narrowing the content or shifting radar, comparison and recommendation objects sideways. A permanent desktop rail is a separate layout decision and is not needed in this scope.

This is a reasonable response to the requested simpler shell, with a real usability cost: hidden navigation reduces destination visibility and adds an action. [NN/g's original hidden-navigation study](https://www.nngroup.com/articles/hamburger-menus/) found discoverability and task costs; its [later hamburger-recognition research](https://www.nngroup.com/articles/hamburger-menu-icon-recognizability/) does not establish that a familiar icon eliminates those costs. Labeling the button “Menu,” keeping page identity visible and retaining Home's direct destination links are mitigations, not proof that a drawer will be faster. Validate one routine cross-page task before treating the shell as an improvement.

Use a navigation landmark with ordinary links and `aria-current="page"`; do not apply application-menu roles to website navigation. Follow [W3C navigation guidance](https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/examples/disclosure-navigation/) and the [modal-dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/): clear label, initial focus inside, contained keyboard focus, inert background, Escape/visible close, and logical focus return. Close on navigation and ensure the destination heading is reachable without focus remaining in a removed overlay.

Use 44–48 px comfortable hit areas for Menu, close and player actions. WCAG's [target-size criterion](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html) specifies a smaller minimum with exceptions; our touch target is a project choice. Check safe areas, keyboard exposure and [focus not being obscured](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html).

## Shared player implementation

### State and persistence

Add a small Client Component provider inside the existing Server Component root layout. Keep its state above route content so normal navigation does not create a fresh selection. The installed Next.js 16.3 guides support this pattern: [shared layouts](../node_modules/next/dist/docs/01-app/01-getting-started/03-layouts-and-pages.md) and [client providers in server layouts](../node_modules/next/dist/docs/01-app/01-getting-started/05-server-and-client-components.md). Read the installed guides again when implementing rather than assuming another Next version's behavior.

Persist a minimal versioned object in `localStorage`, for example:

```json
{"version":1,"playerKey":"<existing public player key>"}
```

Use a namespaced key such as `pumbility-farmer.player.v1`. Resolve display names and eligibility from current directory data; do not persist complete scores, stale eligibility, internal identifiers or credentials. A small separate onboarding-dismissed flag is sufficient if needed.

Required provider states are hydrating, guest, validating, ready, saved-player-missing and directory-unavailable. A fetch failure is not proof that a saved player disappeared. Keep the saved choice on failure and offer retry/change. Invalid stored JSON/version can fall back to guest with an unobtrusive recovery message. A blocked storage write should leave a working in-memory selection and explain that it will not be remembered.

Browser storage persists for that origin and browser profile, not every device or deployment URL, and storage access can fail. Update the current provider directly when choosing/clearing; listen to `storage` events for other open tabs. The event is not delivered back to its originating document. Sources: [MDN localStorage](https://developer.mozilla.org/en-US/docs/Web/API/Window/localStorage), [MDN storage event](https://developer.mozilla.org/en-US/docs/Web/API/Window/storage_event).

Use React context and a small storage adapter first. There is no demonstrated need for an additional store library or a new global score cache. If an external store is selected, its server/client snapshot contract must follow [React's useSyncExternalStore documentation](https://react.dev/reference/react/useSyncExternalStore).

### Eligibility and data lifecycle

Load the shared recommendation directory once per app session. Keep feature capability separately: Recommendations by mode, Radar through its existing filtered index or authoritative response. Lazy-load Radar eligibility when needed; unknown/loading/error must not be collapsed into ineligible. Selecting a player with insufficient Radar data must not clear them from Recommendations or Tier Lists.

On an effective-player change, clear the previous player's visible results and close any open result dialogs immediately. Abort active fetches and polling where supported. Retain player key, mode and a request revision in response checks before committing data. Poll cancellation need not cancel a server job, but late results must not render as the new player. Keep the existing result-cache dimensions and page-specific fetch logic; move selection ownership first.

Refresh/sync belongs to the active player and relevant source. Retain freshness, progress and unavailable states. Do not start the same refresh from every mounted consumer. Do not substitute official-comparison refreshes for submitted-score refreshes.

### Existing player URLs and browser Back

Preserve shareable player links without silently overwriting somebody's saved preference. Use one centralized policy:

| Input/action | Effective player | Saved preference |
| --- | --- | --- |
| Route without a player override | Remembered player, or guest | Unchanged |
| Valid explicit `?player=B` while A is saved | B for this shared-link context; visibly identify the temporary player | A remains saved |
| Invalid explicit player key | Show unresolved-link state and Choose/Use saved player action | Unchanged; never silently display A as though it were B |
| “Use this player” or a normal chooser commit | Chosen player | Persist chosen key and remove the obsolete temporary override |
| Browse without a player | Guest; clear active URL override as well | Remove saved key |
| Browser Back/Forward | Recompute the explicit URL context | Do not adopt an override merely because history changed |

Carry an active temporary shared-link player through links between Tier Lists, Recommendations and Skill Radar with one navigation helper. Carry only the player override, not another page's mode/filter parameters. Home/Comparison may show the temporary profile in the shared shell without applying it to comparison subjects. Offer **Use this player** and **Use saved player** centrally; do not create per-page player dropdowns to resolve the distinction.

Keep this policy in the shared provider/URL synchronizer rather than retaining three competing page initializers. Use a small `PlayerUrlSync` inside Suspense if it uses `useSearchParams`; the installed [Next.js useSearchParams guide](../node_modules/next/dist/docs/01-app/03-api-reference/04-functions/use-search-params.md) requires the relevant production static-rendering boundary. Keep the provider and public page content outside that small boundary. A production build is needed to verify it; development alone is insufficient.

## Grade and plate appearance

### Verified palette and its limit

The live CSS served by [PIU Scores Welcome](https://piuscores.arroweclip.se/Welcome), [Charts](https://piuscores.arroweclip.se/Charts) and its [Phoenix 2 calculator](https://piuscores.arroweclip.se/PumbilityCalculator/phoenix-2) exposes a consistent grade/plate palette, with the calculator binding grade tokens to named headings. The following are those verified application values.

| Plate | Color | Grade using the same color |
| --- | --- | --- |
| PG · Perfect Game | `#7FC7EF` | SSS+ |
| UG · Ultimate Game | `#62B4E8` | SSS |
| EG · Extreme Game | `#F2C233` | SS / SS+ |
| SG · Superb Game | `#EDBB2E` | S / S+ |
| MG · Marvelous Game | `#D9DDE1` | AAA+ |
| TG · Talented Game | `#C4C9CE` | AAA |
| FG · Fair Game | `#DE863A` | AA / AA+ |
| RG · Rough Game | `#CE6A2E` | A / A+ |
| — | `#34BE6A` | B / C / D / F |

These are exact PIU Scores values, not a publisher-issued hex specification. Official `piugame.com` requests timed out, and this checkout has no traced official grade/plate artwork. Use this as a concrete familiar palette, then compare it with current Phoenix 2 official result/record artwork before claiming an exact cabinet match. That verification is the only unresolved provenance item; it does not block the architecture or spacing proposal.

Grade and plate are independent fields. The colors above describe two separate mappings, not rules for deriving a plate from a grade. Keep actual grade letters and plate codes/names visible. In compact Tier Lists rows, a grade-toned numeric score plus an independently colored plate code preserves density without adding another line. Details and Top 50 cards can show the existing explicit grade and full plate label.

Use color on small glyphs, chip edges or markers where exact reference hues do not meet text contrast on the final background; keep the label readable. Check both light and dark surfaces. Color supplements text and must not become the only indication of grade, plate or comparison winner. See [W3C use of color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html) and [text contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html).

### Shared module and component

Add `lib/score-appearance.ts` with literal colors, closed grade/plate sets, full-name/code normalization and neutral unknown handling. It must be usable in both client rendering and server image export. Use [phoenix2_pumbility.py](../phoenix2_pumbility.py) as the existing grade/plate authority; derive grades from exact scores only where the payload lacks them. Do not introduce Phoenix 1 thresholds into Phoenix 2 views. Never infer an earned plate from a score.

Add `app/_components/player-result.tsx` with compact/full variants and a complete accessible description. Preserve a valid zero, missing record, unknown plate, loading and unavailable as different states. Current score projections omit broken/invalid records, so an absent score must not receive a fabricated failure grade.

The [Tier score contract](../lib/local-tier-scores.ts) already supplies score and plate code. No API/schema expansion is needed solely to add color. [Top 50 generation](../piu_recommendations.py) already provides grade, plate and plate code from its retained record.

| Surface to migrate | Scope |
| --- | --- |
| [Tier Lists](../app/tier-list/page.tsx) `PersonalChartScore`, tiles, detailed cards and chart dialog | One compact/full result component; no personal block for guests |
| [Recommendations](../app/recommendations/page.tsx) Top 50 cards and details | Color existing grade and actual plate independently |
| Recommendation goals | May reuse colors, but keep “Goal” explicit so a target is not presented as an earned result |
| [Skill Radar](../app/skill-radar/page.tsx) contributors and suggestions | Same score/plate semantics as Tier Lists |
| [Official Comparison](../app/official-leaderboard-comparison/page.tsx) scores and contributors | Grade tone may follow raw score; retain higher-score arrow/text; do not fabricate earned plates |
| [Top 50 image export](../app/exports/top50/route.tsx) | Use the same literal token module in inline export styles; browser CSS variables alone are insufficient |

Official Comparison's `assumedPlate` is a calculation assumption (SG below perfect, PG at perfect), not an observed player result. Keep it in its existing calculation context; do not display it as an earned plate badge. This is a concrete semantic constraint in [official_player_comparison.py](../official_player_comparison.py) and [lib/official-comparison.ts](../lib/official-comparison.ts).

## BPM and tile spacing decision

Use a natural vertical stack: square jacket → title of up to two lines → BPM → optional personal result. A one-line title consumes one line; a two-line title consumes two. Keep a small consistent title-to-BPM gap. Do not reserve a second title line or use automatic vertical spacing to push BPM down.

Keep jacket tops aligned across the row. Any unused row height belongs after the complete metadata group. Slightly staggered BPM baselines are the correct tradeoff: they keep each chart's information together and avoid the detached appearance in the supplied screenshot. Do not introduce masonry, absolute positioning or smaller type to fix this.

The preview has been corrected by removing its mobile title `min-height`; the two-line clamp and four-column grid remain. Read-only browser measurements at 390 px and 320 px confirmed a **2 px title-to-BPM gap** for both one-line “Odin” and two-line “Kokugen Kairou Labyrinth,” exactly four tracks, and no horizontal overflow in the inspected board. No captured browser errors/warnings appeared. This is prototype verification only; the production tile layout still needs implementation.

## File map and delivery order

| Phase | Files / work | Exit condition |
| --- | --- | --- |
| 1. Shared identity | New `lib/player-profile.ts`, provider, URL synchronizer and chooser; wire `app/layout.tsx` | Remember/clear/restore and temporary link policy work with existing public keys; no per-page data behavior changed yet |
| 2. Shell and setup | `app/_components/site-header.tsx`, new navigation drawer and player gate; small Home change | Menu and optional setup work by keyboard/touch; direct public links bypass setup |
| 3. Personal pages | Migrate Tier Lists first, then Recommendations and Radar | Repeated selectors removed, one player applies to all, gates and eligibility work, old responses cannot show after a switch |
| 4. Comparison chooser | Keep cards and sections; replace selector controls with chooser triggers using official-directory roles | Left/right choices never overwrite saved personal player |
| 5. Shared visual semantics | Score appearance module/component, listed renderers and export; bounded shared palette pass | Same score meanings/colors everywhere, export matches, other content placements retained |
| 6. Tier Lists polish | Apply approved Quiet board controls and tiles, including natural BPM flow | Four mobile columns, clear titles/BPM, required filters/rating behavior preserved |
| 7. Verify and stop | Relevant tests/build plus one visual and interaction review | Acceptance criteria below pass; repair only a demonstrated regression within scope |

Phases 5 and 6 can be delivered separately after the shell migrations. Do not combine this work with a new account system, arbitrary username lookup, shared backend session, identity-source merger, rating-model rewrite, permanent desktop sidebar, new chart analytics or redesign of the other pages' content.

## Bounded verification and acceptance

Production work is complete when these directly relevant checks pass:

1. Guest can open Tier Lists directly, browse and inspect charts without a chooser. Guest Recommendations/Radar show one clear route-local gate and perform no player-specific fetch until selection is ready.
2. Choose A once; navigation, reload and a new tab use A. Change to B or clear during a slow request/refresh; A's results never appear under B or guest. Canceling a draft search leaves A selected.
3. A selected player lacking Radar or per-mode recommendation data stays selected. Directory outage retains the saved key. Blocked storage still allows the current session to work.
4. Saved A plus shared URL B, invalid URL player, Back/Forward, adopting B, restoring A and clearing behave according to the central policy. Comparison left/right changes never alter the personal preference. Another browser tab's saved-player change is reflected appropriately, while an explicit temporary override remains explicit.
5. No per-tab player dropdown remains. Home setup is optional. Drawer navigation and chooser close/focus correctly, work with Escape/keyboard and fit 320/390 px screens with the keyboard open.
6. All eight plate aliases normalize; grade boundary fixtures match the existing Phoenix 2 backend; zero/null/unknown stay distinct. Grade and plate are never inferred from each other. Existing comparison assumptions remain labeled as assumptions.
7. Check Tier tiles/dialog, Top 50 cards/dialog, Radar contributors/suggestions, comparison scores and one rendered Top 50 PNG. Verify readable semantic colors, long titles and no clipping at desktop and 320/390 px widths. The same shared page palette appears across routes.
8. Mobile Tier grids keep four columns; title clamp does not reserve empty lines; BPM sits directly below the visible title, including ranges. Details expose full title. Tier headings have no repeated Estimated prefix, chart count or leading vertical accent. Difficulty badges retain number and familiar color.
9. Existing metric/goal/mode/filter behavior, Co-op applicability, missing-score handling and freshness warnings pass the original redesign preservation contract. Other pages keep their principal object placements except the specifically approved header/player controls.

Run `npm run typecheck`, `npm run build` and the relevant existing frontend tests (`npm run test:frontend`, or targeted test files while developing). Grade normalization/derivation merits focused fixture tests because it duplicates backend boundary semantics. Existing backend tests such as `tests/test_phoenix2_pumbility.py` and `tests/test_tier_scores.py` are the parity reference; run affected backend tests if those contracts or fixtures change. No broad backend refactor is needed for frontend appearance.

Perform one general visual/interaction review after implementation. If it identifies a reproducible regression caused by these changes, make one focused repair and rerun the affected verification. Do not restart general review or expand into unrelated cleanup. A small formative check of choosing/skipping a player, switching features and identifying a score should assess the drawer's usability tradeoff; do not claim a performance improvement from the mockup alone.

## Deliverables and remaining limits

The investigation produced the source/research audit, consolidated plan, verified PIU Scores palette and renderer coverage. The later user request authorized local implementation; the app now includes the shared player/Menu flow, page migrations, score colors and Tier Lists redesign. See the implementation notes for verification. Player data and rating calculations were not changed, and no deployment was performed.

The grade/plate palette still needs comparison against traceable current official artwork before being described as an exact in-game specification. Existing production refresh warnings and production/local goal differences recorded in the earlier proposal were not investigated or repaired. They are outside this planning task.
