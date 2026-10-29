# Urja Meter Ops API

A REST API service (FastAPI, Python) that sits in front of the legacy **Urja Meter Ops** web portal (`https://urja-ops.flockenergy.tech`) and exposes its data as clean, typed JSON: meter details, geolocation, 30-minute consumption readings, distribution transformers and the network hierarchy. A small single-page dashboard is served on top of the API.

> **Local development service.** All `/api/v1` routes require a bearer token configured through `API_ACCESS_TOKEN`; the dashboard asks for that token and keeps it in the current browser session. Keep the server bound to loopback for local use. A shared bearer token is not a substitute for production identity, authorization, secret rotation, network controls, TLS, or a production CORS policy; do not expose this service publicly as-is.

**Where to find everything**

| What | Where |
|---|---|
| How the portal actually works (auth, endpoints, signing, quirks) | [PROTOCOL.md](PROTOCOL.md) |
| OpenAPI 3.1 description of *this* API | [openapi.json](openapi.json) (also served live at `/openapi.json`, rendered at `/docs` and `/redoc`) |
| Reflection answers | [REFLECTION.md](REFLECTION.md) |
| Assumptions, trade-offs, skipped items, known issues, next steps | Sections below |

---

## 1. What I built and how it is structured

On startup (and whenever the cache is older than the TTL) the service logs into the portal, fetches and validates every page of the signed `/portal/export` response plus the transformer list (`/portal/dts`), and builds an in-memory index. Metadata endpoints are answered from that index. Consumption readings are **not** cached: each call goes live to the portal.

```
app/
  main.py              FastAPI app, lifespan (startup sync), error handlers, dashboard route
  config.py            Settings from env / .env (pydantic-settings)
  core/
    upstream_client.py Portal client: login, CSRF headers, re-auth, HMAC signing, fetch helpers
    cache.py           In-memory DataStore: sync, indexes, filtering, nearby search, hierarchy tree
  models/
    schemas.py         Pydantic response models (these drive openapi.json)
    errors.py          Upstream exception types and the JSON error envelope
  routers/
    meters.py          list/search, nearby, detail, consumption (+ timestamp/number normalisation)
    transformers.py    list, detail with connected meters
    hierarchy.py       hierarchy tree
    admin.py           sync status, forced sync
    health.py          health check
  static/dashboard.html  single-page UI
tests/                 pytest suite (mocked upstream) + one live smoke test
```

### Endpoints

| Method & path | Purpose |
|---|---|
| `GET /health` | Service status, whether the portal is reachable, whether the cache is populated |
| `GET /api/v1/meters` | Paginated list. `q` (substring of meter ID or serial), `make`, `phase_type`, `install_status`, `dt_code`, `page`, `page_size` (default 20, max 100) |
| `GET /api/v1/meters/nearby` | Meters within `radius_km` (0.01 to 50, default 1) of `latitude`/`longitude`, sorted by distance (Haversine) |
| `GET /api/v1/meters/{meter_id}` | Meter detail with coordinates, source nameplate attributes (when present), and hierarchy. Also accepts a hardware serial number |
| `GET /api/v1/meters/{meter_id}/consumption` | Cumulative registers plus consecutive interval deltas, ISO-8601 timestamps, and interval-based summaries |
| `GET /api/v1/transformers` | Paginated list, optional `feeder_code` filter |
| `GET /api/v1/transformers/{code}` | Transformer with its connected meters |
| `GET /api/v1/hierarchy` | Reconstructed Zone → Circle → Division → Subdivision → Substation → Feeder → DT tree, meter counts, and a source data-quality report |
| `GET /api/v1/sync/status` | Cache age, counts, staleness |
| `POST /api/v1/sync` | Force a cache refresh, subject to the configured cooldown |
| `GET /` and `/dashboard` | Web dashboard |

Errors use one envelope: `{"error": {"code": "...", "message": "...", "details": ...}}`. Portal-side failures map to 404 (not found), 504 (timeout), 502 (portal auth failure) or the upstream status.

### Dashboard

`app/static/dashboard.html` is one file served by the API. It has a searchable/filterable meters table with pagination, a transformers table, a hierarchy tree view, and a meter detail modal with a Chart.js consumption chart and readings table. It loads Tailwind, Chart.js and Font Awesome **from public CDNs**, so it needs internet access in the browser. It has no map and no UI for the nearby search.

---

## 2. Install, configure, run

Requires Python 3.10+ (developed and tested on newer versions).

```bash
git clone <this-repo-url>
cd <repo-folder>

python3 -m venv venv
source venv/bin/activate            # Windows: .\venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                # set PORTAL_PASSWORD and a unique API_ACCESS_TOKEN
python -m app                       # binds using HOST and PORT from .env
```

Open:
- Dashboard: http://127.0.0.1:8000/dashboard
- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc
- Spec: http://127.0.0.1:8000/openapi.json

### Configuration (environment variables or `.env`)

