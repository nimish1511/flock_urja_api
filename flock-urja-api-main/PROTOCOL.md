# PROTOCOL.md — Reverse-Engineering Urja Meter Ops

This document provides a technical audit of the internal architecture, network protocol, authentication mechanisms, and data models of the legacy **Urja Meter Ops** portal (`https://urja-ops.flockenergy.tech`), discovered during autonomous protocol analysis.

---

## 1. Upstream Architecture & Technology Stack

The Urja Meter Ops portal is built with **SvelteKit** and hosted on modern web infrastructure fronted by Cloudflare. Although described as an "ageing internal web portal" with "no API", inspection of client bundles and network exchanges revealed that the application is a Single Page Application (SPA) with server-side rendered entry pages that invoke internal JSON and RPC-style endpoints.

### Key Technology Findings:
- **Frontend Framework**: SvelteKit 2.x with client-side routing (`@sveltekit/preload-data="hover"`).
- **Session Layer**: Cookie-based authentication using `__Secure-better-auth.session_token`.
- **Security / CSRF**: SvelteKit built-in cross-site form submission prevention. It rejects any form POST lacking an `Origin` matching the portal domain with `HTTP 403 Forbidden: Cross-site POST form submissions are forbidden`.
- **Bundle Anatomy**:
  - `start.*.js` & `app.*.js`: SvelteKit application runtime and router.
  - `nodes/0.*.js`: Root layout and favicon/SVG assets.
  - `nodes/2.*.js`: Authenticated layout with Ops Desk user profile and sign-out handler (`/api/auth/sign-out`).
  - `nodes/4.*.js`: Distribution Transformers view (`/portal/dts`, `/portal/keys`, `/portal/export`).
  - `nodes/5.*.js`: Meters list & search view (`/portal/meters/search`).
  - `nodes/6.*.js`: Meter detail view (`/portal/meters/[id]/geo`, `/portal/meters/[id]/energy`).
  - `nodes/7.*.js`: Sign-in form (`/login`).

---

## 2. Authentication and Session Management

### Login Flow:
1. **Initial Access**: Accessing `GET /` without an active session responds with `HTTP 200` rendering the login form at URL `/login`.
2. **Form Submission**:
   - **Method**: `POST /login`
   - **Content-Type**: `application/x-www-form-urlencoded`
   - **Required Headers**:
     ```http
     Origin: https://urja-ops.flockenergy.tech
     Referer: https://urja-ops.flockenergy.tech/login
     User-Agent: Mozilla/5.0 ...
     ```
   - **Payload**:
     ```
     email=operator%40urja.local&password=urja-ops-2026
     ```
3. **Successful Response**:
   - Status: `HTTP 200 OK`
   - Body: `{"type":"redirect","status":303,"location":"/meters"}`
   - Header: `Set-Cookie: __Secure-better-auth.session_token=...; Path=/; HttpOnly; Secure; SameSite=Lax`
4. **Failed Authentication**:
   - Submitting incorrect credentials yields `HTTP 200 OK` (SvelteKit action response):
     ```json
     {"type":"failure","status":401,"data":"[{\"email\":1,\"error\":2},\"wrong@urja.local\",\"Invalid email or password.\"]"}
     ```
5. **Session Expiry / Invalidation**:
   - Unauthenticated or expired requests to internal endpoints either return `HTTP 401 Unauthorized` or redirect to `/login`.

---

## 3. Discovered Endpoints & Protocols

### A. Meter Search & Listing
- **Endpoint**: `GET /portal/meters/search?q={query}&page={page}`
- **Auth**: Session cookie required.
- **Parameters**:
  - `q`: Search string matching meter ID or hardware serial number. Empty string (`q=`) matches all meters.
  - `page`: 1-based integer page index.
- **Response Format**:
  ```json
  {
    "data": [
      {
        "meterId": "J100000",
        "serialNo": "SE33962",
        "make": "HPL",
        "phaseType": "single",
        "installStatus": "Decommissioned",
        "dtCode": "DT-001"
      }
    ],
    "total": 403
  }
  ```
- **Observations**:
  - Exactly 20 meters are returned per page.
  - Total utility meter count is **403**.

