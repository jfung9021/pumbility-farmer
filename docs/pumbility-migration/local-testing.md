# Local Pumbility Supabase testing

This workflow uses the local Supabase stack owned by the sibling
`C:\Users\jfung\Downloads\bite-open-card-draw` repository. It never needs production Supabase,
Vercel, or PIU Scores credentials. Commands that mutate the database are deliberately run from the
sibling repository, whose `supabase/config.toml` identifies the local project and database port.

## Hard safety rules

- Use only `localhost`, `127.0.0.1`, or `::1` database targets. The preflight and migration scripts
  must refuse every other host.
- Never paste a production database URL, service-role key, PIU Scores key, or operator password into
  a command, log, issue, or committed file.
- Do not edit or replace this repository's `.env.local` as part of setup. Set variables in the
  current PowerShell process or copy the names from `.env.supabase-local.example` manually.
- Use `.\.venv\Scripts\python.exe` (or `uv run python` after `uv sync --frozen`), never bare
  `python`; bare Python on this host is not the project's Python 3.12 runtime.
- `supabase db reset` deletes the sibling stack's local database. It must never be aimed at a linked
  or hosted project.

## Current machine blocker

The last audit found less than 1 GB free on C:, while Docker Desktop stores its WSL data there. Do
not install the Supabase CLI, start Docker, pull images, or reset the database in that state.

First use Docker Desktop **Settings > Resources > Advanced > Disk image location** to move Docker
data to a dedicated directory on D:, or free at least 15-20 GB on C:. Moving Docker data is a Docker
Desktop operation; do not move its WSL files by hand. Then restart Docker Desktop and rerun the
preflight.

From the Pumbility repository:

```powershell
Set-Location C:\Users\jfung\Downloads\piu_misgrade_bundle
$env:PUMBILITY_DATABASE_URL = '<LOCAL_LOOPBACK_POSTGRES_URL>'
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\preflight-pumbility-local.ps1
```

The preflight prints no URL or key. It only checks the target host/port, sibling project identity,
C: free space, Docker daemon, Supabase CLI, Python 3.12 environment, and snapshot manifests. It does
not install software, start services, write environment files, or touch either database.

If the disk check is healthy but the CLI is still missing, install a pinned Supabase CLI using the
team-approved method in the sibling repository, then rerun the preflight. Do not use an unpinned
download and do not install while the disk check is blocked.

## Reset the shared local stack without application seeds

Only after preflight passes:

```powershell
Set-Location C:\Users\jfung\Downloads\bite-open-card-draw
npx --no-install supabase start
npx --no-install supabase status
npx --no-install supabase db reset --no-seed
```

`--no-seed` is required: Pumbility tests need migrations and purpose-built fixtures, not the
sibling application's general seed data. If `npx --no-install` reports that Supabase is unavailable,
stop and install the pinned CLI; do not allow `npx` to fetch an arbitrary version implicitly.

## Run only the Pumbility database test

Run the sibling repository's focused schema test through its guarded local runner:

```powershell
Set-Location C:\Users\jfung\Downloads\bite-open-card-draw
& .\scripts\run-pumbility-schema-tests.ps1
```

This does not run the sibling project's Karaoke, Protein Tracker, or tournament database suites.
The adapter independently refuses non-loopback URLs and a noncanonical container name.

## Backfill private local snapshots

Run the guarded backfill entrypoint from this repository:

```powershell
Set-Location C:\Users\jfung\Downloads\piu_misgrade_bundle
& .\.venv\Scripts\python.exe .\scripts\backfill_pumbility_supabase.py `
  --database-url-env PUMBILITY_DATABASE_URL `
  --source-root .local-data/piu-scores `
  --mix phoenix1 `
  --mix phoenix2
```

The URL is passed by environment-variable name so it is not exposed in the process command line.
The importer must validate the existing manifests and checksums before committing any rows. It must
not import legacy recommendation shards; those repeat several gigabytes of derived candidate data.

