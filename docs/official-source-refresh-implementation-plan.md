# Refresh official leaderboard evidence before daily analysis

## Evidence and scope

Transmission D25 and Pastry mix D25 are unrated in the completed October 25
publication. Both have zero official observations and Phoenix2 `not-mirrored`
states from the October 11 official epoch, fetched October 17. The scheduled
refresh only recalculates against that saved manifest. Official capture is
currently a separate protected operator action.

Add official-source capture to newly requested, enabled Phoenix2 analysis jobs.
Keep General/SSS+ as the only public goals. Preserve historical captures,
checkpoints, scoring rules, models, seeds and sample counts. Do not change
authentication, schedules, queue concurrency, database schema or numerical
recipes. The independently built official player-comparison report is not an
input to these tiers and is outside this scoring-tier repair.

## Implementation

1. Mark new enabled hosted refresh requests as requiring official capture.
   Historical jobs and existing typed checkpoints keep their prior contract.
2. Before submitted-score synchronization or analysis input pinning, invoke the
   existing hosted capture in batches of at most 100 board requests. Persist
   aggregate progress in the owning analysis job, hand off through its existing
   leased queue, and use stable distinct continuation identities. Stop repeated
   unchanged progress after three observations. Do not expose capture rows or
   player tags in status or logs.
3. Reuse the existing live date probe, mixed-epoch rejection, immutable capture
   validation, and atomic manifest promotion. Include safe epoch/count identity
   in partial progress. Never proceed into fitting while capture is partial or
   failed. A complete capture is recorded once; subsequent continuations reuse
   it and validated numerical caches.
4. Expose a clearly named official-source progress phase. Preserve all existing
   cancellation, lease, terminal-delivery and analysis continuation behavior.

## Focused verification

- New cron/operator jobs opt into capture only when official scoring is enabled;
  archived/disabled paths and historical typed resumes remain unchanged.
- Batches advance across the real worker continuation boundary, stop before
  analysis on partial/error, and proceed only after a complete promoted epoch.
- Same-epoch no-progress is bounded; epoch changes are distinguishable. Capture
  failures retain the previous complete manifest and unpublished rankings.
- Existing capture tests cover missing/empty boards, date changes, authenticated
  provenance, archived Phoenix reuse, partial resume and storage failures.
- Run affected worker/runtime/publication and retired-goal checks. Numerical
  modules remain unchanged; run existing cache recipe checks to confirm reuse.
- Perform one general integration review. Repair only a concrete regression
  once, then rerun affected verification without another general review.

## Release and recovery

Push a scoped PR, attach it to this chat, wait for relevant checks, merge and
verify the production alias matches the merge commit. The previous recovery is
completed; start one protected incremental refresh on the new deployment. It
must capture official evidence before analysis, without a competing job or
manual manifest rewrite.

Record the resulting official source epoch and counts for both D25 charts.
Monitor durable capture/model/publication progress, reporting every check in
Eastern Time as requested. Completion requires terminal success, matching tier
and recommendation-model publication, and live General/SSS+ list/detail,
recommendation freshness and Radar checks. Confirm the two D25 tiers have valid
official observations and a scoring estimate; if the new upstream capture still
lacks data or legitimate model eligibility, report the precise source/model
blocker without inventing a tier. A genuine scheduled daily cron remains
separate evidence from the operator recovery.

Stop implementation after this finite acceptance set. No speculative cleanup,
additional reviewer loops or reduced numerical work are authorized by this plan.

## Pre-release verification

- 114 affected runtime, complete continuation, retirement and queue-contract
  tests: 110 pass; four disposable-PostgreSQL checks skip without their optional
  local database configuration.
- 47 official-capture workflow, source validation, lease, budget and numerical
  recipe/cache checks pass, including exact existing 200-sample fixtures.
- New workflow coverage verifies real batched capture across worker handoffs,
  new-request policy gates, no analysis before complete capture, stable distinct
  queue identities, bounded epoch-aware stalls, deadline yielding, failure
  preservation and unchanged historical typed/terminal redelivery behavior.
- One general integration review found no concrete acceptance regression. No
  numerical producer, seed, sample count, authentication or queue setting changed.