### B. Distribution Transformers (DTs)
- **Endpoint**: `GET /portal/dts?page={page}`
- **Auth**: Session cookie required.
- **Response Format**:
  ```json
  {
    "data": [
      {
        "code": "DT-001",
        "name": "Malviya Nagar DT 1",
        "feederCode": "F-001",
        "capacityKva": 100
      }
    ],
    "total": 40
  }
  ```
- **Observations**:
  - Exactly 20 DTs returned per page across 2 total pages (40 transformers).
  - Capacities observed: 63 kVA, 100 kVA, 250 kVA, 500 kVA.

### C. Meter Geolocation
- **Endpoint**: `GET /portal/meters/{meterId}/geo`
- **Auth**: Session cookie required.
- **Response Format (Success)**:
  ```json
  {
    "data": {
      "latitude": "26.938961002479868",
      "longitude": "75.83095696146852"
    }
  }
  ```
- **Response Format (Not Found)**:
  - Status: `HTTP 404 Not Found`
  - Body: `{"error":"not_found","message":"Meter not found"}`
- **Observations**: Coordinates are returned as string representations of 64-bit IEEE floating-point numbers centered around Jaipur, Rajasthan, India (`~26.9°N, 75.8°E`).

### D. Meter Consumption / Energy Time-Series
- **Endpoint**: `GET /portal/meters/{meterId}/energy`
- **Auth**: Session cookie required.
- **Response Format**:
  ```json
  {
    "data": [
      {
        "timestamp": "23/06/2026 23:30",
        "kwh": "48438.74",
        "kvah": "52313.84",
        "voltR": "226"
      }
    ]
  }
  ```
- **Observations**:
  - Timestamps follow `"DD/MM/YYYY HH:MM"` in 30-minute block increments.
  - Readings represent cumulative meter registers (active energy `kWh`, apparent energy `kVAh`) and instantaneous RMS phase voltage (`voltR` in Volts).
  - Numerical fields are serialized as strings.
  - Missing/corrupted readings may present as null, empty string, or `"—"`.
  - Number of historical intervals varies per meter (e.g. 337 readings for active meters, 8 readings for newly commissioned or decommissioned units).

### E. Cryptographic Keys & Bulk Export Protocol
One of the most significant discoveries in client bundle `node_4.*.js` is the existence of an HMAC-SHA256 authenticated bulk export interface.

1. **Key Retrieval**:
   - `GET /portal/keys`
   - Returns:
     ```json
     {
       "data": {
         "signingSecret": "I3dZPPf5CgTp7JyGNMI8i6z8LFR7TmSR"
       }
     }
     ```
2. **Signature Generation**:
   The frontend constructs a canonical string from the request components and computes an HMAC-SHA256 hex digest:
   ```javascript
   let timestamp = String(Math.floor(Date.now() / 1000));
   let stringToSign = ["GET", "/portal/export", `page=${page}`, timestamp].join("\n");
   // HMAC-SHA256 with signingSecret -> hex lowercase digest
   ```
3. **Signed Request**:
   - `GET /portal/export?page={page}`
   - **Headers**:
     ```http
     x-timestamp: 1761768650
     x-signature: 8f4b23c91e...
     ```
4. **Export Payload**:
   The frontend signs a page-number query, and the response exposes an overall record count. The adapter requests pages until the collected count matches `total`; the live portal's page behavior is verified only when the live smoke test is run and the portal is reachable:
   ```json
   {
     "data": [
       {
         "meterId": "J100000",
         "serialNo": "SE33962",
         "make": "HPL",
         "phaseType": "single",
         "installStatus": "Decommissioned",
         "installType": "Whole Current",
         "build": "legacy",
         "dtCode": "DT-001",
         "hierarchy": {
           "zone": {"name": "Jaipur Zone 1", "code": "Z-01"},
           "circle": {"name": "Circle 1", "code": "C-01"},
           "division": {"name": "Division 1", "code": "D-01"},
           "subdivision": {"name": "Subdivision 1", "code": "SD-01"},
           "substation": {"name": "Substation 1", "code": "SS-01"},
           "feeder": {"name": "Feeder 1", "code": "F-001"},
           "dt": {"name": "Malviya Nagar DT 1", "code": "DT-001"}
         },
         "geo": {
           "lat": 26.938961002479868,
           "lng": 75.83095696146852
         }
       }
     ],
     "total": 403
   }
   ```

