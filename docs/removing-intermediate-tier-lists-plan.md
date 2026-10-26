# Remove SS, SS+ and SSS tier lists

## Requested result and boundary

Expose only **General** and **SSS+** tier-list goals in Scoring and Pumbility.
Keep SSS+ as the default. Remove SS, SS+ and SSS choices, navigation and public
variant data, and stop computing independent SS/SS+ models during the daily
refresh. Clearing, Co-op, score grades, earned Pumbility, recommendations and
Radar retain their existing meaning. Do not delete historical publications,
private checkpoints, fits, samples, seeds or database rows.

This is a finite product removal, not a further general performance review.
The existing bounded-execution rules apply to implementation and release.

## Dependency findings

- `lib/scoring-variants.ts` supplies the selector and parses `scoring=` links.
  Removing only selector options leaves old deep links reachable.
- Python `tier_public.py` and TypeScript `lib/tier-browser-payload.ts` produce
  separate response DTOs. Existing compact-list and browser-version early
  returns can retain historical variant maps unless the removal projection also
  covers those inputs. Demo/local responses need the same visible contract.
- `/api/tier-list/chart` can serve a stored lazy chart shard directly. Its
  response must be projected as well as the list and full-payload detail path.
- `production_tiers._publication_overlays` currently calls
  `_advance_required_goals`, requiring four cohorts and four fits for each of SS
  and SS+, plus assembly, before publication. Removing those required work units
  is the direct reduction in refresh work.
- Historical SSS computation is an internal predecessor of SSS+:
  `centered-sss` precedes `sssplus` in `FINISH_STAGES`. Keep this dependency and
  its numerical policies; hiding the SSS tier list does not permit deleting it.
- Private schema/replay validation and stored publication hashes refer to the
  historical goals. Filter at the response boundary after validation, without
  rewriting signed historical publications or silently weakening validators.
- Active tier generations pin `pipelineCode`. A new worker source cannot safely
  resume an incompatible old generation by replacing its identity.

## Implementation

1. **UI and navigation.** Restrict selectable goals to General and SSS+;
   normalize removed/unknown `scoring=` values to the existing SSS+ default while
   preserving mode, metric, player, filters and fragment. Update current About
   copy and availability messages that advertise removed lists. Keep historical
   internal types/validators where required to decode retained artifacts.
2. **Public responses.** Apply an idempotent, non-mutating projection to browser
   lists and chart details in both runtimes. Keep only General/SSS+ variant maps
   and matching advertised goal/policy metadata. Cover raw historical payloads,
   already compact lists, already projected browser DTOs and lazy detail shards.
   Preserve the exact General/SSS+ numbers, generation, detail identity, Clearing,
   Co-op and BPM metadata. Do not modify the persisted signed audit payload.
3. **Refresh work.** Remove required independent SS/SS+ generation from normal
   refresh/publication. Do not look up, prepare, fit or assemble those goals on
   that path. Remove the obsolete operator backfill entry point or return an
   explicit disabled response so it cannot reintroduce retired public goals.
   Keep centered SSS -> SSS+ computation and all approved numerical recipes,
   bootstrap samples, seeds, model caches, lease and cancellation fences.
4. **Focused verification.** Add regression checks for the response projection,
   old deep links, exact retained values and absence of SS/SS+ work. Update tests
   whose explicit product requirement was the retired goals, rather than
   deleting historical numerical/replay coverage.

## Acceptance checks

- The current tier selector contains exactly General and SSS+; General and SSS+
  work for Scoring/Pumbility. Removed goal links resolve to SSS+ without losing
  unrelated navigation state. Clearing and Co-op still work.
- Public list/detail responses expose no SS/SS+/SSS variant keys or advertised
  goals, including old schema/compact/shard inputs. Repeating the projection is
  safe and leaves the input untouched. Retained estimates and metadata are
  unchanged; no General estimate is relabeled SSS+.
