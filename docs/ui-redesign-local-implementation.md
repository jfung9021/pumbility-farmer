# Local UI redesign implementation

21 October 2026 · local only · no deployment

Open [the local app](http://127.0.0.1:3000). Menu reaches Home, Recommendations, Tier Lists, Skill Radar and Player Comparison. It contains the sole personal-player chooser, score-sync link and browse-without-a-player action. Test player selections were cleared after browser verification.

## Delivered

- One browser-saved personal player across routes. Draft selection is explicit and cancelable. Temporary shared links preserve the saved preference; storage events update other tabs.
- Optional Home setup. Tier Lists stays public; Recommendations/Radar show a chooser gate without a player. Existing feature eligibility and data-unavailable states remain intact.
- Shared modal Menu and reusable chooser with keyboard/Escape behavior. Header contains brand and Menu only; repeated page player dropdowns and the top-right score/player button are absent.
- Independent official-comparison subjects, with Choose/Change buttons. Their official identities and URL state never overwrite the personal player.
- Shared Phoenix 2 grade/plate appearance in live results, details, Radar and Top 50 PNG export. Grade and plate remain independent; calculation assumptions are not presented as earned plates.
- Neutral charcoal surfaces and restrained sage chrome across routes, preserving semantic chart/radar colors and other pages' principal content placements.
- Tier Lists with compact mode/metric/goal controls, disclosed BPM/Display settings, active filters/reset, named grid and readable rows. Existing calculations, per-mode filters, goal projection and grouping semantics remain.
- Four mobile chart columns; titles use up to two visible lines without reserved blank height. BPM follows the actual title. Numeric colored badges remain; per-tier chart counts and leading accent lines are absent.
- Plain, static chart artwork: no added image gradients, filters, shadows or lift/zoom.

## Verification

- Typecheck and the final optimized production build passed, including TypeScript/static-page checks.
- Initial complete frontend run passed 250 tests and identified 11 source assertions tied to the intentionally replaced UI. The affected frontend/API and profile tests passed **72/72** after updating those assertions.
- Personal-result/radar targeted checks passed **17/17**; official comparison/export checks passed **20/20**. These overlap the broader suite and must not be added into one total.
- The actual Satori/resvg export route rendered a 2312 × 4096 PNG fixture covering every grade/plate color, zero and unknown values. Known grade and plate fields rendered independently.
- Browser checks covered global choice, saved player across Recommendations/Radar, available/unavailable Radar profiles, independent official comparison selection, temporary URL B with saved A, restoration/clearing, and guest gates.
- At 390 px and 320 px, implemented Tier Lists had four grid tracks, no horizontal overflow and a 2 px title-to-BPM gap. A complete first chart was visible within 844 px height at 390 px. Computed artwork had no background image, filter, shadow or transform. Search/reset, readable rows, chart details/evidence and focus return were also verified.
- Review was bounded to these changes. No backend/model rewrite, deployment or unrelated cleanup was performed.

## Limits and incidental observations

The grade/plate palette uses verified PIU Scores values. Exact publisher/cabinet-art color matching remains unverified; grade thresholds and earned-result semantics are unchanged.

Local datasets have older sync/model timestamps. Some selectable players lack a generated Radar artifact; its existing unavailable state remains and retains the shared player. A player with a generated local Radar profile was also verified.

The build reports two broad-file-pattern warnings in the unchanged local official-comparison data loader. They do not fail the build and were not modified. A long unknown plate string remains cramped in the existing export layout; it was observed during fixture verification and was not expanded into unrelated layout work.

## Release integration

The release candidate applies these UI changes to current production `main` (`70f59f9`), rather than publishing the older local checkout wholesale. It preserves all current scoring goals, simplified evidence copy, publication-generation recovery, refresh continuation and freshness notices. No backend, rating-model or unrelated local work is included.

The integrated production build and typecheck passed. The current frontend suite passed 313 of 314 tests initially; its remaining source assertion expected the intentionally removed browsing warning badge. That assertion now checks that evidence remains in chart details and browsing stays concise; all nine affected evidence tests passed on rerun.
