import math


def get(client, **params):
    return client.get("/api/measurements", params=params)


def test_clarity_hourly_records_and_basis(client):
    r = get(client, station="C1")
    assert r.status_code == 200
    b = r.json()
    assert b["total"] == 48 and b["count"] == 48 and b["truncated"] is False and b["interval"] == "hourly" and b["unit"] == "µg/m³"
    first = b["records"][0]
    assert first["timestamp"] == "2021-03-01T00:00:00+06:00" and first["interval_end"] == "2021-03-01T01:00:00+06:00"
    assert first["pm25_ugm3"] == 10.0 and first["quality_flag"] == "VALID" and first["flags"] == []
    assert "HIGH_VALUE_OVER_500" in b["records"][5]["flags"] and "NEIGHBOR_OUTLIER_CANDIDATE" in b["records"][5]["flags"] and b["records"][5]["pm25_ugm3"] == 600.0   # flagged, kept
    assert "ASSUMED" in b["timestamp_basis"] and "UNRESOLVED" in b["alignment_note"] and any("never zero" in n for n in b["notes"])


def test_date_filters_pagination_and_time_zone(client):
    b = get(client, station="C1", start="2021-03-02", end="2021-03-02").json()
    assert b["total"] == 24 and b["records"][0]["timestamp"] == "2021-03-02T00:00:00+06:00" and b["records"][-1]["timestamp"] == "2021-03-02T23:00:00+06:00"
    b = get(client, station="C1", start="2021-03-01T10:00", end="2021-03-01T12:00").json()
    assert [r["timestamp"][11:13] for r in b["records"]] == ["10", "11", "12"]                    # naive input = Asia/Bishkek local time
    b = get(client, station="C1", start="2021-03-01T10:00:00+00:00", end="2021-03-01T10:00:00+00:00").json()
    assert [r["timestamp"] for r in b["records"]] == ["2021-03-01T16:00:00+06:00"]               # UTC input converted
    p1 = get(client, station="C1", limit=10, offset=0).json()
    p2 = get(client, station="C1", limit=10, offset=10).json()
    assert p1["truncated"] is True and p1["count"] == 10 and p1["total"] == 48 and p2["records"][0]["timestamp"] == "2021-03-01T10:00:00+06:00"
    assert get(client, station="C1", limit=10, offset=40).json()["truncated"] is False
    assert get(client, station="C1", start="2022-01-01").json()["total"] == 0                    # empty result, not an error and not zeros


def test_daily_aggregation_keeps_incomplete_days_null(client):
    b = get(client, station="C1", interval="daily").json()
    assert [(r["timestamp"], r["pm25_ugm3"], r["quality_flag"]) for r in b["records"]] == [
        ("2021-03-01", 21.0, "COMPLETE_DAY"), ("2021-03-02", 35.0, "COMPLETE_DAY"), ("2021-03-03", None, "INCOMPLETE_DAY")]
    assert b["records"][2]["detail"]["n_valid_hours"] == 10 and "UNDER_18_VALID_HOURS" in b["records"][2]["flags"] and "18 valid hours" in b["notes"][0]
    one = get(client, station="C1", interval="daily", start="2021-03-02", end="2021-03-02").json()
    assert one["total"] == 1


def test_monthly_aggregation_and_rule(client):
    b = get(client, station="C1", interval="monthly").json()
    assert [(r["timestamp"], r["pm25_ugm3"], r["quality_flag"]) for r in b["records"]] == [("2021-03", 28.0, "MEETS_75PCT_DAY_RULE"), ("2021-04", None, "BELOW_75PCT_DAY_RULE")]
    assert get(client, station="C1", interval="monthly", start="2021-04").json()["total"] == 1


