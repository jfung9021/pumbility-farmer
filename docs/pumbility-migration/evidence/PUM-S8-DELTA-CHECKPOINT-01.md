# PUM-S8-DELTA-CHECKPOINT-01

Date: 2026-08-13 UTC / 2026-08-14 JST

Owner: Codex

## Result

The Supabase adapter no longer stores repeated whole-snapshot staging checkpoints. Each checkpoint
root stores only job/mix/timestamp/completion metadata; completed-player state and merged score rows
are written as checksum-gated per-player JSONB rows in batches at the existing 50-player boundary.
Object keys use a one-way player-ID digest. Values remain private inside the unexposed `pumbility`
schema.

Resume reads verify every root and player-row checksum, reject a missing or duplicate completed
player, and reconstruct a player-delta checkpoint. The synchronizer overlays those deltas on the
last canonical snapshot and fetches only unfinished players. Deleting a staging root also deletes
its child rows.

Vercel behavior is unchanged: in `vercel` and fail-open `shadow` modes it remains the primary and
continues storing the existing whole-snapshot checkpoint. The Supabase shadow receives only the
batched delta representation. A later Supabase-primary worker can resume from that representation.

## Verification

- Full Python suite: 243/243 passed.
- Focused post-review suite: 45/45 passed.
- Python compilation and `git diff --check`: passed.
- Regression coverage proves full-checkpoint compatibility, snapshot-free delta resume, exact
  unfinished-player fetching, compact Supabase root storage, checksum-gated reconstruction, and
  refusal of incomplete checkpoint sets.

This evidence covers the implementation and regression gate only. The first production scheduled
cycle remains separately gated and is not claimed here.
