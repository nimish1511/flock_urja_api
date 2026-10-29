from fastapi.testclient import TestClient
from app.auth import require_api_token
from app.config import settings


def test_health_healthy(test_client: TestClient):
    response = test_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["portal_connected"] is True
    assert data["cache_synced"] is True
    assert "timestamp" in data


def test_health_degraded_when_portal_down(test_client: TestClient, mock_client):
    mock_client.portal_alive = False
    response = test_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert data["portal_connected"] is False


def test_cors_is_limited_to_local_origins(test_client: TestClient):
    allowed = test_client.options(
        "/api/v1/sync",
        headers={
            "Origin": "http://127.0.0.1:8000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:8000"
    assert "authorization" in allowed.headers["access-control-allow-headers"].lower()

    rejected = test_client.options(
        "/api/v1/sync",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers


def test_api_routes_require_bearer_token(test_client: TestClient, monkeypatch):
    test_client.app.dependency_overrides.pop(require_api_token)
    monkeypatch.setattr(settings, "api_access_token", "test-token-for-api-access")

    missing = TestClient(test_client.app).get("/api/v1/sync/status")
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "API_AUTH_REQUIRED"

    accepted = TestClient(test_client.app).get(
        "/api/v1/sync/status",
        headers={"Authorization": "Bearer test-token-for-api-access"},
    )
    assert accepted.status_code == 200


def test_api_fails_closed_when_token_is_not_configured(test_client: TestClient, monkeypatch):
    test_client.app.dependency_overrides.pop(require_api_token)
    monkeypatch.setattr(settings, "api_access_token", None)
    response = TestClient(test_client.app).get("/api/v1/sync/status")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "API_AUTH_NOT_CONFIGURED"


def test_safe_config_masks_api_token(monkeypatch):
    monkeypatch.setattr(settings, "api_access_token", "test-token-for-api-access")
    assert settings.safe_dict()["api_access_token"] == "******"


def test_forced_sync_has_cooldown(test_client: TestClient, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "sync_state_db_path", str(tmp_path / "sync_state.sqlite3"))
    first = test_client.post("/api/v1/sync")
    assert first.status_code == 200

    second = test_client.post("/api/v1/sync")
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "SYNC_COOLDOWN"
    assert second.headers["retry-after"]


def test_failed_forced_sync_does_not_consume_cooldown(
    test_client: TestClient, mock_client, monkeypatch, tmp_path
):
    from app.models.errors import UpstreamError

    monkeypatch.setattr(settings, "sync_state_db_path", str(tmp_path / "sync_state.sqlite3"))
    original_fetch = mock_client.fetch_all_dts
    fail_once = True

    async def failing_fetch():
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise UpstreamError("temporary sync failure")
        return await original_fetch()

    monkeypatch.setattr(mock_client, "fetch_all_dts", failing_fetch)
    failed = test_client.post("/api/v1/sync")
    assert failed.status_code == 502
    succeeded = test_client.post("/api/v1/sync")
    assert succeeded.status_code == 200
