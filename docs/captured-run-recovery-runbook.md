# Saved-input recovery operator runbook

This is an explicit operator recovery. Ordinary daily refreshes continue to fetch
Phoenix 2 and use the Phoenix 1 archive. No recurring monitor is enabled.

All endpoints below use the existing `X-Jonathan-Password` header. Keep its value
in the normal secret provider; never put it in a URL, committed file or log.
Responses contain safe hashes/counts, not score rows or private object paths.

## Prepare before considering cancellation

1. Verify the optimized release's production commit/deployment. Read current
   source-job status and `/api/refresh-status` once. A completed, verified source
   job should not be restarted merely to adopt a new producer. A publishing job
   is not cancellable through this recovery operation.
2. GET `/api/jonathan/captured-recovery` without an intent ID returns the active
   job, serving revision and any admission reservation.
3. POST `/api/jonathan/captured-recovery/prepare` with exactly these JSON fields:
   `sourceJobId`, `expectedManifestSha256`, `sourceDeploymentId`, `expectedHead`,
   `expectedServingRevision`, and a fresh `idempotencyKey`. Expected head must be
   the source job ID. Use the actual verified source deployment and recorded
   complete manifest identity, not a guessed/latest source.
4. Save the returned intent/bundle IDs and safe inventory. This exports validated
   copies only. It does not cancel, fetch, fit or publish. Compare remaining
   retained fits against cancellation/drain, adoption, unmatched fitting and
   assembly costs. Prefer finishing when switching is not demonstrably useful.

The bundle retains original source epochs/fetch times, the privately synchronized
snapshot, base rows/configuration, catalog/policy and compatible numerical
references. It survives abandoned original checkpoint cleanup. Preparation fails
for changed/corrupt inputs or later local consent revocations. It never silently
substitutes the previous published snapshot.

## Execute only after a favorable fresh decision

POST `/api/jonathan/captured-recovery/execute` with `{"intentId":"..."}`.
The first successful call reserves global analysis admission and conditionally
cancels the expected source job. It returns promptly in `draining` state and does
not start replacement fits. A competing owner, changed serving revision or a
publishing/completed source prevents that cancellation.

The configured old invocation maximum is 800 seconds. No replacement is queued
until that database-clock bound after cancellation has elapsed, the old job is
terminal, its owner/lease is absent, and final publication revisions reconcile.
Read GET `/api/jonathan/captured-recovery?intentId=...` for saved state. Invoke the
same execute operation after the drain deadline to attempt admission. Repeated
execute requests retain the same replacement job/task identity.

Do not treat cancellation acceptance or a healthy heartbeat as completion. An
old pointer transaction already entered can finish during drainage. A changed
final serving revision blocks admission and requires explicit reconciliation.
Never clear cancellation flags or start an ordinary competing refresh.

The replacement adopts saved inputs with zero upstream calls. It records its own
bounded row-copy checkpoints, reuses only verified matching numerical components,
and resumes the retained four-cohort producer. Original seeds and 200-sample SSS+
bootstrap policy remain intact. Local source/consent drift blocks canonical
snapshot adoption; cancellation, owner loss or later source drift blocks the
atomic tier/model publication.

Canonical adoption also commits a private job/bundle/snapshot receipt in the
import transaction. If a callback ends before its next runtime checkpoint, an
exact retry recognizes its own committed import and avoids importing twice.
Different inputs, a later source revision or later consent changes still block.

## Abort and verify

For a waiting/blocked handoff, use POST
`/api/jonathan/captured-recovery/abort` with `intentId` and the freshly observed
`expectedServingRevision`. Abort only succeeds after the old callback's drain
gate and a safe waiting-replacement state. Never manually delete reservation
rows to bypass an unknown old callback.

After admission, follow the normal analysis job status through completed state.
Verify tier/global-model publication from that run, current General/SSS+ list
and lazy chart detail, Transmission D25 and Pastry mix D25 evidence/estimates,
one recommendation's stale/current-model contract and Radar. Original source
dates must remain original. A genuine scheduled daily cron is separate evidence.

If a replacement fails, retain the last complete serving generation and the
bundle. Do not revive its cancelled predecessor. Any subsequent attempt requires
another safe validated intent and must not overlap a live owner.
