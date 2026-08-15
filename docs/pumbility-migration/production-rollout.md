# Pumbility production rollout

The production rollout is fail-closed and keeps Vercel Blob/Runtime Cache authoritative until the
full parity checklist is signed. The hosted `pumbility` schema may exist and contain shadow data
while `PUMBILITY_DATA_BACKEND` remains unset or `vercel`.

## Credentials and connection boundaries

- Never deploy the Supabase `postgres` owner password to Vercel.
- Provision a dedicated `pumbility_runtime_login` login out of band with a generated password and
  membership only in the migration-owned `pumbility_worker` NOLOGIN role.
- Use the Supavisor session pooler on port 5432 only for the one-off production backfill. The
  operator URL must be supplied through `PUMBILITY_PRODUCTION_DATABASE_URL`, use the username
  `pumbility_runtime_login.gsiyqhkcgegjrvqcqioc`, database `postgres`, and `sslmode=require`.
- Use the transaction pooler on port 6543 for the later Vercel runtime. The adapter disables named
  prepared statements. Store the URL only as the server-side `PUMBILITY_DATABASE_URL` variable.
- Obtain the server-only Supabase URL and service-role key through the authorized Supabase/Vercel
  secret channels. Never expose either as `NEXT_PUBLIC_*` or commit them.

## Backfill gate

First run the existing local validation, which performs no database connection:

```powershell
& .\.venv\Scripts\python.exe .\scripts\backfill_pumbility_supabase.py `
  --dry-run --mix phoenix1 --mix phoenix2
```

Then set the operator URL and exact confirmation in the current secured process. Do not put either
value on a command line or in a file. The production command independently verifies the exact
project ref, host, port, database, TLS mode, login flags, worker-role membership, migration version,
and advisory lock. Without `--apply`, it only reads one generation-consistent Blob boundary and
prints aggregate counts:

```powershell
& .\.venv\Scripts\python.exe .\scripts\backfill_pumbility_production.py `
  --expected-project-ref gsiyqhkcgegjrvqcqioc
```

The mutation requires both `--apply` and this exact process-only value:

```text
PUMBILITY_PRODUCTION_CONFIRMATION=BACKFILL gsiyqhkcgegjrvqcqioc 20260813010000
```

The importer commits one mix at a time, records sync provenance, suppresses unchanged temporal
revisions, imports the active schema-3 model graph and cached player artifacts, verifies the private
NPZ through Storage, and rereads the mutable snapshot and active pointers at the end. If production
advanced during the run, it exits unsuccessfully; rerunning is required and is idempotent.

The approved one-time orchestration keeps the generated login password and downloaded service key
in process memory and subprocess stdin. It provisions the narrow login, runs both guarded backfill
phases, and installs the server-side Vercel variables with the backend explicitly left on `vercel`:

```powershell
vercel env run -e production -- `
  .\.venv\Scripts\python.exe .\scripts\provision_pumbility_production.py
```

After exact hosted reconciliation passes and the flags-off adapter release is deployed, populate
typed shadow analysis/model rows through the same secured wrapper:

```powershell
vercel env run -e production -- `
  .\.venv\Scripts\python.exe .\scripts\provision_pumbility_production.py --populate-shadow
```

This mode rotates the narrow login in memory, reruns exact relational/artifact reconciliation,
computes the production-equivalent typed analyses and schema-3 recommendation model, verifies the
stable Vercel boundary, and only then inserts `shadow` typed rows. Analysis, combined-tier,
recommendation JSON, indexes, and private shards must match exactly. Numeric NPZ arrays retain
exact names, shapes, dtypes, and discrete values; floating score arrays may differ by at most
`1e-8` absolute with zero relative tolerance to accommodate cross-platform floating-point drift.
The copied source NPZ remains the stored artifact. No publication pointer or rollout flag changes.

## Shadow and cutover order

1. Reconcile the imported rows against the exact captured source and require zero unexplained
   differences.
