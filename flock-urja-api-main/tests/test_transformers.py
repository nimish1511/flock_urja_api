from fastapi.testclient import TestClient


def test_list_transformers(test_client: TestClient):
    resp = test_client.get("/api/v1/transformers")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2


def test_filter_by_feeder(test_client: TestClient):
    resp = test_client.get("/api/v1/transformers?feeder_code=F-001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["code"] == "DT-001"
    assert data["items"][0]["feeder_code"] == "F-001"


def test_get_transformer_detail(test_client: TestClient):
    resp = test_client.get("/api/v1/transformers/DT-001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == "DT-001"
    assert data["name"] == "Malviya Nagar DT 1"
    assert data["capacity_kva"] == 100
    assert data["connected_meters_count"] == 2
    assert len(data["meters"]) == 2
    meter_ids = [m["meter_id"] for m in data["meters"]]
    assert "J100000" in meter_ids
    assert "J100001" in meter_ids


def test_transformer_not_found(test_client: TestClient):
    resp = test_client.get("/api/v1/transformers/NONEXISTENT_DT")
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"]["code"] == "TRANSFORMER_NOT_FOUND"
