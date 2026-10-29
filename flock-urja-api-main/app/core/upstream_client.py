import hashlib
import hmac
import logging
import time
from typing import Any, Dict, List, Optional
import httpx

from app.config import settings
from app.models.errors import (
    UpstreamAuthError,
    UpstreamError,
    UpstreamNotFoundError,
    UpstreamTimeoutError,
)

logger = logging.getLogger("flock_urja.upstream")


class UpstreamPortalClient:
    """Async HTTP client for interacting with the Urja Meter Ops legacy portal.
    
    Handles:
    - Session authentication via form POST to /login
    - SvelteKit CSRF header compliance (Origin and Referer)
    - Automatic session renewal on expiry or redirect
    - HMAC-SHA256 signing for /portal/export bulk retrieval
    - Connection pooling and timeout management
    - Normalized domain exceptions and credential masking
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        self.base_url = (base_url or settings.portal_base_url).rstrip("/")
        self.username = username or settings.portal_username
        self.password = password or settings.portal_password
        self.timeout = timeout or settings.upstream_timeout_seconds

        # Headers required for SvelteKit CSRF check
        self._default_headers = {
            "User-Agent": "FlockEnergy-UrjaAPI/1.0 (+https://flockenergy.tech)",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/login",
            "Accept": "application/json, text/html, */*",
        }

        self._client: Optional[httpx.AsyncClient] = None
        self._is_authenticated = False
        self._signing_secret: Optional[str] = None

    async def get_client(self) -> httpx.AsyncClient:
        """Get or initialize persistent AsyncClient bound to current event loop."""
        import asyncio
        current_loop = asyncio.get_running_loop()
        if (
            self._client is None
            or self._client.is_closed
            or getattr(self, "_loop", None) != current_loop
        ):
            self._loop = current_loop
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._default_headers,
                timeout=httpx.Timeout(self.timeout, connect=5.0),
                follow_redirects=True,
            )
            self._is_authenticated = False
        return self._client


    async def close(self):
        """Close active client session."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._is_authenticated = False
        self._client = None

    async def authenticate(self) -> bool:
        """Authenticate with the portal using operator credentials.
        
        Submits login form data to /login and validates session establishment.
        """
        client = await self.get_client()
        logger.info("Authenticating with Urja Meter Ops portal as %s", self.username)

        login_payload = {
            "email": self.username,
            "password": self.password,
        }

        try:
            # SvelteKit expects x-www-form-urlencoded
            response = await client.post(
                "/login",
                data=login_payload,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": self.base_url,
                    "Referer": f"{self.base_url}/login",
                },
            )

            # Check if login succeeded
            # Portal returns 200 with redirect JSON: {"type":"redirect","status":303,"location":"/meters"}
            # Or failure: {"type":"failure","status":401,...}
            body_text = response.text
            if "failure" in body_text or response.status_code == 401:
                logger.error("Authentication failed: invalid credentials for %s", self.username)
                raise UpstreamAuthError("Invalid username or password for Urja Meter Ops portal")

            # Check if session cookie was stored
            has_session = any(
                "session_token" in k for k in client.cookies.keys()
            )
            if not has_session and "redirect" not in body_text:
                logger.warning("No session token cookie detected after login attempt")

            self._is_authenticated = True
            logger.info("Successfully authenticated with Urja Meter Ops portal")
            return True

        except httpx.TimeoutException as exc:
            logger.error("Timeout during portal authentication: %s", exc)
            raise UpstreamTimeoutError("Timeout during authentication with upstream portal") from exc
        except httpx.RequestError as exc:
            logger.error("Network error during portal authentication: %s", exc)
            raise UpstreamError(f"Network error during authentication: {exc}") from exc

    async def _ensure_auth(self):
        """Ensure client is authenticated before making requests."""
        if not self._is_authenticated:
            await self.authenticate()

    async def request(
        self,
        method: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Any] = None,
        json_data: Optional[Any] = None,
        allow_retry: bool = True,
    ) -> httpx.Response:
        """Make an authenticated request to the upstream portal with auto-reauth."""
        await self._ensure_auth()
        client = await self.get_client()

        req_headers = dict(self._default_headers)
        if headers:
            req_headers.update(headers)

        url = path if path.startswith("http") else f"{self.base_url}{path}"

        try:
            response = await client.request(
                method=method,
                url=url,
                headers=req_headers,
                params=params,
                data=data,
                json=json_data,
            )

            # If portal session expired, it redirects to /login or returns 401/403
            if allow_retry and (
                response.status_code in (401, 403)
                or str(response.url).rstrip("/").endswith("/login")
            ):
                logger.info("Session expired or unauthorized (%s), attempting re-authentication", response.status_code)
                self._is_authenticated = False
                await self.authenticate()
                # Retry once
                return await self.request(
                    method=method,
                    path=path,
                    headers=headers,
                    params=params,
                    data=data,
                    json_data=json_data,
                    allow_retry=False,
                )

            return response

        except httpx.TimeoutException as exc:
            raise UpstreamTimeoutError(f"Request to {path} timed out") from exc
        except httpx.RequestError as exc:
            raise UpstreamError(f"Network error requesting {path}: {exc}") from exc

    async def check_health(self) -> bool:
        """Check if upstream portal is alive and reachable."""
        try:
            client = await self.get_client()
            resp = await client.get("/login", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def get_signing_secret(self) -> str:
        """Fetch the HMAC signing secret from /portal/keys."""
        if self._signing_secret:
            return self._signing_secret

        resp = await self.request("GET", "/portal/keys")
        if resp.status_code != 200:
            raise UpstreamError(f"Failed to fetch signing keys: HTTP {resp.status_code}")

        try:
            data = resp.json()
            secret = data.get("data", {}).get("signingSecret")
            if not secret:
                raise ValueError("signingSecret missing in response")
            self._signing_secret = secret
            return secret
        except Exception as exc:
            raise UpstreamError(f"Malformed response from /portal/keys: {exc}") from exc

    async def fetch_bulk_export(self) -> Dict[str, Any]:
        """Fetch every page of the signed export and verify its declared record count."""
        secret = await self.get_signing_secret()
        path = "/portal/export"
        all_meters: List[Dict[str, Any]] = []
        first_response: Optional[Dict[str, Any]] = None
        expected_total: Optional[int] = None
        page = 1

        while expected_total is None or len(all_meters) < expected_total:
            timestamp = str(int(time.time()))
            query = f"page={page}"
            string_to_sign = f"GET\n{path}\n{query}\n{timestamp}".encode("utf-8")
            signature = hmac.new(secret.encode("utf-8"), string_to_sign, hashlib.sha256).hexdigest()

            resp = await self.request(
                "GET",
                f"{path}?{query}",
                headers={"x-timestamp": timestamp, "x-signature": signature},
            )
            if resp.status_code != 200:
                raise UpstreamError(f"Bulk export page {page} failed with HTTP {resp.status_code}")

            try:
                payload = resp.json()
            except Exception as exc:
                raise UpstreamError(f"Failed to parse bulk export page {page} JSON: {exc}") from exc

            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise UpstreamError(f"Bulk export page {page} has an invalid data payload")
            total = payload.get("total")
            if not isinstance(total, int) or isinstance(total, bool) or total < 0:
                raise UpstreamError(f"Bulk export page {page} has an invalid total")
            if expected_total is None:
                expected_total = total
                first_response = payload
            elif total != expected_total:
                raise UpstreamError(
                    f"Bulk export total changed during pagination: expected {expected_total}, received {total}"
                )

            page_meters = payload["data"]
            if not page_meters and len(all_meters) < expected_total:
                raise UpstreamError(
                    f"Bulk export ended at {len(all_meters)} of {expected_total} meters (empty page {page})"
                )
            all_meters.extend(page_meters)
            if len(all_meters) > expected_total:
                raise UpstreamError(
                    f"Bulk export returned {len(all_meters)} records, exceeding declared total {expected_total}"
                )
            page += 1

        if first_response is None:
            raise UpstreamError("Bulk export did not return a first page")
        return {**first_response, "data": all_meters, "total": expected_total}

    async def search_meters(self, query: str = "", page: int = 1) -> Dict[str, Any]:
        """Query /portal/meters/search?q={query}&page={page}."""
        resp = await self.request(
            "GET",
            "/portal/meters/search",
            params={"q": query, "page": page},
        )
        if resp.status_code != 200:
            raise UpstreamError(f"Meter search failed: HTTP {resp.status_code}")
        return resp.json()

    async def fetch_dts(self, page: int = 1) -> Dict[str, Any]:
        """Query /portal/dts?page={page}."""
        resp = await self.request(
            "GET",
            "/portal/dts",
            params={"page": page},
        )
        if resp.status_code != 200:
            raise UpstreamError(f"DT fetch failed: HTTP {resp.status_code}")
        return resp.json()

    async def fetch_all_dts(self) -> List[Dict[str, Any]]:
        """Paginate through all distribution transformers."""
        all_dts = []
        page = 1
        while True:
            data = await self.fetch_dts(page=page)
            dts = data.get("data", [])
            if not dts:
                break
            all_dts.extend(dts)
            total = data.get("total", len(all_dts))
            if len(all_dts) >= total:
                break
            page += 1
        return all_dts

    async def fetch_meter_energy(self, meter_id: str) -> List[Dict[str, Any]]:
        """Query /portal/meters/{meter_id}/energy."""
        resp = await self.request("GET", f"/portal/meters/{meter_id}/energy")
        if resp.status_code == 404:
            raise UpstreamNotFoundError("Meter", meter_id)
        if resp.status_code != 200:
            raise UpstreamError(f"Failed to fetch energy for meter {meter_id}: HTTP {resp.status_code}")
        
        try:
            body = resp.json()
            return body.get("data", [])
        except Exception as exc:
            raise UpstreamError(f"Invalid energy JSON for meter {meter_id}: {exc}") from exc

    async def fetch_meter_geo(self, meter_id: str) -> Optional[Dict[str, Any]]:
        """Query /portal/meters/{meter_id}/geo."""
        resp = await self.request("GET", f"/portal/meters/{meter_id}/geo")
        if resp.status_code == 404:
            raise UpstreamNotFoundError("Meter", meter_id)
        if resp.status_code != 200:
            return None
        try:
            return resp.json().get("data")
        except Exception:
            return None