2. Deploy the adapter with `PUMBILITY_DATA_BACKEND=vercel`, canonical snapshot writes disabled,
   strict shadow disabled, and selected-player refresh behavior unchanged.
3. Enable fail-open `shadow` writes only after database credentials and private Storage probes pass.
4. Complete one genuine scheduled shadow generation and one full-sync parity run, including all-player analysis,
   recommendations, API contracts, privacy, concurrency, and performance checks.
5. Canary Supabase reads one domain at a time while writes continue to mirror. Any mismatch or
   candidate error serves the Vercel value automatically.
6. Set `PUMBILITY_DATA_BACKEND=supabase` only after every acceptance item has evidence and approval.

At every stage, the immediate rollback is a server-side backend flag change to `vercel`. Do not drop
the schema or delete hosted data during the acceptance window.

The owner approved one scheduled production cycle as the changing-data shadow gate on 2026-08-14
JST; no multi-day soak is required. This variance does not waive reconciliation, full-sync,
all-player parity, privacy, regression, capacity, or rollback evidence. Run the full sync immediately
after the scheduled cycle in the same supervised low-traffic window.

## Read canaries and cutover controls

Immediately before enabling each read-canary group, run the read-only pre-canary gate through the
injected production environment:

```powershell
vercel env run -e production -- `
  .\.venv\Scripts\python.exe .\scripts\verify_pumbility_pre_canary.py
```

The gate does not edit deployment configuration or persisted data. It accepts either the documented
Vercel-authoritative, fail-open shadow flag set with canonical shadow writes in their accepted state,
or the stricter Vercel-only rollback state with canonical writes off. Both require an empty
read-canary allowlist, cutover-only Blob controls disabled, and selected-player refresh frozen. The
reconciler must report the same Vercel-authoritative backend that the flag check observed. The gate
then runs the focused checksum, schema-migration, exact-comparison, fallback, and
privacy contracts and performs the existing exact production reconciliation. A passing aggregate-safe
summary is required before the canary flag is changed; a prior run is not evidence for a later group.

`PUMBILITY_SUPABASE_READ_CANARY` is a comma-separated, fail-closed allowlist. The only accepted
domains are `analysis`, `tier-list`, `recommendation-players`, `recommendation-player`, and
`job-status`. Unknown values fail application startup. The owner-approved accelerated sequence is
three 15-minute windows: `analysis,tier-list`, then
`recommendation-players,recommendation-player`, then `job-status`, for 45 minutes total. Score p99
only with at least 100 successful probes per domain; the focused probe defaults to 3 unscored
warmups plus 100 scored requests spread across each 15-minute window. Each domain must have zero
HTTP errors, candidate errors, fallback reads, cache hits, or unexplained
mismatches; endpoint p95 may be no more than 10% above the
Vercel baseline and p99 no more than 20% above it. The canary reads both stores and serves Supabase
only when the values compare exactly; otherwise it serves Vercel and emits aggregate-safe telemetry.

Use `scripts/probe_pumbility_read_domains.ps1` for the immediately adjacent Vercel baseline and
each canary window. Omit `-ExpectCanaryTelemetry` for the baseline and require it for a canary run,
so the two evidence types cannot silently claim the same expected event count. The probe requests
compressed responses, uses a unique query nonce plus no-cache
headers, and records TTFB, download, JSON parsing, and end-to-end timing separately. It writes a
sanitized JSONL sample file and JSON summary under ignored `.local-data/pumbility-latency-probes/`;
neither file contains request paths, query values, response bodies, player keys, job IDs, or curl
error text. `-SkipP99` is only for non-gating smoke runs.

```powershell
& .\scripts\probe_pumbility_read_domains.ps1 `
  -Domains analysis,tier-list `
  -Samples 100 `
  -WarmupSamples 3 `
  -WindowMinutes 15 `
  -ExpectCanaryTelemetry
```

