# Freeze Phoenix 1 official evidence

## Scope and acceptance

Phoenix 1 submitted scores already use an archived private snapshot. Official
Scoring capture still requests its catalog and probes a board during every
batch. Store that official capture permanently and make subsequent capture
requests exclusively to Phoenix 2, including when Phoenix 1 is offline.

Keep the running `analysis-20261026T01` recovery on its pinned deployment. Do not
trigger a refresh, rewrite its inputs/pending capture, change schedules, queues,
authentication, numerical methods, samples, seeds, or model publication flow.

## Implementation

1. Add a private, hash-validated Phoenix archive alongside the existing manifest.
   On the first future refresh, adopt the latest complete manifest's Phoenix
   capture without a network request. If no complete source exists in a new
   environment, finish one bounded initial Phoenix capture and retain it even
   while Phoenix 2 is partial. Never overwrite an existing archive or bypass it
   with the local `--restart` option. Invalid archives fail without fallback.
2. Read the Phoenix catalog from the archive. Reuse its original observations,
   source epoch, fetch timestamps and provenance. Refresh/probe/checkpoint only
   Phoenix 2 once the archive exists. Apply this to hosted and local capture.
3. Project the immutable archive onto each current Phoenix 2 target scope without
   mutating the original. New charts absent from the old catalog remain absent;
   old-catalog charts outside the archived scope are explicitly `not-captured`,
   with zero observations. Preserve chart comparability validation. Never fill
   gaps by fetching Phoenix 1 or inventing scores.
4. Keep atomic complete-manifest promotion, bounded batches, error preservation,
   and worker handoff unchanged. Expose only aggregate archive provenance in the
   existing protected source status, so the adopted archive can be verified
   after a future refresh without revealing player data.

## Verification and release

Test zero Phoenix catalog/board requests after adoption, unchanged archived bytes
and timestamps over repeated refresh/restart, bootstrap/resume with a partial
Phoenix 2 capture, catalog expansion/shrinkage and missing historical coverage,
hash validation, failure preservation, and existing worker continuation behavior.
Run affected scoring/merge/runtime tests, frontend source-state validation,
typecheck and production build. Perform one general integration review; only a
proven regression warrants one focused repair and its affected checks.

Push a scoped PR, merge after checks, verify production points to the merge
commit, and confirm the existing recovery still advances on its original job.
Do not start a competing refresh to activate the archive. The first subsequent
refresh adopts the completed recovery's Phoenix source. Monitor that original
recovery under its existing completion criteria separately.

## Verification results

- 157 affected backend checks: 153 pass and four optional disposable-PostgreSQL
  skips. The protected status fixture was updated for the additional archive
  lookup; all 28 API route checks pass on the affected rerun.
- All 327 frontend tests, TypeScript checking and the production build pass.
- Tests reject every Phoenix request after adoption, check original source
  equality across subsequent dates and restart, and preserve the permanent
  capture across scope changes, partial batches and Phoenix 2 failure.
- One general integration review found no concrete acceptance regression.
  Runtime orchestration, schedules, authentication and numerical producers are
  unchanged. No further general review is needed.
