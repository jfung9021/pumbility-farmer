# Reusing refresh computations

The refresh can retain exact numerical results across jobs instead of treating
every successful sync as a new training dataset. Run IDs and source-check times
remain publication/provenance fields. They are not sufficient reasons to refit
an unchanged mathematical input.

## Implemented boundaries

| Component | Durable identity and reuse |
| --- | --- |
| General and tier pipeline | Submitted snapshots omit only top-level generation time and per-player last-sync time. Scores, recorded-at values, membership, catalog, records, metadata, official manifest, other tier-build arguments and exclusions remain dependencies. Code and numerical-library identities remain conservative. Immutable completed/partial computations retain their original audit history; a new run has a separate publication envelope. |
| Target main fit | Prepared binary observations, player/chart identities and eligibility, connected components, target, regularization policy, sample count, seed, model code and numerical-library versions. All 200 approved bootstrap samples are reused only for identical inputs to this component. |
| Target source sensitivity | Source-specific prepared binary inputs and the corresponding main-fit identity. Raw-score General diagnostics are refreshed separately. |
| Refinement point context | Exact private cold-fit parameters, verified against the captured chart effects. New artifacts avoid the recovery solve; legacy artifacts retain deterministic replay. |
| General confidence profiles | Sorted chart scores, chart-derived seed, sample count, percentile/batch recipe and implementation/library identity. Cache the original 1,000 bootstrap percentile vectors, then project them against the current global curve in the original batch order. |
| Recommendation mode fits | Separate Singles and Doubles identities cover normalized frames in their existing order, catalog/combined estimates, folds, numerical code and library versions. Overall remains an assembly of the two modes. |
| Player responses | The serving model's numerical identity plus that player's baseline-input identity. A new publication alone need not invalidate an unchanged response. Changed player data, serving dependencies, schema or membership still invalidate it. Personal score-sync freshness remains independent. |

Cache objects remain private and outside job-checkpoint cleanup. Optional cache
records and their numerical artifacts are verified before reuse; invalid optional
components are rebuilt from current inputs. Corrupt pinned recovery checkpoints,
changed source revisions, or incompatible publication inputs still refuse to
advance. Visible publication pointers continue through the existing fenced join.

### General profile cache storage and recovery

General confidence profiles use version 2 private PostgreSQL JSONB shards rather
than one Supabase Storage upload per chart. A stable chart hash selects one of
64 shards; each chart retains its own exact score/recipe identity. Each entry
contains compressed NumPy bytes with a checksum, so JSONB numeric normalization
cannot change the cached vectors. The independent confidence-interval loop
groups work by shard while preserving the published chart order and subsequent
calibration order. A General build reads each touched shard once and atomically
writes it at most once, only when it changed. Unchanged siblings can still be
reused when one chart's inputs change.

Only one shard is retained in memory at a time. Shards are capped at 128 entries
and 4 MiB, with a 128 KiB compressed-profile limit. Entries exceeding the cache
bounds are computed normally. Version 1 per-chart cache objects are not adopted;
they remain private and may be removed by a separately scoped cleanup.

A cache connection, timeout, or HTTP failure opens a circuit for that General
build. Already computed profiles remain usable, and subsequent charts compute
in memory without further optional-cache I/O. Invalid cached data is a miss;
programming errors and required checkpoint/publication failures still raise.
The 1,000 samples, chart seed, quantile method, projection batches, and scoring
formulas are unchanged. Each build emits aggregate hit/miss/bypass, read/write
count and duration, byte, and failure-class diagnostics without chart/player
identities, raw scores, credentials, or exception messages.

Required Supabase binary operations reuse a separate HTTP session per thread.
They retain checksum validation and upload-before-metadata ordering. Known
transient connection failures and selected retryable HTTP statuses receive at
most three attempts within the configured socket-timeout budget, with short
backoff. Certificate errors, unknown TLS failures, and authorization/policy
responses are not retried. Exhausted required operations still fail. Requests'
socket timeout is not a hard end-to-end transfer deadline. Retry/failure events
include sanitized TLS categories; ordinary first-attempt success stays quiet.

## Execution and timestamps

The global tier continuation pins combined preparation once and resumes the
existing tier state directly. Source revisions and the pinned snapshot revision
remain guards, avoiding a complete source/model reconstruction for each finishing
invocation. Legacy checkpoints still follow their validated recovery path.

Checkpoint serialization hashes the encoded JSON while writing it. Checkpoint
reads verify compressed bytes and hash the uncompressed stream while parsing;
they do not inflate the same object twice. Existing gzip encoding remains
compatible with previously published checkpoints. A local fixture comparison
retained the original compression level: lower levels more than doubled the
compressed bytes for a modest serialization-time improvement.

`generatedAtUtc`/publication generation continues to describe the current run.
`modelComputedAtUtc` preserves the first computation time of identical serving
content. `sourceCheckedAtUtc` describes the successful source-check envelope.
Freshness notices use publication/source freshness; the model-date label can
retain the actual computation date. Immutable audit timestamps are not rewritten.

## Verification and rollout expectations

The required parity cases are unchanged sync metadata, same-band improvements,
target crossings, player/observation removal, policy/catalog changes, changed
source decomposition, cold and warm process caches, interrupted writes and
corrupt optional artifacts. Compare full-precision estimates and intervals,
eligibility, diagnostics and publication/audit contracts. No bootstrap counts,
scoring formulas, calibration or convergence policies are reduced.

The first run after a numerical-code identity change can have cold caches.
Subsequent identical computations can reuse durable results even after a process
restart. Existing artifacts from an incompatible code identity are not silently
adopted.

The observed upstream sync took approximately nine minutes. A 10–15 minute
end-to-end target is plausible for unchanged numerical inputs but requires an
actual fresh-sync benchmark. Changed-data and cold-rebuild timings remain
unverified; a small input delta can still invalidate an entire fitted cohort.

Heavy tier/recommendation fanout, resumable batches inside a single 200-bootstrap
fit, and incremental Clearing evidence are not enabled by this change. Their
memory/coordinator or numerical contracts require separate bounded work. Current
same-input fit reuse does not carry bootstrap replicates into a changed cohort.