## Reconcile against the existing baseline

After a successful backfill, run typed analysis and then reconcile:

```powershell
& .\.venv\Scripts\python.exe .\scripts\analyze_pumbility_supabase.py `
  --database-url-env PUMBILITY_DATABASE_URL `
  --mix phoenix1 `
  --mix phoenix2

& .\.venv\Scripts\python.exe .\scripts\build_pumbility_supabase_model.py `
  --database-url-env PUMBILITY_DATABASE_URL
```

The model command also needs the local Supabase URL and CLI-generated service-role key from the
ignored shell environment because its NPZ binary is stored in the private local Storage bucket.
To exercise the selected-player path without calling PIU Scores, copy a `playerKey` from the local
`/api/recommendations/players` response and run:

```powershell
& .\.venv\Scripts\python.exe .\scripts\refresh_pumbility_supabase_player.py `
  --database-url-env PUMBILITY_DATABASE_URL `
  --player-key '<LOCAL_PUBLIC_PLAYER_KEY>'
```

Then reconcile:

```powershell
Set-Location C:\Users\jfung\Downloads\piu_misgrade_bundle
& .\.venv\Scripts\python.exe .\scripts\reconcile_pumbility_supabase.py `
  --database-url-env PUMBILITY_DATABASE_URL `
  --source-root .local-data/piu-scores `
  --baseline .local-data\pumbility-migration\local-self-review\baseline-manifest.json
```

The reconciler exits nonzero for any unexplained row mismatch. Never substitute an ad hoc SQL
import or suppress a mismatch percentage.

## Start the existing app in local mode

In a separate PowerShell window:

```powershell
Set-Location C:\Users\jfung\Downloads\piu_misgrade_bundle
$env:PIU_LOCAL_ANALYSIS = '1'
npm run dev:local
```

This mode serves privacy-checked aggregate and recommendation files from `.local-data`; live player
refresh remains disabled. It is the unchanged UI/API baseline. The Python FastAPI/worker adapter
uses `PUMBILITY_DATA_BACKEND=supabase` for database reads after reconciliation, or `shadow` for
legacy-primary mirroring. The default remains `vercel`. Database variables must remain unprefixed;
never add a Supabase service-role key under `NEXT_PUBLIC_*`.

After the local stack has been backfilled and reconciled, the guarded launcher starts both the
existing UI and a separate Supabase-backed FastAPI surface without writing credentials to disk:

```powershell
Set-Location C:\Users\jfung\Downloads\piu_misgrade_bundle
& .\scripts\start-pumbility-local.ps1
```

The resulting local surfaces are `http://localhost:3000` for the existing UI,
`http://localhost:3001/api/recommendations/players` as one Supabase-backed API endpoint, and
`http://localhost:54323` for Supabase Studio. Port 3001 intentionally has no `/` page or interactive
API documentation; use the `/api/*` routes listed below. Logs remain under ignored `.local-data`.
The separate API is necessary on Windows because
the current Vercel multi-service development launcher emits invalid unescaped Python paths; this
does not affect deployed Vercel services or the application code.

To exercise the Python API against Supabase after reconciliation, set the server-only variables
from `.env.supabase-local.example`, set `PUMBILITY_DATA_BACKEND=supabase`, and use `vercel dev`
instead of `npm run dev:local`. Verify these unchanged routes before trying the UI:

```text
/api/analyze?mix=phoenix1
/api/analyze?mix=phoenix2
/api/tier-list
/api/recommendations/players
/api/recommendations?playerKey=<LOCAL_PUBLIC_PLAYER_KEY>
```

Do not enable live refresh or provide a PIU Scores key for this local parity pass; the offline
refresh command above creates the selected-player cache from imported rows.

Stop the Next process with Ctrl+C. Stop, but do not reset, the sibling stack when finished:

```powershell
Set-Location C:\Users\jfung\Downloads\bite-open-card-draw
npx --no-install supabase stop
```
