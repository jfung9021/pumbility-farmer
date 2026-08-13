# Migration evidence handling

This directory contains indexes and safe links, not raw production evidence. Private evidence must remain in access-controlled operational storage or under the ignored `.local-data/pumbility-migration/<boundary>/` directory.

Allowed committed evidence:

- Aggregate counts that cannot identify an individual.
- Whole-dataset SHA-256 values for public artifacts.
- Whole-dataset HMAC-SHA256 values for private artifacts.
- Schema versions, methodology versions, test names, CI run links, and redacted screenshots.
- Reconciliation results containing mismatch paths and value digests.

Never commit:

- Upstream or internal player identifiers.
- Public player keys on a per-person basis.
- Usernames or display names.
- Raw or per-player scores, grades, plates, or timestamps.
- Per-player digests.
- Credentials, authorization headers, database URLs, or private Storage paths.
- Production exports, recommendation input shards, player state, or model binaries.

The same private `PUMBILITY_BASELINE_HMAC_KEY` must be available to both source and candidate captures. It must contain at least 32 bytes and must never be written to an artifact or log.

The explicit production collector writes exactly `baseline-manifest.json` and `private-evidence.json` beneath the ignored `.local-data/pumbility-migration/<boundary>/` directory. It emits no Blob object locations, generation identifiers, player identifiers, usernames, raw scores, model bytes, or HMAC key. The operator must move or reference the private evidence through the approved access-controlled evidence store; it must never be committed.

Screenshots must use synthetic/local data or be redacted before their checksums and locations are added to the evidence index. A checkbox without an evidence reference is incomplete.