The HTTP probe cannot complete the telemetry gate by itself. Before accepting a window, reconcile
server logs to each summary's `expectedCandidateReadEvents`: normally one event per request, but a
selected-player request reads both its index and player artifact and therefore expects two. Group 2
also includes the one retained player-discovery request in the recommendation-player-list count.
Every expected event must exist and be `candidate-served`, with zero candidate errors, fallbacks, or
mismatches. A canary summary intentionally reports `telemetry.countGateComplete=false` until this
separate server-log evidence is attached; a baseline summary marks that gate not applicable.
For the `job-status` group, pass `-JobId` for a current job created by the supervised rollout
window. The probe rejects that domain without an explicit job ID because Vercel job records expire
after 24 hours; the supplied value is used only in the request and is never written to probe evidence.

### Controlled preview region and connection comparison

Region or connection-strategy experiments must use two already-created preview deployments. This
repository tool does not create deployments or edit `vercel.json`, environment variables, rollout
flags, or database state. For a region comparison, build both previews from the same commit and
configuration, varying only the function region. For a connection-strategy comparison, keep the
region and every other input fixed and vary only the reviewed connection implementation. Do not
combine both variables in one comparison.

Run identical probes against the two explicit preview origins. The known production alias is
rejected, HTTPS is mandatory outside loopback tests, and URLs containing credentials, paths,
queries, or fragments are rejected. Labels are the only deployment identity retained in the
aggregate report; use generic region/variant labels, never deployment identifiers:

```powershell
.\.venv\Scripts\python.exe .\scripts\compare_pumbility_preview_regions.py `
  --first-url "https://<first-preview-origin>" `
  --first-label "iad1" `
  --second-url "https://<second-preview-origin>" `
  --second-label "cle1" `
  --domain analysis `
  --domain tier-list `
  --samples 100 `
  --warmup-samples 3 `
  --window-minutes 15
```

The runner invokes the tracked probe with exactly the same domains, scored samples, warmups,
window, p99 mode, and telemetry expectation for both previews. It hashes decoded HTTP response
bodies and requires exact pairwise identity across discovery, warmup, and scored responses. Raw
digests remain only in ignored local evidence; the comparison report exposes counts, parity,
latency by supplied label, and no URL, hostname, digest, body, subprocess error, or secret. Run the
previews against a stable data boundary because sequential requests that legitimately observe a
publication change will fail exact parity and must be repeated.

A faster API result is not sufficient evidence to move production. The runner always records the
adoption decision as pending. Before selecting a region or connection strategy, separately prove
worker execution, private Blob behavior, cron delivery, queue publish/consume behavior, cold starts,
connection capacity, failure handling, and rollback from that deployment topology. The origin-based
runner intentionally does not accept a protection credential or bypass token. Use it only when both
explicit preview origins are already reachable without weakening their configured protection.

For protected previews, inject the two deployment references into the operator process through the
named environment variables below. Never put either reference on the command line, in a report, or
in a checked-in file. The tracked authenticated runner attests that both references are Preview
deployments, uses `vercel curl --deployment` without a bypass token, and keeps the references,
request paths, decoded bodies, response digests, and captured command output only in memory or
short-lived temporary files:

Run deployment, inspection, curl, and log commands only from a worktree whose `.vercel/project.json`
has been compared in memory with the already trusted project link. Do not allow an interactive CLI
command to create or relink a project during qualification. In PowerShell, pass every deployment
environment override as one quoted argument. The two-domain value must therefore be written as
`--env "PUMBILITY_SUPABASE_READ_CANARY=analysis,tier-list"`; an unquoted comma can split the value
into separate PowerShell arguments. Inherit credentials from the linked Preview environment rather
than placing secret values on the command line.

```powershell
# Set these through the approved secure operator-shell mechanism; do not echo them.
if ([string]::IsNullOrWhiteSpace($env:PUMBILITY_FIRST_PREVIEW_DEPLOYMENT)) {
  throw "The first protected preview reference is not injected."
}
if ([string]::IsNullOrWhiteSpace($env:PUMBILITY_SECOND_PREVIEW_DEPLOYMENT)) {
  throw "The second protected preview reference is not injected."
}