| Variable | Default | Meaning |
|---|---|---|
| `PORTAL_BASE_URL` | `https://urja-ops.flockenergy.tech` | Portal to wrap |
| `PORTAL_USERNAME` | `operator@urja.local` | Portal login |
| `PORTAL_PASSWORD` | the assignment password (see `app/config.py`) | Portal password. `.env.example` contains a placeholder; replace it, or the placeholder string will be used and login will fail |
| `API_PREFIX` | `/api/v1` | Route prefix |
| `CACHE_TTL_SECONDS` | `900` | Cache age after which the next request triggers a refresh |
| `AUTO_SYNC_ON_STARTUP` | `true` | Sync at boot. If it fails, the service still starts and retries on the first request |
| `UPSTREAM_TIMEOUT_SECONDS` | `15.0` | Per-request timeout to the portal |
| `UPSTREAM_MAX_RETRIES` | `3` | **Declared but not currently used by any code** |
| `HOST`, `PORT`, `DEBUG` | `127.0.0.1`, `8000`, `false` | Server settings; keep `HOST` loopback-only for local use |
| `API_ACCESS_TOKEN` | unset (API requests fail closed) | Unique bearer secret, at least 16 characters, required for every `/api/v1` route |
| `SYNC_STATE_DB_PATH` | `.sync_state.sqlite3` in the project directory | SQLite state shared by local worker processes to coordinate forced-sync locking and cooldown |
| `FORCED_SYNC_COOLDOWN_SECONDS` | `60` | Minimum time between successful manually forced syncs |

### Tests

```bash
pytest -v
```

The tests run against a mocked portal client for meters, filters, pagination, nearby search, consumption, transformers, hierarchy, cache sync, and health. `tests/test_live_smoke.py` calls the real portal read-only; it is skipped if the portal is unreachable.

All API examples below require the configured token:

```bash
curl -H "Authorization: Bearer $API_ACCESS_TOKEN" \
  "http://127.0.0.1:8000/api/v1/meters?make=HPL&page=1&page_size=2"
```

---

## 3. Sample requests

```bash
curl -H "Authorization: Bearer $API_ACCESS_TOKEN" "http://127.0.0.1:8000/api/v1/meters?make=HPL&phase_type=single&page=1&page_size=2"
```
```json
{
  "items": [
    {"meter_id": "J100000", "serial_no": "SE33962", "make": "HPL", "phase_type": "single",
     "install_status": "Decommissioned", "dt_code": "DT-001",
     "install_type": "Whole Current", "build": "legacy"}
  ],
  "total": 41, "page": 1, "page_size": 2, "pages": 21
}
```

```bash
curl -H "Authorization: Bearer $API_ACCESS_TOKEN" http://127.0.0.1:8000/api/v1/meters/J100000
curl -H "Authorization: Bearer $API_ACCESS_TOKEN" http://127.0.0.1:8000/api/v1/meters/J100000/consumption
curl -H "Authorization: Bearer $API_ACCESS_TOKEN" "http://127.0.0.1:8000/api/v1/meters/nearby?latitude=26.9389&longitude=75.8309&radius_km=0.5"
curl -H "Authorization: Bearer $API_ACCESS_TOKEN" http://127.0.0.1:8000/api/v1/transformers/DT-001
curl -H "Authorization: Bearer $API_ACCESS_TOKEN" http://127.0.0.1:8000/api/v1/hierarchy
```

(Example values are from a previous run against the live portal; actual numbers depend on portal data.)

---

## 4. How the portal works (summary)

