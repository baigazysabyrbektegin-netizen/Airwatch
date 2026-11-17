def test_health_still_works(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_stations_lists_clarity_with_data_and_the_embassy(client):
    r = client.get("/api/stations")
    assert r.status_code == 200
    body = r.json()
    ids = [s["id"] for s in body["stations"]]
    assert ids == ["us-embassy-bishkek", "C1", "C2"] and body["count"] == 3          # devices without readings are not listed by default
    assert any("historical" in n.lower() for n in body["notes"]) and any("1-hour offset" in n for n in body["notes"])


def test_station_fields_come_from_the_processed_inventory(client):
    c1 = next(s for s in client.get("/api/stations").json()["stations"] if s["id"] == "C1")
    assert (c1["latitude"], c1["longitude"]) == (42.86, 74.55) and c1["network"] == "clarity" and c1["grade"] == "low-cost"
    assert c1["data_available"] is True and c1["status"] == "has_data_in_file" and c1["n_valid_readings"] == 48
    assert c1["data_period"]["start"] == "2021-03-01T00:00:00+06:00" and c1["data_period"]["end"] == "2021-03-02T23:00:00+06:00"
    assert c1["quality"]["high_value_readings_over_500"] == 1 and c1["quality"]["neighbor_outlier_candidates"] == 1
    assert "NOT independently verified" in c1["quality"]["calibration"] and "ASSUMED" in c1["timestamp_convention"]
    assert c1["coverage_pct"] == 100.0                                                  # 48 of the 48-hour network window
    c2 = client.get("/api/stations/C2").json()
    assert c2["coverage_pct"] == 50.0 and c2["quality"]["valid_readings"] == 24         # 24 of the same 48-hour window
    assert c1["data_kind"].startswith("measured") and "2021" in c1["vintage"]


def test_embassy_location_is_marked_approximate(client):
    e = client.get("/api/stations/us-embassy-bishkek").json()
    assert e["location_status"] == "APPROXIMATE" and "NOT a verified" in e["location_note"]
    assert (e["latitude"], e["longitude"]) == (42.8272774, 74.5824625)
    assert "reference-grade in the source paper" in e["sensor_type"]
    q = e["quality"]
    assert q["hours_without_source_row"] == 1 and q["negative_noise_hours"] == 1 and q["high_value_hours_over_500"] == 1 and q["duplicate_conflict_hours"] == 1
    assert "regime" in q["potential_regime_issue"].lower() and "Date (LT)" in e["timestamp_convention"]


def test_devices_without_readings_are_only_listed_on_request_and_flagged(client):
    ids = [s["id"] for s in client.get("/api/stations?include_no_data=true").json()["stations"]]
    assert {"C3", "C4"} <= set(ids)
    c3 = client.get("/api/stations/C3").json()
    assert c3["data_available"] is False and c3["data_period"] is None and c3["quality"] is None and c3["n_valid_readings"] == 0
    assert any("not an active sensor" in n for n in c3["notes"])


def test_shared_coordinates_are_reported_not_hidden(client):
    c2 = client.get("/api/stations/C2").json()
    assert c2["shares_coordinates_with"] == ["C4"] and any("same coordinates" in n for n in c2["notes"])


def test_network_filter_and_unknown_station(client):
    assert [s["id"] for s in client.get("/api/stations?network=us-embassy").json()["stations"]] == ["us-embassy-bishkek"]
    assert client.get("/api/stations?network=purpleair").status_code == 422
    r = client.get("/api/stations/NOPE")
    assert r.status_code == 404 and r.json()["error"] == "not_found" and "C1" in r.json()["available"]
    assert client.get("/api/stations/c1").json()["id"] == "C1"                         # ids are case-insensitive
