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

`PUMBILITY_SUPABASE_READ_CANARY` is a comma-separated, fail-closed allowlist. The only accepted
domains are `analysis`, `tier-list`, `recommendation-players`, `recommendation-player`, and
`job-status`. Unknown values fail application startup. The owner-approved accelerated sequence is
three 15-minute windows: `analysis,tier-list`, then
`recommendation-players,recommendation-player`, then `job-status`, with at least 30 successful
probes per domain. Each domain must have zero candidate errors, fallback reads, or unexplained
mismatches; endpoint p95 may be no more than 10% above the
Vercel baseline and p99 no more than 20% above it. The canary reads both stores and serves Supabase
only when the values compare exactly; otherwise it serves Vercel and emits aggregate-safe telemetry.

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

Current safe stage on 2026-08-14 JST: the genuine production cron, immediate full sync, canonical
typed shadow generation, exact reconciliation, privacy, regression, capacity, and rollback gates
passed. The production deployment is running `PUMBILITY_DATA_BACKEND=shadow` in fail-open mode,
with `PUMBILITY_SHADOW_STRICT=false` and canonical Supabase shadow writes enabled. Vercel remains
authoritative for every read and publication; Blob mirror/read fallback are disabled and the read
canary allowlist is absent. Canary group 1 produced 60/60 exact candidate reads with no HTTP error,
mismatch, candidate error, or fallback, but exceeded both endpoint latency limits. It was rolled
back immediately. Do not run groups 2/3 or enable Supabase authority until a focused candidate-read
latency change meets the existing p95/p99 gate. See `REMOTE_HANDOFF_2026-08-14.md` for exact evidence.