Full detail is in [PROTOCOL.md](PROTOCOL.md). In short: the portal is a SvelteKit app with cookie-session login (`POST /login` as form data, which needs an `Origin` header or SvelteKit's CSRF check returns 403). Behind the UI are JSON endpoints under `/portal/` for meter search, DTs, per-meter geo and per-meter energy. A signing secret from `/portal/keys` is used to HMAC-SHA256 sign paged requests to `/portal/export`, which returns meter hierarchy and coordinates without per-meter metadata requests.

---

## 5. Assumptions

1. **Timezone is IST (UTC+05:30).** Portal timestamps (`DD/MM/YYYY HH:MM`) carry no timezone. Coordinates are around Jaipur and the hierarchy uses Indian DISCOM terms, so I attach `+05:30`. This is an inference, not something the portal states.
2. **`kwh` and `kvah` are cumulative registers**, because values increase over time. I did not independently confirm this beyond the data pattern.
3. **Meter metadata changes slowly**, so a 15-minute TTL is an acceptable freshness trade-off. Consumption is treated as live and fetched per request.
4. **The portal is read-only for me.** The service only issues the login POST and GET requests.
5. **The export supports page-number pagination.** Every page is fetched and its declared `total` is checked for consistency against the final record count. The cache also independently rejects exports with mismatched counts.
6. **Placeholder values** (`""`, `"—"`, `null`) in numeric fields mean "missing" and are returned as `null`.

---

## 6. Design decisions and trade-offs

- **Bulk export instead of per-meter calls.** Signed, paginated requests replace hundreds of per-meter metadata calls. Trade-off: the signing scheme was reverse-engineered from the portal's JS and could change without notice.
- **In-memory store, no database.** Zero dependencies and fast at ~400 meters. Trade-off: state is per process, lost on restart, not shared between workers.
- **Lazy TTL refresh.** The cache is refreshed by whichever request first finds it stale (guarded by an `asyncio.Lock` with a re-check, and swapped in atomically). There is no background refresher, and if a refresh fails the request fails rather than serving the old data.
- **Cache-miss behaviour.** Unknown meter IDs and transformer codes return 404 from the current cache; they do not bypass the TTL with a forced upstream refresh. An operator can call `POST /api/v1/sync`, with a cooldown to limit upstream load.
- **Consumption passes through live.** Always current, but adds load on the portal and latency per call, and is not cached.
- **Local search and filtering** over the cached data rather than calling the portal's search, which allows multi-attribute filters and nearby search that the portal does not offer.
- **Normalised output.** snake_case fields, floats instead of strings, ISO-8601 timestamps with offset, one error envelope.
- **Session handling.** The client logs in lazily; on a 401/403 or a redirect to `/login` it re-authenticates and retries the request once.

---

## 7. What I intentionally skipped, and why

- **Production identity and authorization.** A shared bearer token protects API routes for this local service; it is not user identity or role-based authorization. Add appropriate production authentication, authorization, network controls, and TLS before deployment.
- **A database / persistent store and time-series storage.** Not needed at this data size; consumption is not stored.
- **Background refresh, webhooks or streaming.** Adds moving parts beyond the brief.
- **Per-meter detail-page fetching for nameplate attributes.** The API maps `classData` and `detail.data` if they are present in an export record, but does not make an additional detail-page request for every meter; those attributes may therefore be `null` on live data. This is intentionally scoped out rather than claiming the bulk export always provides nameplate details.
- **Retries and backoff for 5xx or network errors.** Only the single re-auth retry exists.
- **A map view, anomaly/health summaries and a UI for nearby search** in the dashboard.
- **Rate limiting and caching of consumption responses.**

---

## 8. Known issues and limitations (found in review)

I would rather list these than have them discovered:

1. **Consumption interval classification.** Blank or missing readings are bridged using the nearest available prior register and the next valid register, so the resulting delta contributes to `total_kwh`. The longer elapsed time is reported as `time_gap` and excluded from nominal 30-minute average/min/max statistics. Deltas across resets are excluded. Duplicate timestamps are collapsed, preferring the most complete and then latest row.
2. **Interval power factor is an estimate.** It is derived from consecutive active/apparent-energy deltas only when both support the interval; it is not an instantaneous measurement.
3. **API authentication uses a shared bearer token.** It is basic local-service authentication, not production identity/authorization. Forced sync is additionally cooldown-limited across local worker processes using the configured SQLite state file; failed attempts release their reservation and do not consume the cooldown. A crashed worker's reservation expires after at least five minutes.
4. **Live export pagination is portal-dependent.** Pagination and signature behavior have mocked tests; `tests/test_live_smoke.py` also checks the live export's returned count when the portal is reachable. The upstream may change its paging contract without notice.
5. **Auth handling to the upstream portal is basic.** Login failure is detected by looking for the word `failure` in the response body; a missing session cookie only logs a warning; there is no lock around concurrent re-authentication; the cached signing secret is not cleared on auth errors; `UPSTREAM_MAX_RETRIES` is unused.
6. **Scale:** filtering, nearby search and transformer lookup are linear scans over in-memory lists. Fine for ~400 meters and 40 transformers; there is no benchmark beyond that.
7. **Dashboard depends on public CDNs** (Tailwind, an unpinned Chart.js, Font Awesome).
8. **Credentials:** the assignment's portal credentials are hardcoded as defaults in `app/config.py` and appear in the docs.

### Where the approach would struggle at larger scale

Roughly: the full-export-into-memory approach is fine into the low tens of thousands of meters. Beyond that the refresh time, memory per worker, and O(n) scans would hurt. I would then move to a database (PostgreSQL with PostGIS or a spatial index for "nearby"), refresh incrementally in a background job, and share state across workers. I have not measured where the actual limit is.

---

## 9. What I would improve with more time

1. Replace the shared local bearer token with production authentication and authorization before deployment.
2. Serve stale data if a scheduled cache refresh fails.
3. Real retry/backoff with jitter and a circuit breaker; guard re-auth with a lock.
4. Move credentials entirely to environment configuration.
5. Tests for session expiry, login failure, and malformed source data.
6. Vendor the dashboard assets, add a map and a nearby-search UI.
7. A short-lived consumption cache and a concurrency limit towards the portal.

---

## Reflection

Answers to the five reflection questions are in [REFLECTION.md](REFLECTION.md).