.\.venv\Scripts\python.exe .\scripts\compare_pumbility_protected_previews.py `
  --first-label "pool-control" `
  --second-label "pool-candidate" `
  --domain analysis `
  --domain tier-list `
  --samples 100 `
  --warmup-samples 3 `
  --window-minutes 15 `
  --expect-canary-telemetry
```

The protected runner alternates deployment order and pairs both variants inside one scored window.
It requests gzip and cache bypass, requires curl to provide separate TTFB and total-transfer timing,
times decoded JSON parsing separately, and fails closed if any timing boundary is unavailable. The
reported end-to-end value is curl's network total plus local JSON parsing; authenticated CLI startup
is explicitly excluded and reported as such. It compares decoded-body SHA-256 values only in memory;
the sanitized comparison and sample files retain only generic labels, timing/count evidence, and
exact-pair booleans. With the command above, each
preview must produce exactly 103 expected `candidate-served` events per domain. Reconcile those exact
counts in server logs before treating the telemetry gate as complete; the runner never marks that
external gate complete itself.

The owner approved one bounded transport exception on 2026-08-15: an individual request that ends
before receiving any HTTP response may be retried once immediately against the same deployment,
domain, and probe phase. The original attempt and retry must both be retained as separate sanitized
attempt records; neither may expose a deployment reference, origin, request path or query, body,
digest, credential, or raw error text. A no-response scored attempt does not count toward the sample
set, so each deployment must still complete exactly 100 successful scored responses per domain in
addition to the three successful warmups. There is no retry for an HTTP response of any status, an
application or JSON-contract error, a cache hit, a parity mismatch, a candidate or authority error,
a fallback, or a missing timing boundary. If the one retry also receives no HTTP response or fails
any ordinary gate, the comparison fails immediately; the rule must not become a recursive retry or
an excuse to omit the failed attempt from evidence.

The comparison report applies the original diagnostic target directly to end-to-end latency:
second-deployment p95 may be at most 10% above the first deployment and p99 at most 20% above it.
The report is `failed` when either target misses. A `-SkipP99` run is reported as `smoke-passed`, not
as qualification evidence. TTFB, download, JSON parsing, and end-to-end percentile decomposition
remain in `latencyComparison` for diagnosis even when the aggregate target misses.

### Offline topology qualification evidence

Use the following tools only with already-created, non-production diagnostic environments. They do
not deploy, change application configuration, change a rollout flag, or mutate the database, Blob,
or queue. All private input and generated evidence belongs under ignored `.local-data/`. Keep the
real alias Vercel-authoritative while gathering it.

First create two local deployment-metadata files and one stable-boundary file. Deployment metadata
schema version 1 contains only `label`, `region`, `gitCommit`, `sourceSha256`, `lockSha256`,
`runtime`, `memoryMb`, `maxDurationSeconds`, `workerConcurrency`, `databaseConnectionLimit`,
`connectionStrategy`, environment *key names*, and the sanitized rollout flags. Do not include an
origin, deployment/project ID, environment value, connection string, or token. The boundary file
contains `publicationFrozen`, `exactReconciliationPassed`, and two private SHA-256 boundary values.
The capture command compares the private values but does not retain them:

```powershell
.\.venv\Scripts\python.exe .\scripts\capture_pumbility_topology_manifest.py `
  --first .\.local-data\qualification\iad1-metadata.json `
  --second .\.local-data\qualification\cle1-metadata.json `
  --stable-boundary .\.local-data\qualification\stable-boundary.json `
  --topology-kind region `
  --output .\.local-data\qualification\topology.json
```

