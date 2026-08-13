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

## Shadow and cutover order

1. Reconcile the imported rows against the exact captured source and require zero unexplained
   differences.
2. Deploy the adapter with `PUMBILITY_DATA_BACKEND=vercel`, canonical snapshot writes disabled,
   strict shadow disabled, and selected-player refresh behavior unchanged.
3. Enable fail-open `shadow` writes only after database credentials and private Storage probes pass.
4. Complete two daily shadow generations and one full-sync parity run, including all-player analysis,
   recommendations, API contracts, privacy, concurrency, and performance checks.
5. Canary Supabase reads while writes continue to mirror. Any mismatch returns reads to Vercel.
6. Set `PUMBILITY_DATA_BACKEND=supabase` only after every acceptance item has evidence and approval.

At every stage, the immediate rollback is a server-side backend flag change to `vercel`. Do not drop
the schema or delete hosted data during the acceptance window.
