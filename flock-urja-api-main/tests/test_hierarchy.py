from fastapi.testclient import TestClient
from app.core.cache import store


def test_get_hierarchy_tree(test_client: TestClient):
    resp = test_client.get("/api/v1/hierarchy")
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_meters"] == 4
    assert data["total_transformers"] == 2
    assert len(data["tree"]) == 1

    zone = data["tree"][0]
    assert zone["level"] == "zone"
    assert zone["name"] == "Jaipur Zone 1"
    assert zone["code"] == "Z-01"
    assert zone["meter_count"] == 4

    # Circle
    assert len(zone["children"]) == 1
    circle = zone["children"][0]
    assert circle["level"] == "circle"
    assert circle["meter_count"] == 4

    # Division
    division = circle["children"][0]
    assert division["level"] == "division"

    # Subdivision
    subdivision = division["children"][0]
    assert subdivision["level"] == "subdivision"

    # Substation
    substation = subdivision["children"][0]
    assert substation["level"] == "substation"

    # Feeders (Feeder 1 and Feeder 2)
    feeders = substation["children"]
    assert len(feeders) == 2
    f1 = next(f for f in feeders if f["code"] == "F-001")
    f2 = next(f for f in feeders if f["code"] == "F-002")
    assert f1["meter_count"] == 2
    assert f2["meter_count"] == 2

    # DTs
    assert len(f1["children"]) == 1
    assert f1["children"][0]["code"] == "DT-001"
    assert f1["children"][0]["meter_count"] == 2

    assert len(f2["children"]) == 1
    assert f2["children"][0]["code"] == "DT-002"
    assert f2["children"][0]["meter_count"] == 2


def test_hierarchy_reports_source_relationship_mismatches(test_client: TestClient):
    meter = store.get_meter("J100000")
    assert meter is not None and meter.hierarchy is not None and meter.hierarchy.feeder is not None
    meter.hierarchy.feeder.code = "F-UNKNOWN"

    resp = test_client.get("/api/v1/hierarchy")
    assert resp.status_code == 200
    issues = {issue["issue_code"]: issue for issue in resp.json()["data_quality"]}
    assert issues["feeder_assignment_mismatch"]["count"] == 1
    assert issues["feeder_assignment_mismatch"]["examples"] == ["J100000"]