The command fails unless the deployments have the same commit, source and lock identities, runtime,
memory, timeout, concurrency, database limit, environment key set, safe flags, and connection
strategy; only `region` may differ. A connection-strategy experiment instead uses
`--topology-kind connection` and requires the region to match. Safe flags mean Vercel-authoritative
`vercel` or accepted fail-open `shadow`, cutover-only Blob controls off, selected-player refresh
frozen, and no canonical writes in Vercel-only mode. The read-canary list must be either empty or,
only for the protected Phase 5 API comparison, exactly `analysis,tier-list`; Production and rollback
deployments keep it empty.

For the adopted topology, `connectionStrategy` means the Vercel runtime transaction pooler on port
6543. Capacity probes, queue durable effects, mutation probes, and injected database failures must
exercise that same runtime URL. The session pooler on port 5432 remains restricted to the existing
one-off backfill and reconciliation operations and cannot provide candidate-topology capacity or
worker evidence. When exporting deployment metadata, read runtime region, runtime, memory, and
timeout from each applicable function output; the deployment build location is not runtime-region
evidence.

Run the private-Blob harness *inside an isolated diagnostic task in each deployment topology*. The
harness requires the platform-provided `VERCEL_REGION` to equal the supplied label, so running it
from an operator laptop is not region evidence. Its private target manifest contains generic
artifact names, private HTTPS Blob URLs, and expected payload SHA-256 values. The token is read from
`BLOB_READ_WRITE_TOKEN`, never from an argument. It proves that anonymous reads are denied, then
performs three warmups and at least 100 authenticated read-only samples per artifact with exact byte
identity:

```powershell
.\.venv\Scripts\python.exe .\scripts\benchmark_pumbility_blob_region.py `
  --label cle1 `
  --targets .\.local-data\qualification\blob-targets.json `
  --output .\.local-data\qualification\blob-cle1.json
```

The output contains only the attested label, counts, latency percentiles, and booleans. URLs, hashes,
bodies, tokens, and exception text are suppressed. A task/route capable of invoking this script in a
deployment is intentionally not added by this tooling-only change; that runtime wiring must be
reviewed separately before its evidence can exist.

Export sanitized diagnostic events as JSONL. The verifier accepts only these exact event contracts;
unknown fields fail closed so raw job, request, player, deployment, URL, or secret values cannot be
silently retained:

- `telemetry`: label, domain, allowlisted outcome, and aggregate count. Candidate-served counts must
  exactly equal each probe summary's `expectedCandidateReadEvents`; candidate errors, fallbacks,
  mismatches, and authority errors must all be zero.
- `worker`: label, component (`analysis` or `player-recommendations`), outcome/count, and
  `isolatedDiagnostic=true`. Each topology needs at least one successful isolated execution and zero
  failures for both configured worker components.
- `cron`: label, source (`platform-scheduler`, `route`, or `manual`), a locally HMAC/SHA-256-derived
  correlation value, count, and authorization result. This gate applies only to the adopted IAD1
  topology because Vercel Cron invokes the current Production deployment and ignores Preview
  deployments. A genuine cron requires exactly one independent platform control-plane delivery and
  one correlated route observation, with zero manual events. A manually authorized HTTP request
  alone can never pass this gate. CLE remains a protected comparison deployment and must never be
  made current Production during this rollout.
- `queue`: label, one of the configured `analysis` or `player-recommendations` topics, stage
  (`published`, `consumed`, `durable-effect`, or `error`), a one-way identity value, and attempt.
  Per topology and topic, at least 100 unique published/consumed/effect identities must reconcile,
  durable effects must occur exactly once, and errors must be zero. At least one identity must have a
  repeated consume with an attempt greater than one while retaining exactly one durable effect, so a
  happy-path-only sample cannot claim redelivery safety.
- `cold-start`: label, component (`api`, `analysis-worker`, or
  `player-recommendations-worker`), duration, success, and `cold=true`. Each component/topology needs
  30 successful samples and zero errors. Each record must measure a genuinely new runtime instance's
  initialization boundary. A module timer started after imports, a first request served by a reused
  process, or a duration that includes ordinary queue-task work is not a cold-start sample. Vercel
  instance reuse means request or task concurrency alone cannot establish the sample count. The
  diagnostic target is +10% p95 and +20% maximum.
- `capacity`: label, active connections, connection limit, connection/deadline error counts. Each
  topology needs 30 samples, must reach configured worker concurrency, remain at or below 75% of the
  dedicated database connection limit, and have zero connection or deadline errors.

Collect the runtime events with the tracked privacy-safe collector. Use UTC bounds that tightly
cover the supervised run. The collector reads each deployment reference only from the named process
environment variables, keeps Vercel log envelopes and immutable platform log IDs in memory, splits
limit-saturated time windows, deduplicates overlapping boundaries, and writes only exact
verifier-allowlisted event objects below ignored `.local-data/`. If a minimum time window remains
saturated, it fails closed instead of emitting incomplete evidence:

```powershell
.\.venv\Scripts\python.exe .\scripts\collect_pumbility_topology_events.py `
  --first-label iad1 `
  --second-label cle1 `
  --since "<UTC-start>" `
  --until "<UTC-end>" `
  --output .\.local-data\qualification\diagnostic-events.jsonl
```

