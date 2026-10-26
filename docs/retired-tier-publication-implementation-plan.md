# Route tier-only publication through the retained producer

Date: October 27, 2026.

Implemented and reviewed once. Python compilation and 55 focused API, storage,
publication, retained-producer/cache/progress, and resume tests pass. An unrelated
cold-cache fixture failure in `CheckpointTests` reproduces with unchanged HEAD
source and was left untouched.

The protected tier-only control still pins the legacy pipeline and calls its
eight-fit builder. Normal refreshes already use the retained General/SSS+
producer, which needs four SSS+ source/mode fits. The tier-only path also starts
with an outdated stage total and records schema 57 in its publication audit.

Two subagents traced the producer path and the worker/test/release path. Their
plans agree on the following bounded change.

## Implementation

1. Mark new tier-publication jobs and pinned inputs with the retained producer.
   Pin and validate its pipeline identity, then dispatch through
   `build_retained_hosted_tiers`. Require the same producer when resuming a
   captured phase so a legacy state cannot enter a retained continuation.
2. Use the retained four-fit stage count: 14 producer stages plus the outer
   input-pinning stage. Return the actual candidate schema and record schema 61
   in the completed publication audit.
3. Reject legacy unmarked jobs inside the existing claimed-job failure path,
   before new numerical work or promotion. Reject a candidate without the
   retained schema/producer. Preserve all existing source revision,
   recommendation, lease, immutable checkpoint, and atomic publication checks.
4. Keep historical builders and validators available. Do not change the
   cancellation API, credentials, score capture, recommendation model, or tier
   mathematics.

## Verification

- New tier-only jobs pin and dispatch the current producer.
- Only the four submitted/official Singles/Doubles SSS+ cohorts are fitted;
  each target is 995,000. No retired SSS, SS, or SS+ work is scheduled.
- Continuations preserve their retained identity and accurate progress totals.
- Legacy jobs and incompatible candidates fail terminally without promotion.
- Successful publication records schema 61 and preserves protected revisions.
- Run the affected publication, retained producer, checkpoint, and API tests;
  run TypeScript/frontend checks and the production build as relevant.

Perform one general review, repair only demonstrated regressions, rerun affected
checks, and stop when acceptance passes.

## Release and running-job handling

Push and merge this as a separate PR, deploy the merged revision, and let the
already-running legacy tier-only job fail safely on its next new-code
continuation. Its current fit phase cannot publish by itself. Preserve its
immutable input/checkpoint artifacts; do not reinterpret them as retained state.
After it reaches a terminal state, start a fresh retained tier-only job from
cached production inputs. Verify the live General/SSS+ generation declares the
75% midpoint policy and that list and lazy chart details agree.