- A normal refresh fixture reaches publication without invoking independent
  SS/SS+ lookup, preparation, fitting or assembly. SSS+ internal predecessors,
  resumability, publication validation and cancellation remain covered.
- Run the affected Python publication/runtime/DTO checks, all frontend tests,
  TypeScript checking and the production build. Read the installed Next.js guide
  before changes to Next.js code. Record any proven unrelated baseline failure
  separately.
- Perform one bounded integration review of the final diff. Make one focused
  repair only if that review identifies a demonstrated regression, and rerun
  affected checks without a new general review.

## Safe release and active-job transition

1. Record sanitized status/publication pointers for `analysis-20261025T21` before
   release. If it has completed, retain its result. If it remains active, use the
   existing authenticated `/api/analyze/cancel?jobId=...` operation when its
   credential is available; verify the cancellation flag/terminal state and active
   head release. Do not delete its checkpoint or let the new source claim that
   its pinned generation is compatible. Terminal queue redeliveries must remain
   no-ops through the existing cancellation fences.
   If cancellation credentials are unavailable, preserve the active run and
   its checkpoints, deploy only the public removal, and start no competing
   recovery. Its continuations are pinned to the old immutable deployment;
   redeployment is not cancellation. Both new response boundaries filter any
   later old-run publication, so retired lists cannot reappear in the app.
2. Push the scoped branch, create and attach the PR, wait for relevant checks,
   merge, and verify the production alias points to the merged commit. No shared
   database migration or infrastructure reset is required.
3. Verify production UI/deep links, uncached tier-browser JSON and one lazy chart
   detail. Confirm only General/SSS+ remain and the retained goal values and
   generation agree. Smoke one recommendation and Radar to detect a directly
   affected compatibility regression.
4. If the old job was cancelled, start one authorized incremental recovery on
   the deployed code using the existing operator control. Reuse only caches
   that pass their normal input/recipe integrity checks; do not forge generation
   IDs or blindly restart the cancelled job. Update the existing monitor to
   follow the new job and explain that the old run was intentionally cancelled
   for this product change.
5. The removal deployment can be verified before the replacement refresh
   finishes. Report those facts separately. Refresh completion still requires a
   completed job, tier and recommendation-model publication against that run,
   then tier list, lazy detail, player recommendation under its freshness
   contract, and Radar checks. An accepted request, queued continuation or
   worker-task success alone is insufficient. A genuine scheduled daily run is
   separate evidence from this operator recovery.

## Stop condition

Stop implementation after the scoped checks, one review and release checks
pass. Continue only the authorized refresh monitor if publication is pending.
Report any concrete unresolved blocker rather than entering a repair loop or
changing additional numerical policies to improve runtime.

## Integration review record

One bounded review of the final implementation diff found no concrete acceptance
regression. It confirmed response filtering in both runtimes, compact/browser
early-return coverage, full and lazy chart-detail paths, old deep-link
normalization, retained historical validator contracts and the disabled
independent-goal refresh path. This completes the general review; release
verification is separate and does not authorize another general review pass.

All 327 frontend tests, TypeScript checking, the production build and 81
focused backend checks pass. Backend coverage includes historical continuation,
cache reuse, exact 200-sample parity, publication and API projection. Production
release checks remain the release owner's final verification.

Inspection found no alternate existing operator cancellation route: the current
cancel endpoint requires `CRON_SECRET`; the Jonathan password controls only
refresh/tier-fit operations. Preview topology diagnostics are isolated tests,
not an alternative for live-job mutation. Release must resolve the active-job
transition safely rather than treating new deployment acceptance as cancellation.

The release uses the preserved-run fallback: the local cancellation credential
is stale, and current Vercel production credentials are sensitive and cannot be
downloaded. The old run was not cancelled and no replacement run was started.
Monitor that run separately through terminal status and publication; all later
new-deployment refreshes skip independent SS/SS+ work.