---

## 4. Grid Hierarchy Reconstructed

The portal exposes a 7-tier distribution network model:
```
Zone (e.g. "Jaipur Zone 1" / Z-01)
 └── Circle (e.g. "Circle 1" / C-01)
      └── Division (e.g. "Division 1" / D-01)
           └── Subdivision (e.g. "Subdivision 1" / SD-01)
                └── Substation (e.g. "Substation 1" / SS-01)
                     └── Feeder (e.g. "Feeder 1" / F-001)
                          └── Distribution Transformer (DT) (e.g. "DT-001" / 100 kVA)
                               └── Smart Meters (e.g. J100000 ... J100009)
```

### Verified Entity Cardinality:
- **Total Smart Meters**: 403
- **Total Distribution Transformers**: 40 (connected to ~10 meters each)
- **Meters with Complete Coordinates**: 403 / 403 (100%)
- **Meters with Hierarchy Topology**: 403 / 403 (100%)

---

## 5. Quirks, Surprises, and Upstream Inconsistencies

1. **SvelteKit CSRF Verification**: Standard headless HTTP requests fail with `403 Forbidden` unless the `Origin: https://urja-ops.flockenergy.tech` header is explicitly provided.
2. **String-Encoded Numbers**:
   - Latitude/Longitude in `/portal/meters/{id}/geo` are strings (`"26.938961002479868"`).
   - In `/portal/export`, they are numbers under different keys (`lat`, `lng`).
   - Energy registers (`kwh`, `kvah`, `voltR`) are strings and occasionally contain non-numeric placeholders (`"—"`).
3. **Date/Time Formatting**: Timestamps in the energy endpoint use Indian standard formatting `"DD/MM/YYYY HH:MM"` without explicit timezone indicators. The physical location (Jaipur) dictates an Indian Standard Time offset of `UTC+05:30`.
4. **Dual Data Structures on Detail Pages**: In the client JavaScript for meter detail (`node_6.*.js`), SvelteKit's `page.data` supports two different representations of nameplate attributes:
   - `classData`: JSON string containing an `installed_meter` object.
   - `detail.data`: An array of `{ parameterName, parameterValue }` pairs.
5. **Silent Redirect on Expired Session**: When a session token expires, upstream requests are HTTP 303-redirected to `/login` rather than returning a clean `401 Unauthorized`. The API adapter must intercept redirects to `/login` as re-authentication triggers.

---

## 6. How the Protocol Influenced API Design

1. **Bulk Sync vs N+1 Fetching**: Rather than executing 403 individual HTTP requests to discover each meter's hierarchy and coordinates, our API service leverages `/portal/keys` and `/portal/export` to populate an in-memory index during startup or on a 15-minute TTL.
2. **Real-Time Consumption Pass-Through**: While metadata is indexed, consumption time-series are queried on-demand via `/portal/meters/{id}/energy` to ensure fresh interval readings.
3. **Data Normalization**:
   - Geolocation coordinates are normalized to strict float fields (`latitude`, `longitude`).
   - Energy readings are converted to ISO-8601 strings with timezone offset (`+05:30`) and floating-point numeric registers.
   - Consecutive cumulative-register deltas are exposed as interval consumption. Blank readings are bridged for the window total; non-30-minute intervals are not included in nominal interval statistics, and reset-crossing deltas are excluded.
   - An interval energy-ratio estimate is computed from valid 30-minute `delta kWh / delta kVAh` values. It is not an instantaneous power-factor measurement.
   - Readings are ordered by parsed timestamp. Exact duplicate instants collapse to the most complete and then latest source row; the summary reports how many rows were omitted.
   - Hierarchy assignments are reconstructed from source data and accompanied by a data-quality report; they are not treated as independently verified.
   - Nameplate attributes are extracted from `classData` and `detail.data` when those source fields are included in an export record. The API does not separately fetch every meter's detail page, so the bulk export may not contain these optional attributes.
4. **Resilient Session Lifecycle**: The upstream client transparently authenticates on initialization and intercepts 401/403 or redirect responses, re-authenticating and retrying without dropping user requests.