The output never contains deployment references, platform log IDs, hosts, URLs, secrets, or raw log
messages. Preserve the private deployment references only in the operator process and clear them
after collection.

### Genuine scheduled-cron evidence

Gather genuine scheduler evidence only for the adopted IAD1 topology. After every other hosted gate
passes, retain the current safe Production deployment reference privately and prepare a
date-specific UTC expression several minutes in the future. Deploy the IAD1 one-shot as Production,
require the intended deployment to become current, verify its registered schedule and safe flags,
and do not use `vercel crons run`. Require exactly one scheduler GET with HTTP 202, one correlated
IAD1 route event, worker completion, and exact post-run reconciliation.

Immediately restore `0 6 * * *` with a second IAD1 Production deployment. Do not rely on an instant
rollback to restore cron registration. Verify the second deployment is READY and current, the daily
schedule is registered, topology diagnostics are disabled, the safe flags are restored, and exact
reconciliation still passes. A staged `--prod --skip-domain` deployment is useful for inspection but
does not by itself prove that the scheduler invoked that staged deployment.

The schema-version-1 fault/rollback checklist must cover `supabase-timeout`, `blob-timeout`,
`queue-redelivery`, `worker-crash`, and `cron-replay`, each with the expected outcome observed, zero
data corruption, and an explicit pass. Its `privateBlobMutation` section must attest that a separately
isolated diagnostic performed exact JSON and binary write/read/delete cycles and that an injected
failed bundle retained the previous pointer with no partial publication. Read-only Blob timing cannot
substitute for this worker-topology mutation gate. Rollback must finish within 300 seconds and prove
safe flags, API and both worker smokes, exact reconciliation, absent canary telemetry, and zero data
loss. Its privacy section must attest sanitized events and no raw identifiers, URLs, or secrets.

Reconcile all evidence offline, passing the Blob reports in the same order as the topology manifest:

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_pumbility_topology_qualification.py `
  --topology-manifest .\.local-data\qualification\topology.json `
  --api-comparison .\.local-data\pumbility-region-benchmarks\<run>\comparison.json `
  --blob-report .\.local-data\qualification\blob-iad1.json `
  --blob-report .\.local-data\qualification\blob-cle1.json `
  --events .\.local-data\qualification\diagnostic-events.jsonl `
  --fault-rollback-checklist .\.local-data\qualification\fault-rollback.json `
  --output .\.local-data\qualification\qualification.json
```

