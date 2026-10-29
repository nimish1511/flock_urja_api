import pytest
from typing import AsyncGenerator
import httpx
from fastapi.testclient import TestClient

from app.core.cache import store
from app.core.upstream_client import UpstreamPortalClient
from app.main import app, portal_client

MOCK_METERS = [
    {
        "meterId": "J100000",
        "serialNo": "SE33962",
        "make": "HPL",
        "phaseType": "single",
        "installStatus": "Installed",
        "installType": "Whole Current",
        "build": "legacy",
        "dtCode": "DT-001",
        "classData": "{\"installed_meter\": {\"ratedVoltage\": \"240 V\"}}",
        "detail": {
            "data": [
                {"parameterName": "meter_constant", "parameterValue": "3200 imp/kWh"}
            ]
        },
        "hierarchy": {
            "zone": {"name": "Jaipur Zone 1", "code": "Z-01"},
            "circle": {"name": "Circle 1", "code": "C-01"},
            "division": {"name": "Division 1", "code": "D-01"},
            "subdivision": {"name": "Subdivision 1", "code": "SD-01"},
            "substation": {"name": "Substation 1", "code": "SS-01"},
            "feeder": {"name": "Feeder 1", "code": "F-001"},
            "dt": {"name": "Malviya Nagar DT 1", "code": "DT-001"}
        },
        "geo": {"lat": 26.938961, "lng": 75.830957}
    },
    {
        "meterId": "J100001",
        "serialNo": "GE84132",
        "make": "L&T",
        "phaseType": "three",
        "installStatus": "Installed",
        "installType": "CT Operated",
        "build": "v2",
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
        "geo": {"lat": 26.940120, "lng": 75.832450}
    },
    {
        "meterId": "J100002",
        "serialNo": "SE99014",
        "make": "Secure",
        "phaseType": "single",
        "installStatus": "Faulty",
        "installType": "Whole Current",
        "build": "legacy",
        "dtCode": "DT-002",
        "hierarchy": {
            "zone": {"name": "Jaipur Zone 1", "code": "Z-01"},
            "circle": {"name": "Circle 1", "code": "C-01"},
            "division": {"name": "Division 1", "code": "D-01"},
            "subdivision": {"name": "Subdivision 1", "code": "SD-01"},
            "substation": {"name": "Substation 1", "code": "SS-01"},
            "feeder": {"name": "Feeder 2", "code": "F-002"},
            "dt": {"name": "C-Scheme DT 2", "code": "DT-002"}
        },
        "geo": {"lat": 26.912400, "lng": 75.787300}
    },
    {
        "meterId": "J100003",
        "serialNo": "GN55421",
        "make": "Genus",
        "phaseType": "three",
        "installStatus": "Decommissioned",
        "installType": "CT Operated",
        "build": "v2",
        "dtCode": "DT-002",
        "hierarchy": {
            "zone": {"name": "Jaipur Zone 1", "code": "Z-01"},
            "circle": {"name": "Circle 1", "code": "C-01"},
            "division": {"name": "Division 1", "code": "D-01"},
            "subdivision": {"name": "Subdivision 1", "code": "SD-01"},
            "substation": {"name": "Substation 1", "code": "SS-01"},
            "feeder": {"name": "Feeder 2", "code": "F-002"},
            "dt": {"name": "C-Scheme DT 2", "code": "DT-002"}
        },
        "geo": {"lat": 26.915000, "lng": 75.790000}
    }
]

MOCK_DTS = [
    {
        "code": "DT-001",
        "name": "Malviya Nagar DT 1",
        "feederCode": "F-001",
        "capacityKva": 100
    },
    {
        "code": "DT-002",
        "name": "C-Scheme DT 2",
        "feederCode": "F-002",
        "capacityKva": 250
    }
]

MOCK_ENERGY_READINGS = [
    {"timestamp": "23/06/2026 22:30", "kwh": "48430.00", "kvah": "52300.00", "voltR": "224"},
    {"timestamp": "23/06/2026 23:00", "kwh": "48434.50", "kvah": "52306.80", "voltR": "225"},
    {"timestamp": "23/06/2026 23:30", "kwh": "48438.74", "kvah": "52313.84", "voltR": "226"},
]


class MockUpstreamClient(UpstreamPortalClient):
    """Mock client for offline unit & integration testing."""

    def __init__(self):
        super().__init__(base_url="https://mock.portal.local", username="test@local", password="test")
        self.portal_alive = True
        self.auth_should_fail = False
        self.energy_readings = MOCK_ENERGY_READINGS

    async def authenticate(self) -> bool:
        if self.auth_should_fail:
            from app.models.errors import UpstreamAuthError
            raise UpstreamAuthError("Invalid credentials")
        self._is_authenticated = True
        return True

    async def check_health(self) -> bool:
        return self.portal_alive

    async def fetch_bulk_export(self):
        return {"data": MOCK_METERS, "total": len(MOCK_METERS)}

    async def fetch_all_dts(self):
        return MOCK_DTS

    async def fetch_meter_energy(self, meter_id: str):
        from app.models.errors import UpstreamNotFoundError
        if meter_id not in [m["meterId"] for m in MOCK_METERS]:
            raise UpstreamNotFoundError("Meter", meter_id)
        self.last_energy_meter_id = meter_id
        return self.energy_readings

    async def fetch_meter_geo(self, meter_id: str):
        for m in MOCK_METERS:
            if m["meterId"] == meter_id:
                return {"latitude": str(m["geo"]["lat"]), "longitude": str(m["geo"]["lng"])}
        return None


@pytest.fixture
def mock_client():
    return MockUpstreamClient()


@pytest.fixture(autouse=True)
def populate_cache(mock_client):
    """Populate DataStore cache before each test synchronously."""
    import asyncio
    asyncio.run(store.sync(mock_client, force=True))


@pytest.fixture
def test_client(mock_client, monkeypatch):
    """FastAPI TestClient with overridden upstream dependencies."""
    from app.routers import admin, health, hierarchy, meters, transformers
    from app.auth import require_api_token
    from app.config import settings

    monkeypatch.setattr(settings, "api_access_token", "test-token-for-api-access")

    app.dependency_overrides[meters.get_upstream_client] = lambda: mock_client
    app.dependency_overrides[transformers.get_upstream_client] = lambda: mock_client
    app.dependency_overrides[hierarchy.get_upstream_client] = lambda: mock_client
    app.dependency_overrides[health.get_upstream_client] = lambda: mock_client
    app.dependency_overrides[admin.get_upstream_client] = lambda: mock_client
    app.dependency_overrides[require_api_token] = lambda: None

    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()
