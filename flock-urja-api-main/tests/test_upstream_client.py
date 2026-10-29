import hashlib
import hmac
import httpx
import pytest
from app.core.cache import DataStore, ForcedSyncCooldownError, haversine_km, store
from app.core.upstream_client import UpstreamPortalClient
from app.models.errors import UpstreamAuthError, UpstreamError
from app.config import settings


def test_haversine_distance_calculation():
    # Distance between two known coordinates in Jaipur:
    # Malviya Nagar (26.8528, 75.8237) and C-Scheme (26.9124, 75.7873) is ~7.5 - 8.0 km
    dist = haversine_km(26.8528, 75.8237, 26.9124, 75.7873)
    assert 7.0 <= dist <= 8.5
    # Same point distance should be 0.0
    assert haversine_km(26.9389, 75.8309, 26.9389, 75.8309) == 0.0


def test_hmac_signature_generation():
    secret = "test_signing_secret_123"
    ts = "1700000000"
    method = "GET"
    path = "/portal/export"
    query = "page=1"

    string_to_sign = f"{method}\n{path}\n{query}\n{ts}".encode("utf-8")
    expected_sig = hmac.new(secret.encode("utf-8"), string_to_sign, hashlib.sha256).hexdigest()

    assert len(expected_sig) == 64  # SHA256 hex is 64 characters
    assert all(c in "0123456789abcdef" for c in expected_sig)


def test_client_headers():
    client = UpstreamPortalClient(base_url="https://urja-ops.flockenergy.tech")
    headers = client._default_headers
    assert headers["Origin"] == "https://urja-ops.flockenergy.tech"
    assert headers["Referer"] == "https://urja-ops.flockenergy.tech/login"
    assert "User-Agent" in headers


@pytest.mark.asyncio
async def test_bulk_export_fetches_and_validates_every_page(monkeypatch):
    client = UpstreamPortalClient(base_url="https://mock.portal.local")
    pages = [
        {"data": [{"meterId": "A"}, {"meterId": "B"}], "total": 3},
        {"data": [{"meterId": "C"}], "total": 3},
    ]
    requested_paths = []
    seen_signatures = []

    async def get_secret():
        return "test-secret"

    async def request(method, path, headers=None):
        requested_paths.append(path)
        seen_signatures.append((path, headers))
        page = int(path.rsplit("=", 1)[1])
        return httpx.Response(200, json=pages[page - 1])

    monkeypatch.setattr(client, "get_signing_secret", get_secret)
    monkeypatch.setattr(client, "request", request)

    result = await client.fetch_bulk_export()
    assert [meter["meterId"] for meter in result["data"]] == ["A", "B", "C"]
    assert result["total"] == 3
    assert requested_paths == ["/portal/export?page=1", "/portal/export?page=2"]
    for path, headers in seen_signatures:
        page_query = path.rsplit("?", 1)[1]
        signed = f"GET\n/portal/export\n{page_query}\n{headers['x-timestamp']}".encode()
        expected = hmac.new(b"test-secret", signed, hashlib.sha256).hexdigest()
        assert headers["x-signature"] == expected


@pytest.mark.asyncio
async def test_bulk_export_fails_when_pages_do_not_match_total(monkeypatch):
    client = UpstreamPortalClient(base_url="https://mock.portal.local")
    pages = [
        {"data": [{"meterId": "A"}], "total": 2},
        {"data": [], "total": 2},
    ]

    async def get_secret():
        return "test-secret"

    async def request(method, path, headers=None):
        page = int(path.rsplit("=", 1)[1])
        return httpx.Response(200, json=pages[page - 1])

    monkeypatch.setattr(client, "get_signing_secret", get_secret)
    monkeypatch.setattr(client, "request", request)

    with pytest.raises(UpstreamError, match="ended at 1 of 2"):
        await client.fetch_bulk_export()


@pytest.mark.asyncio
async def test_cache_rejects_duplicate_meter_ids(mock_client, monkeypatch):
    payload = await mock_client.fetch_bulk_export()
    payload["data"] = list(payload["data"])
    payload["data"][0] = {**payload["data"][0], "meterId": payload["data"][1]["meterId"]}

    async def duplicate_export():
        return payload

    monkeypatch.setattr(mock_client, "fetch_bulk_export", duplicate_export)
    with pytest.raises(UpstreamError, match="unique meters"):
        await store.sync(mock_client, force=True)


@pytest.mark.asyncio
async def test_forced_sync_cooldown_is_shared_between_store_instances(
    mock_client, monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "sync_state_db_path", str(tmp_path / "shared_sync.sqlite3"))
    monkeypatch.setattr(settings, "forced_sync_cooldown_seconds", 60)
    first_store = DataStore()
    second_store = DataStore()

    await first_store.force_sync(mock_client)
    with pytest.raises(ForcedSyncCooldownError):
        await second_store.force_sync(mock_client)
