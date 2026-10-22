# Global refresh recovery

Global analysis retains queue deliveries that arrive before a different owner's
lease expires. A retained delivery waits until the observed database expiry plus
two seconds, then attempts the atomic claim again. A live owner remains protected;
terminal job deliveries acknowledge without recomputing. Lease waiting does not
publish a replacement task or reset the original queue message's retention.

Tier preparation commits General, each of the three phase inputs and each of the
eight cohorts separately. Recovery receipts are candidates until their immutable
bytes validate. Preparation progress participates in the existing stall audit;
heartbeats and repeated queue deliveries do not count as completed work. Hosted
callbacks have a 600-second work budget within the 800-second function limit.

Before recovering an incident, read current job status and published generation.
Use the protected operator incremental-refresh control for a stale, incompatible
analysis checkpoint. Its existing database operation releases only an expired
matching active job before starting the replacement. Preserve source snapshots
and validated computation caches. A tier-only refresh does not update the global
recommendation model.

Track job status through completion, then read public tier and recommendation
generations. Check one chart detail, player recommendation and Radar response.
An HTTP 200 or Celery task-success log also describes incomplete work; neither
alone establishes successful publication. Confirm scheduled operation separately
with a genuine daily run at 15:00 JST.

Focused tests cover preparation crash boundaries, stale-progress detection,
protected live owners and the pinned Vercel adapter's visibility and acknowledgment
behavior. Optional real PostgreSQL tests are documented in
`tests/test_global_refresh_recovery_integration.py`; they use a uniquely named
disposable local database and the canonical sibling schema migration.

The adapter does not expose the message creation time to the Celery task. Queue
retention remains the recovery bound; lease-deferral events must not be described
as proof that the application completed or that message retention was exhausted.
