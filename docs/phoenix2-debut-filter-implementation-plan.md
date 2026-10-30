# Phoenix 2 debut chart filter

Add an off-by-default **New Phoenix 2 charts only** toggle to the tier list.
Use the chart catalog's `originalMix === "Phoenix2"` metadata. This includes
chart debuts and excludes older charts imported from Rise, rerates, and older
charts that happen to have no Phoenix 1 submitted scores.

## Implementation

1. Generate `config/phoenix2-debut-charts.json` from the authenticated upstream
   Phoenix 2 chart catalog. Store only sorted debut UUIDs plus catalog source,
   timestamp, count, and hash. No scores or player data belong in this artifact.
2. Preserve optional `originalMix` through daily chart capture, submitted and
   official tier builders, hybrid metadata replacement, and the shared public
   chart projection. Future refreshed charts then carry explicit metadata.
3. In the browser, prefer explicit `originalMix`; use the generated UUID lookup
   when loading older published tier generations that lack the field. No tier
   recalculation, API metadata overlay, or numerical republishing is required.
4. Add the toggle to existing per-mode filter state. Apply it alongside search,
   level, and main BPM, updating the filter count, Clear filters, and removable
   active-filter chip. Existing counts, layouts, groups, and Unrated rows derive
   from the filtered collection and retain their published ratings.

## Catalog maintenance

Fetch only chart metadata using the existing process credential:

```powershell
python scripts/build_phoenix2_debut_charts.py
```

The command reads `PIU_SCORES_API_KEY` from the environment without printing or
storing it. An existing catalog can be regenerated deterministically offline:

```powershell
python scripts/build_phoenix2_debut_charts.py --catalog PATH_TO_CATALOG.json --captured-at ORIGINAL_ISO_TIMESTAMP
```

The fallback covers older tier artifacts. Explicit metadata from future daily
capture takes precedence, so newly introduced charts do not require rebuilding
the fallback before filtering can identify them.

## Acceptance and verification

- Toggle off preserves the displayed chart population and all ratings.
- Toggle on includes Phoenix 2 debuts in Singles, Doubles, and Co-op, including
  Unrated entries, and composes with existing filters.
- Explicit older-mix metadata overrides lookup membership. Unknown UUIDs with
  no metadata remain excluded while the toggle is on.
- Clear filters and the active-filter chip both disable the toggle.
- Metadata survives capture, canonical tier building, official/hybrid overlays,
  and Python/TypeScript browser projections without adding fields to old inputs.
- Run focused Python and frontend tests, typecheck, and a production build.
- Perform one integrated diff review, repair only proven regressions, and rerun
  only affected checks. Push a feature PR, verify preview/CI, merge, and verify
  the production deployment and live filter. Stop after these checks pass.

The fresh catalog fetched on 2026-10-30 contains 4,847 charts and 404 Phoenix 2
debuts: 206 Singles, 192 Doubles, and 6 Co-op. The existing live tier generation
contains 286 matching entries: 112 Singles, 168 Doubles, and 6 Co-op. These are
release-validation baselines, not fixed application counts.
