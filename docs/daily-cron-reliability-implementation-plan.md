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
- One integration review found no additional product-code defect. Production
  publication and the next genuine scheduled cycle require release observation.

## Transaction references

Retries start a new transaction after the failed read has rolled back, following
[Psycopg transaction semantics](https://www.psycopg.org/psycopg3/docs/basic/transactions.html).
The worker SQL deadline is scoped to its transaction rather than a server-wide
setting; see [PostgreSQL client timeout settings](https://www.postgresql.org/docs/18/runtime-config-client.html).