def test_embassy_hourly_keeps_raw_values_flags_and_nulls(client):
    b = get(client, station="us-embassy-bishkek", start="2023-03-31T00:00", end="2023-03-31T08:00").json()
    recs = {r["timestamp"][11:13]: r for r in b["records"]}
    assert "05" not in recs                                                                         # the no-source-row hour is not returned by default
    assert recs["02"]["pm25_ugm3"] == -2.0 and recs["02"]["pm25_valid_ugm3"] == 0.0 and "NEGATIVE_NOISE" in recs["02"]["flags"]   # raw kept, model clipped
    assert recs["03"]["pm25_ugm3"] == 600.0 and "HIGH_VALUE_OVER_500" in recs["03"]["flags"] and "LOCAL_SPIKE" in recs["03"]["flags"]
    assert recs["04"]["quality_flag"] == "INVALID" and recs["04"]["pm25_valid_ugm3"] is None and recs["04"]["detail"]["qc_name_original"] == "Invalid"
    assert recs["06"]["quality_flag"] == "DUP_CONFLICT" and recs["06"]["pm25_ugm3"] is None
    assert not any(r["pm25_valid_ugm3"] == 0.0 and r["quality_flag"] != "VALID" for r in b["records"])   # no missing value is ever turned into 0
    assert "Date (LT)" in b["timestamp_basis"] and "UNRESOLVED" in b["alignment_note"]


def test_embassy_gap_hours_only_with_include_gaps(client):
    n_default = get(client, station="us-embassy-bishkek").json()["total"]
    b = get(client, station="us-embassy-bishkek", include_gaps="true").json()
    assert b["total"] == n_default + 1
    gap = next(r for r in b["records"] if r["quality_flag"] == "NO_ROW")
    assert gap["pm25_ugm3"] is None and gap["pm25_valid_ugm3"] is None


def test_embassy_daily_and_monthly_use_the_stated_rules(client):
    d = get(client, station="us-embassy-bishkek", interval="daily").json()
    assert [r["timestamp"] for r in d["records"]] == ["2023-03-31", "2023-04-01", "2023-04-02"]
    assert d["records"][0]["detail"]["n_valid_hours"] == 21 and d["records"][0]["quality_flag"] == "COMPLETE_DAY"     # 24 hours minus invalid, gap and conflict hours
    assert d["records"][0]["pm25_ugm3"] is not None and not math.isnan(d["records"][0]["pm25_ugm3"])
    m = get(client, station="us-embassy-bishkek", interval="monthly").json()
    assert [r["timestamp"] for r in m["records"]] == ["2023-03", "2023-04"] and all(r["quality_flag"] == "MEETS_75PCT_DAY_RULE" for r in m["records"])


def test_embassy_regime_warning_appears_only_for_the_affected_period(client):
    inside = get(client, station="us-embassy-bishkek", start="2023-04-01", end="2023-04-02").json()
    assert any("regime" in n.lower() for n in inside["notes"])
    before = get(client, station="us-embassy-bishkek", start="2023-03-31T00:00", end="2023-03-31T12:00").json()
    assert not any("regime" in n.lower() for n in before["notes"])


def test_stations_are_never_merged_one_station_per_request(client):
    # Even when two stations are named, exactly one series comes back, on that station's own timestamps (no union, no alignment).
    r = client.get("/api/measurements", params=[("station", "C1"), ("station", "C2")])
    b = r.json()
    assert r.status_code == 200 and b["station"]["id"] == "C2" and b["total"] == 24
    assert {rec["timestamp"][:10] for rec in b["records"]} == {"2021-03-02"}
    c1, emb = get(client, station="C1").json(), get(client, station="us-embassy-bishkek").json()
    assert c1["station"]["network"] == "clarity" and emb["station"]["network"] == "us-embassy"
    assert c1["timestamp_basis"] != emb["timestamp_basis"] and c1["alignment_note"] == emb["alignment_note"]   # each keeps its own basis; the offset stays flagged


def test_invalid_parameters_return_clear_errors(client):
    assert get(client).status_code == 422                                                      # station is required
    assert get(client, station="C1", interval="weekly").status_code == 422
    assert get(client, station="C1", limit=0).status_code == 422 and get(client, station="C1", limit=20001).status_code == 422
    assert get(client, station="C1", offset=-1).status_code == 422
    r = get(client, station="C1", start="not-a-date")
    assert r.status_code == 422 and r.json()["error"] == "invalid_query" and "ISO date" in r.json()["message"]
    r = get(client, station="C1", start="2021-03-05", end="2021-03-01")
    assert r.status_code == 422 and "start must not be later" in r.json()["message"]
    r = get(client, station="NOPE")
    assert r.status_code == 404 and r.json()["error"] == "not_found"
    r = get(client, station="C3")
    assert r.status_code == 422 and "no readings" in r.json()["message"]
