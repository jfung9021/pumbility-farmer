# Daily cron reliability: evidence and implementation

## Scope and acceptance

Restore reliable daily Phoenix 2 refresh completion without changing numerical
Scoring, Clearing, Pumbility, recommendation, or BPM policies. Implement the
supported database-timeout remedies below, merge the pull request, deploy, and
exercise the production recovery path. A successful cron HTTP response is not
proof that analysis and publication completed.

Acceptance requires deterministic timeout recovery/exhaustion tests, unchanged
lease/cancellation and real no-progress protections, the existing focused worker
and publication checks, frontend checks/build, and production deployment smoke
checks. Verify an authorized recovery's tier and recommendation publications.
A genuine scheduled run remains a separate observation at 15:00 JST; do not
label an operator recovery as cron verification.

## Evidence collected on October 26, 2026 (JST)

- Scheduled job `analysis-20261025T06` was accepted October 25 at 15:00:16 JST
  and failed at 16:08:27 JST during `analysis`.
- Its production terminal event records PostgreSQL `QueryCanceled`, SQLSTATE
  `57014`, category `timeout`, checkpoint phase `analysis`.
- A deterministic injection of the same exception retains the durable
  checkpoint, marks the job failed, releases its active head, and causes terminal
  queue redelivery to acknowledge without resuming. A timeout after the resume
  audit also consumes a no-progress observation.
- Shared database reads currently impose a 10-second SQL deadline on both
  browser requests and background artifacts, including large private JSON
  checkpoints. The exact production statement is not identified by existing
  telemetry, so no specific query rewrite is claimed to fix this incident.
- Schedule, route authorization, retained queue receipts, lease renewal,
  owner-safe handoff, and function/visibility limits pass focused checks. The
  schedule is daily at 06:00 UTC / 15:00 JST.
- Integrated SS/SS+ execution reaches publication after 61 resumptions and
  records all 18 goal work units. The older nested-progress test completes after
  43 resumptions; its 40-iteration cap and fit-specific fixtures that skip the
  existing 12 preparation units are outdated, not runtime failures.

## Implementation

### 1. Bound background database reads separately

Keep the current browser SQL deadline. Introduce a worker-scoped read policy
with a longer, finite deadline for large private artifacts. Retry only
idempotent reads canceled by an actual server statement timeout, in a new
transaction, with a small fixed attempt limit. Do not replay arbitrary writes,
retry explicit user cancellations, or change numerical values/integrity checks.
Keep these limits inside the callback's existing 600-second work budget and
800-second platform limit. Record operation labels, timeout category and attempt
counts without SQL, credentials, private rows or unbounded object paths.

### 2. Resume recoverable checkpoint failures safely

Where a confirmed statement timeout still escapes the read policy, allow a small
durable number of retries from the existing checkpoint and job. Preserve pinned
inputs, completed work, lease ownership, publication pointers and cancellation.
Use a distinct retry continuation identity; do not create a fresh analysis job.
Persist the two-retry ledger in the owner-fenced job payload, keyed by the
checkpoint's durable progress token. A new token starts a new finite budget.
Retry accounting must not consume or erase genuine no-progress history: refund
only an observation demonstrably added by the interrupted invocation, and only
when the durable token still matches. Never roll back committed work. Exhausted
timeouts remain terminal with a clear diagnostic; corruption, incompatible
inputs/code, cancellation and lost ownership retain their existing behavior.

### 3. Verify the complete continuation path

Add regression coverage for a first timeout followed by success, bounded
exhaustion, explicit cancellation, loss of lease, a timeout after the resume
audit, and unchanged public pointers before successful publication. Exercise the
actual SS/SS+ continuation path rather than only its standalone stage. Replace
the stale test's fixed 40-iteration assumption with a documented finite bound
that includes bounded preparation and goal work. Three genuine unchanged
durable observations must still fail.

### 4. Merge, deploy and recover

Run focused Python checks, frontend tests/typecheck/build, and one general review.
Make at most one focused repair for a proven regression and rerun its affected
checks. Push the scoped branch, create and attach a PR, wait for relevant checks,
merge, and verify the production alias matches the merge commit. Start one
authorized incremental recovery with the existing operator control. Follow that
job through model/tier publication and check one chart detail, recommendation
and Radar response. Preserve validated caches and snapshots.

## Evidence-dependent follow-ups

The new operation diagnostics can distinguish large JSON artifact reads,
snapshot-envelope reads, write transactions and connection pressure. Optimize a
specific query or add a snapshot metadata sidecar only when that operation is
shown to be the bottleneck. No evidence currently justifies changing cron
authentication, adding duplicate schedules, increasing queue concurrency,
raising the platform duration, blindly retrying mutations, or rebuilding models
with fewer samples. These are not implementation items for this incident.

## Stopping rule

Stop after the acceptance checks and release verification pass. Report any
unverified genuine daily run or production recovery still in progress, and
unrelated baseline failures, separately. Do not begin another general review or
expand into speculative optimization.

## Implementation validation

- All 12 database-read policy tests pass.
- All 24 affected timeout/progress tests pass, including the complete required
  SS/SS+ continuation chain and the real no-progress guard.
- The broader 266-test worker/store/publication run exposed the stale progress
  fixtures repaired above and one unrelated canonical-snapshot persistence
  fixture error. The latter also reproduces with the base store implementation
  and was left unchanged. Thirteen optional PostgreSQL tests were skipped.
- All 326 frontend tests, TypeScript checking, and the production build pass.
- The initial integration review is complete. Production publication and the
  next genuine scheduled cycle require release observation.

## Focused production repair: checkpoint I/O reserve

