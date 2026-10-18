# Tier publication

Tier-only publication uses cached submitted and official records. It does not
fetch scores or update recommendations.

Start a job and follow its persisted progress:

```powershell
.venv/Scripts/python.exe scripts/publish_tiers.py --env-file .env.local
```

Resume polling after a terminal disconnect:

```powershell
.venv/Scripts/python.exe scripts/publish_tiers.py --env-file .env.local --job-id JOB_ID
```

The authenticated POST `/api/jonathan/refresh?mode=tiers` returns HTTP 202 with a
job ID. GET `/api/jonathan/tier-publication?jobId=JOB_ID` reads persisted status.
Both require the `X-Jonathan-Password` header. Keep credentials out of command
arguments, logs and committed files.

Workers checkpoint completed phases and continue through the analysis queue.
Publication checks the pinned source revisions, current tiers, recommendations
and lease inside one transaction. A conflict or failed candidate leaves the
published generation available. A completed worker job is followed by public
API and chart-dialog verification before a release is declared complete.

For a failed job, inspect its stage and safe diagnostic ID/class. Fix a confirmed
deterministic failure before starting again. Do not repeatedly trigger the old
synchronous refresh or refetch scores to repair publication.

The private release audit retains the previous tier identity and immutable
candidate/audit references. Preserve that baseline for a conditional rollback;
never overwrite a newer generation or recommendations during rollback.
