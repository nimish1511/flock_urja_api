from fastapi.testclient import TestClient


def test_get_consumption_success(test_client: TestClient):
    resp = test_client.get("/api/v1/meters/J100000/consumption")
    assert resp.status_code == 200
    data = resp.json()

    assert data["meter_id"] == "J100000"
    summary = data["summary"]
    assert summary["reading_count"] == 3
    assert summary["interval_count"] == 2
    assert summary["total_kwh"] == 8.74
    assert summary["avg_interval_kwh"] == 4.37
    assert summary["min_interval_kwh"] == 4.24
    assert summary["max_interval_kwh"] == 4.5
    assert "avg_kwh" not in summary
    assert summary["avg_voltage_r"] == 225.0

    readings = data["readings"]
    assert len(readings) == 3

    # Check first reading
    r0 = readings[0]
    assert r0["timestamp_raw"] == "23/06/2026 22:30"
    assert "2026-06-23T22:30:00" in r0["timestamp_iso"]
    assert r0["kwh"] == 48430.00
    assert r0["kvah"] == 52300.00
    assert r0["voltage_r"] == 224.0
    assert r0["interval_kwh"] is None
    assert r0["interval_status"] == "baseline"
    assert r0["interval_power_factor"] is None
    assert readings[1]["interval_kwh"] == 4.5
    assert readings[1]["interval_power_factor"] == 0.662
    assert readings[2]["interval_kwh"] == 4.24
    assert readings[2]["interval_power_factor"] == 0.602


def test_consumption_by_serial_uses_canonical_meter_id(test_client: TestClient, mock_client):
    resp = test_client.get("/api/v1/meters/SE33962/consumption")

    assert resp.status_code == 200
    assert resp.json()["meter_id"] == "J100000"
    assert mock_client.last_energy_meter_id == "J100000"


def test_interval_calculation_skips_resets_and_missing_readings(test_client: TestClient, mock_client):
    mock_client.energy_readings = [
        {"timestamp": "23/06/2026 22:30", "kwh": "100", "kvah": "120"},
        {"timestamp": "23/06/2026 23:00", "kwh": "105", "kvah": "126"},
        {"timestamp": "23/06/2026 23:30", "kwh": "2", "kvah": "3"},
        {"timestamp": "24/06/2026 00:00", "kwh": "", "kvah": "4"},
        {"timestamp": "24/06/2026 00:30", "kwh": "4", "kvah": "6"},
        {"timestamp": "24/06/2026 01:00", "kwh": "6", "kvah": "9"},
    ]

    resp = test_client.get("/api/v1/meters/J100000/consumption")
    assert resp.status_code == 200
    data = resp.json()
    assert [reading["interval_status"] for reading in data["readings"]] == [
        "baseline", "valid", "meter_reset", "missing_reading", "time_gap", "valid"
    ]
    assert [reading["interval_kwh"] for reading in data["readings"]] == [
        None, 5.0, None, None, 2.0, 2.0
    ]
    assert data["summary"]["interval_count"] == 2
    assert data["summary"]["gap_interval_count"] == 1
    assert data["summary"]["total_kwh"] == 9.0


def test_missing_value_delta_is_in_total_but_not_30_minute_statistics(
    test_client: TestClient, mock_client
):
    mock_client.energy_readings = [
        {"timestamp": "23/06/2026 22:30", "kwh": "100", "kvah": "120"},
        {"timestamp": "23/06/2026 23:00", "kwh": "", "kvah": ""},
        {"timestamp": "23/06/2026 23:30", "kwh": "110", "kvah": "132"},
    ]

    data = test_client.get("/api/v1/meters/J100000/consumption").json()
    assert data["readings"][2]["interval_kwh"] == 10.0
    assert data["readings"][2]["interval_duration_minutes"] == 60.0
    assert data["readings"][2]["interval_status"] == "time_gap"
    assert data["summary"]["total_kwh"] == 10.0
    assert data["summary"]["interval_count"] == 0
    assert data["summary"]["gap_interval_count"] == 1
    assert data["summary"]["avg_interval_kwh"] is None


def test_duplicate_timestamps_keep_most_complete_latest_reading(
    test_client: TestClient, mock_client
):
    mock_client.energy_readings = [
        {"timestamp": "23/06/2026 23:00", "kwh": "105", "kvah": "126"},
        {"timestamp": "23/06/2026 22:30", "kwh": "100", "kvah": "120"},
        {"timestamp": "23/06/2026 23:00", "kwh": "106", "kvah": "127", "voltR": "225"},
        {"timestamp": "23/06/2026 23:30", "kwh": "110", "kvah": "132"},
    ]

    data = test_client.get("/api/v1/meters/J100000/consumption").json()
    assert data["summary"]["reading_count"] == 3
    assert data["summary"]["duplicate_reading_count"] == 1
    assert [reading["kwh"] for reading in data["readings"]] == [100.0, 106.0, 110.0]
    assert data["summary"]["total_kwh"] == 10.0
    assert data["summary"]["interval_count"] == 2


def test_consumption_meter_not_found(test_client: TestClient):
    resp = test_client.get("/api/v1/meters/NONEXISTENT999/consumption")
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"]["code"] == "METER_NOT_FOUND"
