# Application behavior contract

Existing behavior is frozen for the persistence migration. This is not authorization to alter API shapes, statistical methodology, cache policy, UI ordering, local behavior, or operator workflows.

## Production routing boundary

`vercel.json` rewrites `/api(/.*)?` to the FastAPI service in `api_service.py`; other requests go to Next.js. Production API behavior therefore comes from `api/*.py`, not `app/api/*`.

### Production endpoints

| Method/path | Inputs/authentication | Current outcomes and headers |
| --- | --- | --- |
| `GET /api/analyze` | `mix=phoenix2` default; optional `jobId` | Latest payload 200; missing latest/job or mix mismatch 404; bad mix 400; storage unavailable 503; safe 500. Phoenix 1 without job is 307 to `/data/phoenix1.json`; with job it is 410 archived. No explicit cache header. |
| `POST /api/analyze` | `mix`, `fullSync=false`; `X-Analysis-Run-Secret` must match `CRON_SECRET` | Unconfigured 503; unauthorized 401; started/existing 202; possible fresh 200; busy/archived 409; bad input 400; queue/runtime 503; safe 500. |
| `POST /api/analyze/cancel` | `jobId`; exact `Authorization: Bearer <CRON_SECRET>` | Unauthorized 401; missing ID 400; absent job 404; completed/cancelled 200; safe 500. Cancellation marks the job failed, sets `cancelRequested`, and releases the active pointer. |
| `GET /api/cron` | `mix=phoenix2`; exact Bearer secret | Unauthorized 401; otherwise same refresh coordination outcomes; bad mix 400, runtime 503, safe 500. |
| `GET /api/cron/{mix}` | Path mix; exact Bearer secret | Delegates exactly to `/api/cron`; Phoenix 1 is 409 archived. |
| `POST /api/deploy` | `mix`; HMAC-SHA1 of raw body in `x-vercel-signature` using `VERCEL_DEPLOY_WEBHOOK_SECRET` | Unauthorized 401; malformed JSON/payload 400; unrelated event/project 202 ignored; Phoenix 2 promotion 202 ignored with deployment ID; Phoenix 1 409 archived; safe 500. It never queues analysis. |
| `POST /api/jonathan/refresh` | `mode=incremental|full`; `X-Jonathan-Password` | Unconfigured 503; unauthorized 401; invalid enum 422; refresh outcomes as returned by coordination. Every response explicitly has `Cache-Control: no-store`. Incremental forces a run but retains the watermark; full also discards it. |
| `GET /api/tier-list` | None | Combined payload 200; missing 404; storage unavailable 503; safe 500. No explicit cache header. |
| `GET /api/recommendations/players` | None | Compact list 200 with `public, max-age=30, s-maxage=30, must-revalidate`; missing 404, unavailable 503, safe 500 with `no-store`. |
| `GET /api/recommendations` | Required `playerKey` | Missing key 400; missing index/player/cache 404; incompatible cache 404 plus `refreshRequired`; selected result 200. Schema-3 and legacy success responses use `no-store`; current validation/error branches without an explicit header must not silently gain a different contract during migration. |
| `POST /api/recommendations/refresh` | Required `playerKey` | Missing key 400; model disabled/unavailable 503; player absent 404; fresh 200; started/existing 202; safe failure 500. All explicit responses use `no-store`. |
| `GET /api/recommendations/refresh` | Required `jobId` | Missing ID 400; absent/non-player job 404; job 200; storage failure 503. A queued/running job with no update for 30 seconds is returned as failed/retryable. All responses use `no-store`. |
| `POST /api/recommendations/rollback` | `generationKey`; `X-Analysis-Run-Secret` | Unconfigured 503; unauthorized 401; invalid key 400; missing generation 404; incomplete generation 409; success 200; failure 503. All responses use `no-store`. |

FastAPI disables OpenAPI, Swagger, and ReDoc. Unhandled exceptions return JSON 500 `{"error":"The analysis service failed unexpectedly."}`.

### Refresh coordination

- Successful-run freshness is currently zero; the compatible `fresh` global response exists but is not emitted by the current setting.
- Only one global queued/running job is coordinated.
- Same-mix requests follow the existing job; a different mix is busy.
- Global jobs without an update for more than five minutes are failed and released.
- Job status is retained for 24 hours.
- Queue submission failure records a safe failed job and five-minute retry.
- Duplicate queue delivery of completed/cancel-requested global work returns existing state without work.
- Phoenix 1 is rejected by manual, cron, worker, and publication paths.

### Selected-player refresh

- The feature is enabled only when both the environment switch and the current schema-3 pointer permit it.
- Freshness/deduplication is 60 seconds and checks both the current and previous minute job IDs.
- Cached content is served before browser-initiated refresh.
- The worker fetches the complete Phoenix 2 best-score endpoint without `recordedAfter`.
- Incoming rows are catalog-filtered and best-row merged; missing response rows do not delete stored rows.
- If the model generation changes during the request, all frozen inputs switch together once.
- Failed refresh leaves the previous cached recommendation available.
- Worker failures are acknowledged as safe failed jobs instead of queue redelivery.

## Standalone local API boundary

When running Next.js without the Vercel production rewrite, `app/api/*` is a separate contract controlled by server-side `PIU_LOCAL_ANALYSIS=1`.

