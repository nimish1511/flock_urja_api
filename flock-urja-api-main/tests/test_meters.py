from fastapi.testclient import TestClient


def test_list_meters_all(test_client: TestClient):
    resp = test_client.get("/api/v1/meters")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 4
    assert len(data["items"]) == 4
    assert data["page"] == 1
    assert data["pages"] == 1


def test_search_meter_by_id(test_client: TestClient):
    resp = test_client.get("/api/v1/meters?q=J100000")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["meter_id"] == "J100000"


def test_search_meter_by_serial(test_client: TestClient):
    resp = test_client.get("/api/v1/meters?q=GE84132")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["serial_no"] == "GE84132"


def test_filter_by_make(test_client: TestClient):
    resp = test_client.get("/api/v1/meters?make=HPL")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["make"] == "HPL"


def test_filter_by_phase(test_client: TestClient):
    resp = test_client.get("/api/v1/meters?phase_type=three")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    for item in data["items"]:
        assert item["phase_type"] == "three"


def test_filter_by_status(test_client: TestClient):
    resp = test_client.get("/api/v1/meters?install_status=Faulty")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["install_status"] == "Faulty"


def test_filter_by_dt(test_client: TestClient):
    resp = test_client.get("/api/v1/meters?dt_code=DT-001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    for item in data["items"]:
        assert item["dt_code"] == "DT-001"


def test_pagination(test_client: TestClient):
    resp = test_client.get("/api/v1/meters?page=1&page_size=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 4
    assert len(data["items"]) == 2
    assert data["pages"] == 2

    # Page 2
    resp2 = test_client.get("/api/v1/meters?page=2&page_size=2")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["items"]) == 2
    assert data["items"][0]["meter_id"] != data2["items"][0]["meter_id"]


def test_get_meter_detail_success(test_client: TestClient):
    resp = test_client.get("/api/v1/meters/J100000")
    assert resp.status_code == 200
    data = resp.json()
    assert data["meter_id"] == "J100000"
    assert data["serial_no"] == "SE33962"
    assert data["make"] == "HPL"
    assert data["geo"]["latitude"] == 26.938961
    assert data["geo"]["longitude"] == 75.830957
    assert data["hierarchy"]["zone"]["code"] == "Z-01"
    assert data["hierarchy"]["dt"]["code"] == "DT-001"
    assert data["extra_attributes"]["ratedVoltage"] == "240 V"
    assert data["extra_attributes"]["meter_constant"] == "3200 imp/kWh"


def test_get_meter_by_serial_fallback(test_client: TestClient):
    resp = test_client.get("/api/v1/meters/SE33962")
    assert resp.status_code == 200
    data = resp.json()
    assert data["meter_id"] == "J100000"


def test_get_meter_not_found(test_client: TestClient):
    resp = test_client.get("/api/v1/meters/NONEXISTENT999")
    assert resp.status_code == 404
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == "METER_NOT_FOUND"


def test_nearby_meters(test_client: TestClient):
    # Query close to J100000 (26.938961, 75.830957)
    resp = test_client.get("/api/v1/meters/nearby?latitude=26.9390&longitude=75.8310&radius_km=1.0")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert data["meters"][0]["meter_id"] == "J100000"
    assert "distance_km" in data["meters"][0]
    assert data["meters"][0]["distance_km"] <= 1.0
