# REFLECTION.md — Engineering Reflection

Answers to the five evaluation questions based on the design, reverse-engineering, and implementation of the Flock Energy Urja Meter Ops API service.

---

### 1. What assumptions did you make?

1. **Timezone Offset (+05:30 IST)**: Upstream consumption timestamps are delivered as strings like `"23/06/2026 23:30"` without timezone indicators. Because the geographic coordinates of all 403 meters are clustered around Jaipur, Rajasthan, India (`26.9°N, 75.8°E`), and the distribution utility uses Indian state terminology (DISCOM grid tiers: Zone, Circle, Division, Subdivision, Feeder, DT), I assumed an Indian Standard Time offset of `UTC+05:30`.
2. **Cumulative Meter Registers**: Energy readings contain increasing values of `kwh` and `kvah` across time for a given meter (e.g. `48430.00` to `48438.74`). They are treated as cumulative registers. Consumption is calculated from consecutive usable readings, bridging missing values for the period total while excluding non-30-minute spans from nominal interval statistics. Deltas across resets are excluded.
3. **Cache Invalidation Frequency**: The utility's meter asset metadata (nameplate details, physical phase wiring, transformer association, and GPS location) changes infrequently compared to high-frequency consumption telemetries. I assumed a 15-minute (900 seconds) cache TTL for meter and transformer metadata balances freshness against upstream load.
4. **Read-Only Upstream Operation**: As instructed by the prompt and assignment rules, all upstream interactions are strictly non-destructive and read-only. No operational states on the portal are mutated.

---

### 2. Which part was the most difficult, and how did you get unstuck?

The most challenging phase was initial automated communication with the portal's `/login` route. Initial HTTP POST requests using standard Python tooling failed immediately with `HTTP 403 Forbidden: Cross-site POST form submissions are forbidden`.

**How I got unstuck**:
Rather than treating the portal as a generic black box, I analyzed the frontend JavaScript bundles loaded by the SvelteKit application (`start.*.js`, `app.*.js`). SvelteKit includes built-in protection against Cross-Site Request Forgery (CSRF) for form submissions by checking that incoming request headers contain an `Origin` matching the server's public URL. By explicitly injecting `Origin: https://urja-ops.flockenergy.tech` and `Referer: https://urja-ops.flockenergy.tech/login`, the request immediately succeeded with `HTTP 200` and returned the `__Secure-better-auth.session_token` cookie along with the 303 redirect payload.

A secondary challenge was discovering how to access the complete grid hierarchy efficiently without executing 403 individual HTTP requests. By inspecting bundle `node_4.*.js`, I discovered the cryptographic signing function `C(method, path, query, secret)` and the paginated export endpoint `/portal/export`, allowing us to retrieve the meter dataset and 7-tier topology through signed bulk requests.

---

### 3. If you had another day, what would you improve?

If granted another day, I would focus on:
1. **Background Polling & Change Detection Worker**: Implement an asynchronous background worker (using Celery, Redis, or asyncio background task) that periodically polls `/portal/export` and `/portal/meters/{id}/energy`, computes hash diffs against the previous snapshot, and emits webhook events or WebSocket notifications when meter anomalies occur (e.g., status changes to `Faulty`, voltage drops below threshold, or communication timeouts).
2. **Persistent Time-Series Database**: Replace in-memory storage of consumption readings with a dedicated time-series store (such as TimescaleDB or DuckDB) to support long-term interval storage, rolling aggregations, downsampling, and sub-second analytical queries over millions of readings.
3. **Production Authentication & RBAC**: Replace the local shared bearer token with OAuth2 / JWT identity and role-based access control (e.g., Field Technician vs Data Scientist) before exposing utility infrastructure data beyond local development.
4. **Resilience & Rate-Limiting Circuit Breaker**: Introduce a circuit breaker (e.g. using `tenacity` or `pybreaker`) with exponential backoff and jitter to protect the legacy portal from cascading failure if it experiences elevated load or downtime.

---

### 4. What mistake did you make while solving this?

When implementing the test suite and running `TestClient(app)` against asynchronous endpoints, I initially encountered an `AssertionError` in pytest-asyncio due to mixing async fixture hooks with synchronous pytest 9.2 runner teardown, followed by `RuntimeError: Event loop is closed` when `portal_client._client` was shared across distinct asyncio loops created by Starlette's synchronous TestClient.

**How I resolved it**:
I refactored `UpstreamPortalClient.get_client()` to dynamically inspect `asyncio.get_running_loop()`. If the active event loop changes or the client is closed, a new `httpx.AsyncClient` is instantiated and bound to the current running loop. In addition, I adjusted the fixture in `conftest.py` to run synchronously via `asyncio.run()`, making test execution deterministic and clean across the suite.

---

### 5. If you were reviewing your own submission, what would you criticise?

1. **In-Memory Cache vs Distributed Cache**: For this take-home scope with 403 meters and 40 transformers, an in-memory dictionary is exceptionally fast (sub-millisecond lookups) and zero-dependency. However, in a multi-worker production environment (`uvicorn --workers 4`), each process maintains its own cache instance without synchronization. In production, this should be backed by a centralized Redis instance.
2. **Synchronous Consumption Pass-Through**: While metadata is indexed, calling `/api/v1/meters/{id}/consumption` executes a live request to the upstream portal. If 50 external clients concurrently request consumption for different meters, the legacy portal might experience spikes. Adding a short-lived cache (e.g. 60 seconds) for consumption endpoints would safeguard upstream capacity.
3. **Simulated Missing Parameter Fields**: The legacy portal detail pages occasionally contain varying nameplate parameters (`classData` vs `detail.data`). While our client handles both, our unit tests use sanitized sample payloads that don't cover every esoteric parameter combination that a legacy hardware vendor might emit.
