# Tier title overlays and About page

23 October 2026 · local implementation

## Scope and plan

1. Put compact tier chart titles over the top of the artwork, using white two-line text and a dark gradient fading into the image. Preserve full accessible song names, difficulty badges, BPM, estimates, personal scores and readable-list behavior.
2. Move general explanatory dropdown content into an always-expanded `/about` page with a table of contents and anchored sections for setup/data, Recommendations and skill ratings, Tier Lists and calculation methods, Skill Radar, and Player Comparison. Retain current and historical method variants with clear labels.
3. Add About to the shared menu and small contextual links from each feature. Keep chart-specific details and evidence alongside individual charts.
4. Verify TypeScript and the production build, run the existing frontend checks, and inspect desktop/mobile chart overlays and About navigation. Perform one bounded integration review and stop once these criteria pass.

## Delegation

- Tier overlay subagent: compact chart markup and responsive artwork overlay styles.
- About migration subagent: About page, shared navigation and general description migration outside Tier Lists.
- Root agent: Tier Lists explanation links, integration and verification.

Existing uncommitted work is preserved. No model changes, publishing or deployment are part of this request.

## Completed and verified

- Delivered the chart-title overlay, dedicated About page, shared-menu destination, and contextual links across all five feature pages. General explanations are always expanded on About; per-chart evidence and details remain with charts.
- TypeScript and the optimized production build passed, including the new `/about` route.
- Frontend suite: 263 tests ran; 259 initially passed and four assertions still expected explanatory copy on its previous page. Those assertions now check the About page. The affected frontend/API file then passed all 70 tests. No other frontend test failures remain from that run.
- Browser checks covered desktop and 390 px title overlays, preserved scores/badges, readable rows, opening and expanding chart evidence, tier-band links, all 12 About anchors, expanded About content, mobile overflow, and the About menu destination/current-page state.
- One bounded integration review completed. No unresolved blocker. The build retains two pre-existing broad file-pattern warnings in `lib/local-official-comparison.ts`; that loader was not changed.

## Production release preparation

The follow-up request authorizes pushing, merging, and deploying. The release branch starts from current production main `8f20688`, preserving its refresh recovery and concise chart-evidence implementation. Only the title/About changes are transferred; the original working directory and unrelated local work remain intact.

The About descriptions also retain current level-20 spread behavior, the published Clearing record minimum (100 in current generations, 200 in earlier ones), and the separate ten-record warning boundary. Detailed method prose is on About; current concise chart evidence stays on each chart.

Release verification: TypeScript passed, the optimized production build passed, and all **314 frontend tests passed**. Only UI source, the About stylesheet, migrated-copy assertions, and this plan are included in the release.