| Handler | Current behavior |
| --- | --- |
| `GET app/api/analyze` | Bad mix 400. With local mode off, Phoenix 1 redirects 307 to the archive and Phoenix 2 returns 404 disabled. With local mode on, reads the requested disk aggregate; success uses `no-store, max-age=0`, missing 404, validation 422, safe 500. |
| `GET app/api/tier-list` | Local mode off 404. Reads combined disk result; success `no-store, max-age=0`, missing 404, validation 422, safe 500. |
| `GET app/api/recommendations` | Local mode off 404. Requires a player key or finite rating 1–40. Manual rating exists only here. Success `no-store, max-age=0`; missing 404, validation 422, safe 500. |
| `GET app/api/recommendations/players` | Local mode off 404. Success uses `public, max-age=300, s-maxage=300, stale-while-revalidate=3600`; errors are `no-store`. |
| `GET app/api/recommendations/refresh` | Local mode off 404; local mode on 404 because live refresh is unavailable. |
| `POST app/api/recommendations/refresh` | Local mode off 404; local mode on 503 because live refresh is unavailable. |

The backend production player list is 30 seconds; the standalone local list is five minutes. That difference is current executable behavior and must be tested rather than normalized during migration.

## Browser routes and workflows

### `/`

- Leads with Recommendations and Tier List feature cards.
- Provides the PIU Scores sync/upload link and consent/privacy explanation.
- Does not expose the operator route.

### `/tier-list`

- Fetches `/api/tier-list` with `cache: no-store` unless demo mode is selected.
- Demo mode is `?demo=1` or `NEXT_PUBLIC_DEMO_MODE=1` and uses `lib/demo-data.ts`.
- Defaults to Singles, estimated-difficulty grouping, and compact layout.
- Singles and Doubles filters remain independent.
- Supports song/step-artist search, official-level filter, estimated/tier grouping, compact/detailed layout, and accessible chart detail dialog.
- Estimated difficulty is truncated to one decimal, not rounded.
- Charts with fewer than 20 contributors show Limited data.
- Chart video links and Phoenix 1 rerates remain presentation-only.
- Detailed chart cards and compact-layout detail dialogs share the same per-chart what-if control. Its unset prompt is `S??` for Singles and `D??` for Doubles; selecting an available alternative renders `If S<level> then S<estimate>` or `If D<level> then D<estimate>`.
- The control reads the `whatIfEstimates` already present on the chart. The artifact stores only the adjacent official levels (one below and one above, never below level 16). It performs no fetch and does not regroup, rerank, move, or otherwise recalculate the tier list.
- Alternatives with no estimate remain visible but disabled. The control is optional-data safe for older combined artifacts and resets when its chart details unmount.
- What-if text occupies the unused right side of the existing difference row with out-of-flow positioning. It must not change detailed-card or dialog grid tracks, padding, minimum heights, or the placement of existing chart metadata, difference, and confidence interval content.

### `/recommendations`

- Loads the compact player list and restores a valid `player` query parameter.
- Defaults to Overall, followed by Single and Double tabs.
- Selected player is reflected with `history.replaceState`.
- Loads cached recommendations first, then attempts refresh when supported.
- Polls selected-player jobs every second for at most 30 seconds.
- A refresh error does not replace an already displayed cache with an error state.
- Official-level options derive from the complete mode-specific candidate pool; unfiltered mode shows at most the top 20.
- Each player mode exposes `topScores`: the exact retained Phoenix 2 Pumbility rows used by that mode's `currentTop50Pumbility`, ordered by Pumbility, raw score, and chart ID and capped at 50. Single and Double use independent pools; Overall uses the shared S+D pool. These public rows contain allowlisted chart/display metadata, authoritative Pumbility, and server-derived grade and normalized plate only; raw score, player ID, timestamps, broken-score state, and full score history remain private.
- Overall progress uses rank emblems; Single/Double use title ladders.
- Arrow/Home/End tab navigation and current accessibility labels remain.

### `/jonathan`

- Is unlinked and declares `noindex,nofollow`.
- Requires an entered password but never stores it.
- Full refresh requires browser confirmation.
- Active job ID is retained in local storage under `analysisJobId:phoenix2`.
- Polls every two seconds in a visible tab and ten seconds in a hidden tab.
- Shows progress, safe error, and retry countdown.

## Demo and local behavior variances

- The demo payload fixes seven effect bands, the 0.4 scale, level-16 display floor, and controlled mode rows.
- Local analysis validation rejects credential-shaped values and private keys including player ID, username, game tag, authorization, API key, and token.
- Local recommendations require schema 22 and reject private fields/full score arrays.
- A legacy Phoenix 2 disk aggregate without mix metadata is normalized only for the default Phoenix 2 read.
- Current React source does not render the README-described “Local snapshot” badge or “Reload local results” button. Those are not migration acceptance requirements unless implemented in a separately approved product change.

## Executable evidence map

- Production routes/jobs/publication/retention: `tests/test_analysis_runtime.py`.
- Sync timing, overlap, consent, checkpoint, deterministic winner: `tests/test_phoenix2_sync.py`.
- Analysis: `tests/test_analyzer.py`.
- Recommendation and combined behavior: `tests/test_recommendations.py`.
- Formula/plate behavior: `tests/test_phoenix2_pumbility.py`.
- Local snapshot: `tests/test_local_snapshot.py`.
- Frozen archive/rerates/videos: `tests/test_phoenix1_archive.py`, `tests/test_phoenix1_rerates.py`, `tests/test_nevsister_chart_links.py`.
- Local/demo/frontend behavior: `tests/frontend-api.test.ts`.

Adapter and cutover work must add store-parity and golden-response tests without replacing these methodology tests.