The original +10% p95/+20% p99 API and private-Blob targets always remain in
`diagnosticLatencyGates`; a miss is always reported as `failed`. If the owner explicitly accepts only
the measured latency variance after every non-latency gate passes, repeat the final offline command
with `--owner-latency-waiver`. The only successful waived state is the distinct
`owner-latency-waived`, never `passed`. The waiver is ineligible if latency evidence is incomplete or
if correctness, exact parity, safe flags, telemetry, private access, worker execution, genuine cron,
queue integrity, cold-start correctness, capacity, failure/fallback behavior, rollback, or privacy is
missing or failing.

The final switch is one configuration set:

```text
PUMBILITY_DATA_BACKEND=supabase
PUMBILITY_CANONICAL_SNAPSHOT_WRITE_ENABLED=true
PUMBILITY_BLOB_MIRROR_ENABLED=true
PUMBILITY_BLOB_READ_FALLBACK_ENABLED=true
PUMBILITY_SUPABASE_READ_CANARY=
```

Application startup rejects a partial Supabase-authority flag set. Supabase-primary mutations create
reference-only `blob_mirror` outbox events and synchronously mirror to Vercel. If delivery fails, use
the secured environment injection and explicit confirmation to replay the idempotent event; the
command prints counts only, never artifact references or contents:

```powershell
vercel env run -e production -- `
  .\.venv\Scripts\python.exe .\scripts\drain_pumbility_blob_outbox.py --apply
```

Set the process-only `PUMBILITY_BLOB_OUTBOX_CONFIRMATION` value to
`DRAIN PUMBILITY BLOB OUTBOX`. Keep the Blob mirror and read fallback enabled for 14 days. After
cutover, perform the owner-approved 45-minute active watch of errors, mismatches, fallback use,
latency, job health, and capacity. Any gate failure uses this rollback set immediately:

```text
PUMBILITY_DATA_BACKEND=vercel
PUMBILITY_CANONICAL_SNAPSHOT_WRITE_ENABLED=false
PUMBILITY_BLOB_MIRROR_ENABLED=false
PUMBILITY_BLOB_READ_FALLBACK_ENABLED=false
PUMBILITY_SUPABASE_READ_CANARY=
```

Current safe stage on 2026-08-14 JST: the optimized commit `1ca5399` is live in `iad1` on deployment
`dpl_GMs4LwAMcvZKu76t7FPLDx45MFZp`. Production is still
`PUMBILITY_DATA_BACKEND=shadow` in fail-open mode, with `PUMBILITY_SHADOW_STRICT=false` and accepted
canonical Supabase shadow writes enabled. Vercel remains authoritative for every read and
publication; Blob mirror/read fallback are disabled and the read-canary allowlist is absent.

The optimized production group-1 attempt followed a fresh exact pre-canary reconciliation and an
adjacent corrected baseline. It produced 103/103 `candidate-served` telemetry events per domain,
including warmups, with zero HTTP errors, cache hits, mismatches, authority errors, candidate
errors, or fallbacks. It nevertheless failed the fixed latency gate: analysis p95/p99 was
`1599.077/1752.292 ms` versus `1189.065/1271.094 ms` baseline, and tier-list was
`1254.527/1323.056 ms` versus `977.721/1104.796 ms` baseline. The alias was immediately rolled back
to the Vercel-only deployment above and warm public health checks passed.

The protected pool-off/pool-on IAD repeat and the pool-on IAD/CLE region comparison now both have
100 scored samples per domain, exact response parity within each comparison, zero HTTP/cache errors,
and exact 206/206 `candidate-served` telemetry per deployment. Pool-on improved endpoint p95 in two
independent IAD comparisons. CLE improved analysis p95/p99 by `20.361%/23.284%` and tier-list by
`3.158%/8.561%` in the accepted region comparison. These results qualify the read-path candidates
only. Region or connection adoption remains pending the worker, private Blob, cron, queue,
cold-start, connection-capacity, failure, and rollback topology gates listed above. Do not run groups
2/3, re-enable a production read canary, move the production region, or enable Supabase authority
until the fully gated topology has complete non-latency evidence. See
`REMOTE_HANDOFF_2026-08-14.md` for exact evidence.
