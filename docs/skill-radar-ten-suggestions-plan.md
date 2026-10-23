# Skill Radar: ten suggestions with More

23 October 2026

## Requested behavior

Each Singles/Doubles BPM bucket supplies up to ten qualifying chart suggestions. Opening a bucket initially shows five. A More button below the list reveals the remaining five, or however many are available. Buckets with five or fewer suggestions have no More button.

## Implementation

1. Increase the backend suggestion cap to ten. Retain the existing mode/BPM filters, target official folder, higher-folder backfill, deduplication, personal score/plate attachment and final SSS+ Pumbility ordering. The five-score radar calculation stays unchanged.
2. Advance the profile cache version so cached five-suggestion profiles are not reused. Keep the numeric method version and response fields compatible. Allow zero through ten suggestions in the frontend validator while preserving privacy and ordering checks.
3. Mount a suggestion-list component when a bucket opens. It displays five rows initially, expands through More, and offers Show less afterward. Collapsing/reopening a bucket or replacing the player/profile resets the list to five. Use contextual accessible button labels, an associated list ID and expanded state, with a touch-friendly control at the bottom.
4. Update the About explanation and current implementation documentation to describe ten available suggestions with five shown initially.
5. Verify backend selection/backfill/limits, unchanged radar ratings and contributions, client acceptance of ten and rejection of eleven, cache invalidation, sparse buckets, desktop/mobile More behavior and reset behavior. Run relevant Python/frontend tests, TypeScript and the production build. Perform one bounded integration review, then stop implementation once these criteria pass.
6. Push an isolated branch based on current main, create and merge a PR after Vercel preview checks pass, and verify the production deployment and live suggestion behavior. Preserve unrelated local work.

## Acceptance criteria

- At most ten unique, eligible suggestions per bucket, ordered by calculated difficulty.
- Five visible initially; More reveals all remaining suggestions, up to ten total.
- No More button for zero through five available charts; sparse six-to-nine lists reveal only existing charts.
- Five-score radar values, contributions and group rankings remain unchanged.
- Collapse/reopen and player/profile replacement reset the visible list to five.
- Existing score/plate display, chart links and keyboard/touch access remain functional.
- Relevant verification passes, PR is merged and production is Ready.

## Implementation verification

- Backend: all 33 radar/runtime tests passed, including ten-chart selection, higher-folder backfill, sparse buckets, personal score/plate attachment, unchanged five-score ratings/grouping, and cache invalidation.
- Frontend: all 314 existing frontend tests passed. The radar contract accepts zero, five, six, nine and ten suggestions and rejects eleven, invalid ordering and invalid folder bounds.
- TypeScript and the optimized production build passed.
- Browser: confirmed five rows initially, More and keyboard Enter reveal ten, Show less restores five, and collapse/reopen resets to five. All ten rows retain score/plate and video-link elements. At 390 px the control is 44 px high and the page has no horizontal overflow.
- A selected local profile was regenerated from existing cached inputs for browser verification: no score fetches and no model refits.
- One bounded integration review completed; no unresolved implementation blocker.