The first recovery on the deployed change failed October 25 at 17:31 ET
(`21:31:13Z`), after committing cohort 8 preparation. It reported
`retry-persistence-failed`; the terminal event could not read the job or
checkpoint phase. No replacement public generation was published.

A deterministic regression reproduces the same event pair: after the 600-second
computation deadline, the new read policy assigns a 1-millisecond SQL timeout to
checkpoint reads. Automatic retry accounting then cannot read the checkpoint
either, even though the callback has a reserved persistence/handoff window.

The focused repair keeps the numerical-work deadline at 600 seconds and gives
checkpoint I/O its own finite deadline at 750 seconds. This uses 150 seconds of
the existing reserve and leaves 50 seconds before the 800-second platform limit.
SQL deadlines and read retries use the I/O deadline; computation still yields
at its original deadline. Verify expired computation time with available I/O
time, near/exhausted I/O time, fresh-transaction retry and actual runtime
checkpoint requeueing. Rerun only affected checks; do not start another general
review or expand this repair.

## Transaction references

Retries start a new transaction after the failed read has rolled back, following
[Psycopg transaction semantics](https://www.psycopg.org/psycopg3/docs/basic/transactions.html).
The worker SQL deadline is scoped to its transaction rather than a server-wide
setting; see [PostgreSQL client timeout settings](https://www.postgresql.org/docs/18/runtime-config-client.html).

## Expanded completion plan requested October 26

Implement the following finite set of supported remedies, then monitor an
authorized production refresh through successful tier and recommendation
publication. Predictions without a failing reproduction remain observations.

| Priority | Failure mode and evidence | Solution and acceptance |
| --- | --- | --- |
| 1 | A production callback lasted 647.553 seconds. Its artifact read could not retry after the 600-second work deadline, then checkpoint recovery failed. The exact terminal event pair reproduces locally. | Separate 600-second computation and 750-second I/O deadlines. Expired computation must still allow a finite checkpoint read, fresh-transaction retry, and same-job continuation. |
| 2 | A real 200-sample fit with a synthetic clock completes after 1,010 seconds despite the invocation deadline. Saved bootstrap chunks advance from 10 to 20 samples while the outer audit still fails its third resume. | Yield model fitting at durable bootstrap boundaries and include verified inner progress in continuation/stall accounting. Preserve every approved sample, seed, optimizer and completed fit. Interrupted/resumed output must equal uninterrupted output; genuinely unchanged work must still fail. |
| 3 | Publication assigns a fixed 180-second SQL timeout even when the invocation has less time remaining. | Bound publication statements and lock waits at query boundaries by the remaining I/O deadline. An expired deadline must perform no publication writes. Preserve the atomic transaction, ambiguous-commit readback and existing publication recovery protocol; do not replay arbitrary writes. |
| Verified | Real callback injections show enqueue failure returns HTTP 500 without deleting its receipt; an ambiguous acknowledgment preserves stable continuation identity. Lease and publication checks pass. | Retain the existing queue, ownership, schedule and authorization behavior. Do not add duplicate schedules or change queue concurrency. |
| Observation | An individual optimizer/CV/source-removal solve could take unusually long; ordinary writes and heartbeats have no independently scoped timeout. No production evidence identifies these as the current blocking operation. | Record the limitation and inspect actual callback timings during recovery. Do not change numerical policies or unrelated writes without concrete failure evidence. |

Implementation ownership is split between computation/checkpoint work,
publication deadlines, and read-only production observation. The read-policy
reserve correction and its runtime boundary test already pass. Add focused
numerical-parity, inner-progress and publication-deadline tests for the remaining
changes. Run only checks affected by these changes and the complete continuation
acceptance path; do not begin another general review cycle.

The computation implementation has four bounded parts:

1. Check the work deadline before each bootstrap replicate and preserve the
   exact completed count, RNG state, base fit and CV results before yielding.
   Checking only every tenth replicate is insufficient: a synthetic 50-second
   solve crosses the callback limit before that batch boundary. No sample,
   optimizer, seed or numerical operation is removed or reordered. Retain the
   kernel identity only through the existing exact reviewed-AST allowlist and
   full 200-sample interrupted/uninterrupted parity evidence.
2. Propagate the verified bootstrap/component identity and bounded completed
   count into continuation identity and durable progress before the stall audit.
   Corrupt references, changed inputs and genuinely unchanged work remain
   rejected.
3. Reconcile an already committed scoring-goal fit into its manifest before
   auditing. A reproduction shows that the fit can be durable while a crash
   leaves its manifest behind; replay must adopt it rather than fail the audit
   or recompute it.
4. Bound publication SQL and lock waits before each statement and before commit.
   Test that an expired transaction rolls back candidate pointers and that
   standalone publication keeps its existing settings.

Push the combined scoped branch, create and attach its PR, wait for the Vercel
check, merge, and confirm the production alias matches the merge commit. Start
one incremental recovery through the protected operator route. Observe durable
fit/bootstrap advances, callback durations, retries, and publication pointers
until the job is completed. Then confirm the tier browser payload, lazy chart
detail, recommendation index/player response and Radar are available. A genuine
daily cron at 15:00 JST remains separate scheduled evidence.

Expanded verification before release:

- 18 work/I/O budget and read-policy tests pass.
- 30 target-fit/cache/recipe tests pass, including exact full-200 parity with
  the original implementation. The 12 affected recipe/identity checks pass
  again after strict reviewed-AST traversal normalization. Numerical model,
  General, finishing and SS/SS+ calibration identities remain unchanged; only
  the active orchestration identity changes.
- 10 publication checks pass, including seven new deadline regressions.
- 40 complete continuation, timeout, required-goal and lease checks pass.
- Diff checks pass. No additional general review was started, and no optimizer,
  sample-count, queue-concurrency or cron-authentication changes were made.
