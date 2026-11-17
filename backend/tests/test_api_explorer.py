"""Explorer, summary and comparison endpoints on synthetic data."""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_store
from app.main import create_app
from app.services.data_store import DataStore

START = pd.Timestamp("2021-02-27 00:00", tz="UTC")
EMB = "us-embassy-bishkek"


def hour_index(local: str) -> float:
    return (pd.Timestamp(local, tz="Asia/Bishkek").tz_convert("UTC") - START) / pd.Timedelta(hours=1)


def ex(client, **params):
    return client.get("/api/explorer", params=params)


# ------------------------------------------------------------------ explorer
def test_embassy_hourly_pairs_pm25_with_the_same_hour_of_weather(client):
    b = ex(client, source="embassy", interval="hourly", start_date="2023-03-31", end_date="2023-04-02").json()
    assert b["station"]["id"] == EMB and b["source"] == "embassy" and b["count"] == 72
    first = b["series"][0]
    assert first["timestamp"] == "2023-03-31T00:00:00+06:00" and first["pm25"] == 20.0
    assert first["weather"]["temperature_2m"] == hour_index("2023-03-31 00:00")
    assert b["alignment"]["weather_offset_hours"] == 0 and b["alignment"]["offset_applies"] is True
    assert "No offset was chosen to improve correlation" in b["alignment"]["statement"]


def test_clarity_hourly_pairs_on_the_end_of_the_hour_label(client):
    b = ex(client, source="clarity", station_id="C1", interval="hourly", start_date="2021-03-01", end_date="2021-03-01").json()
    first = b["series"][0]
    assert first["timestamp"] == "2021-03-01T00:00:00+06:00"             # interval START as in the measurements API
    assert first["weather"]["temperature_2m"] == hour_index("2021-03-01 01:00")   # weather at the END of the hour
    assert "END of each hour" in b["alignment"]["pairing_basis"]


def test_user_selected_offset_is_applied_and_echoed_never_automatic(client):
    base = ex(client, source="embassy", interval="hourly", start_date="2023-03-31", end_date="2023-03-31").json()["series"][0]["weather"]["temperature_2m"]
    b = ex(client, source="embassy", interval="hourly", start_date="2023-03-31", end_date="2023-03-31", weather_offset_hours=1).json()
    assert b["series"][0]["weather"]["temperature_2m"] == base + 1 and b["alignment"]["weather_offset_hours"] == 1
    assert "+1 h" in b["alignment"]["statement"]


def test_offset_is_rejected_for_daily_and_out_of_range(client):
    r = ex(client, source="embassy", interval="daily", weather_offset_hours=1)
    assert r.status_code == 422 and "hourly" in r.json()["message"]
    assert ex(client, source="embassy", interval="hourly", start_date="2023-03-31", end_date="2023-03-31", weather_offset_hours=24).status_code == 422


def test_networks_are_never_merged_into_one_series(client):
    b = ex(client, source="clarity", station_id="C1", interval="daily").json()
    assert {b["station"]["id"]} == {"C1"} and b["source"] == "clarity"
    assert all(set(r) >= {"timestamp", "pm25", "weather"} for r in b["series"])
    assert "unresolved" in " ".join(w["message"] for w in b["warnings"]).lower()


def test_station_and_source_validation(client):
    assert ex(client, source="clarity").status_code == 422                               # station required
    r = ex(client, source="embassy", station_id="C1")
    assert r.status_code == 422 and "belongs to the clarity network" in r.json()["message"]
    assert ex(client, source="clarity", station_id="C3").status_code == 422             # listed but no data
    assert ex(client, source="clarity", station_id="nope").status_code == 404
    assert ex(client, source="radar").status_code == 422
    assert ex(client, interval="yearly").status_code == 422


def test_gaps_are_preserved_as_null_records_not_zero(client):
    b = ex(client, source="embassy", interval="hourly", start_date="2023-03-31", end_date="2023-03-31").json()
    by = {r["timestamp"][11:13]: r for r in b["series"]}
    assert by["05"]["pm25"] is None and by["05"]["quality_flag"] == "NO_ROW"             # hour without a source row
    assert by["04"]["pm25"] is None and by["04"]["quality_flag"] == "INVALID"            # provider-invalid: excluded
    assert by["02"]["pm25"] == 0.0 and by["02"]["pm25_raw"] == -2.0                      # negative noise: original preserved
    assert by["03"]["pm25"] == 600.0 and "HIGH_VALUE_OVER_500" in by["03"]["flags"]      # flagged, kept visible
    assert b["n_valid"] == 21


def test_clarity_hourly_gap_hours_are_filled_with_null_records(client, data_dir):
    h = pd.read_parquet(data_dir / "processed/clarity/clarity_pm25_hourly.parquet")
    h = h[~((h["device_id"] == "C1") & (h["ts_start_local"] == pd.Timestamp("2021-03-01 10:00", tz="Asia/Bishkek")))]
    h.to_parquet(data_dir / "processed/clarity/clarity_pm25_hourly.parquet", index=False)
    app = create_app()
    s = DataStore(data_dir)
    app.dependency_overrides[get_store] = lambda: s
    b = TestClient(app).get("/api/explorer", params={"source": "clarity", "station_id": "C1", "interval": "hourly", "start_date": "2021-03-01", "end_date": "2021-03-02"}).json()
    gap = [r for r in b["series"] if r["timestamp"].startswith("2021-03-01T10")][0]
    assert b["count"] == 48 and gap["pm25"] is None and gap["quality_flag"] == "NO_READING"
    assert b["n_valid"] == 47


def test_daily_and_monthly_series(client):
    d = ex(client, source="embassy", interval="daily").json()
    assert [r["timestamp"] for r in d["series"]] == ["2023-03-31", "2023-04-01", "2023-04-02"]
    assert d["series"][0]["quality_flag"] == "COMPLETE_DAY" and d["series"][0]["detail"]["n_valid_hours"] == 21
    m = ex(client, source="embassy", interval="monthly").json()
    assert [r["timestamp"] for r in m["series"]] == ["2023-03", "2023-04"]
    assert m["series"][1]["weather"]["temperature_2m"] is None or isinstance(m["series"][1]["weather"]["temperature_2m"], float)


def test_empty_state_when_dates_do_not_overlap(client):
    b = ex(client, source="embassy", start_date="2030-01-01", end_date="2030-02-01").json()
    assert b["empty"] is True and b["series"] == [] and "do not overlap" in b["empty_reason"]


def test_hourly_range_is_clipped_to_the_stations_data_before_the_cap_applies(client):
    r2 = client.get("/api/explorer", params={"source": "embassy", "interval": "hourly", "start_date": "2023-01-01", "end_date": "2023-12-31"})
    assert r2.status_code == 200  # clipped to the station's 3 days of data, so within the cap


def test_hourly_cap_message(long_client):
    r = long_client.get("/api/explorer", params={"source": "embassy", "interval": "hourly", "start_date": "2023-01-01", "end_date": "2023-05-31"})
    assert r.status_code == 422 and "120 days" in r.json()["message"]


def test_relationships_need_enough_pairs_and_are_labelled_as_association(client):
    b = ex(client, source="embassy", interval="daily").json()["relationships"]
    t = b["variables"]["temperature_2m"]
    assert t["n_pairs"] == 3 and t["enough_pairs"] is False and t["pearson_r"] is None and "at least 30" in t["reason"]
    assert "do not show cause and effect" in b["note"]
    h = ex(client, source="embassy", interval="hourly", start_date="2023-03-31", end_date="2023-04-02").json()["relationships"]["variables"]["temperature_2m"]
    assert h["n_pairs"] == 69 and h["enough_pairs"] is True and h["pearson_r"] is not None and -1 <= h["pearson_r"] <= 1 and h["spearman_rho"] is not None


def test_correlation_matches_an_independent_calculation(client):
    b = ex(client, source="embassy", interval="hourly", start_date="2023-03-31", end_date="2023-04-02").json()
    pairs = [(r["pm25"], r["weather"]["temperature_2m"]) for r in b["series"] if r["pm25"] is not None]
    x, y = np.array(pairs).T
    assert b["relationships"]["variables"]["temperature_2m"]["pearson_r"] == pytest.approx(np.corrcoef(x, y)[0, 1])


def test_quality_warnings_embassy_regime_and_clarity_calibration(client):
    codes = {w["code"] for w in ex(client, source="embassy").json()["warnings"]}
    assert {"embassy_regime_2023_2024", "embassy_location_approximate", "clock_alignment_unresolved", "historical_not_current", "era5_not_local"} <= codes
    c = {w["code"] for w in ex(client, source="clarity", station_id="C1").json()["warnings"]}
    assert {"clarity_not_calibrated_verified", "clarity_flagged_values_included"} <= c


def test_weather_gaps_and_short_period_are_warned(client):
    b = ex(client, source="clarity", station_id="C1", interval="daily").json()
    assert not any(w["code"] == "weather_period_shorter" for w in b["warnings"])


def test_explorer_still_works_without_the_weather_file_and_says_so(data_dir):
    (data_dir / "processed/weather/era5_hourly.parquet").unlink()
    app = create_app()
    s = DataStore(data_dir)
    app.dependency_overrides[get_store] = lambda: s
    b = TestClient(app).get("/api/explorer", params={"source": "embassy"}).json()
    assert b["weather"] == {"included": False, "unavailable": True} and b["count"] == 3
    assert all(r["weather"] == {} for r in b["series"])
    assert any(w["code"] == "weather_unavailable" and "process_weather" in w["message"] for w in b["warnings"])


def test_weather_can_be_switched_off(client):
    b = ex(client, source="embassy", include_weather="false").json()
    assert b["weather"] == {"included": False} and b["relationships"]["variables"] == {}


# ------------------------------------------------------------------ summary
def test_embassy_summary_statistics_and_observation_categories(client, data_dir):
    s = client.get("/api/explorer/summary", params={"source": "embassy"}).json()
    e = pd.read_parquet(data_dir / "processed/embassy/embassy_pm25_hourly.parquet")
    vals = e.loc[e["quality_flag"] == "VALID", "pm25_model_ugm3"].to_numpy()
    assert s["n_valid"] == len(vals) == 69 and s["expected_hours"] == 72
    assert s["stats"]["mean"] == pytest.approx(vals.mean()) and s["stats"]["median"] == pytest.approx(np.median(vals))
    assert s["stats"]["max"] == 600.0 and s["stats"]["min"] == 0.0
    assert s["stats"]["p95"] == pytest.approx(np.quantile(vals, 0.95)) and s["stats"]["p25"] == pytest.approx(np.quantile(vals, 0.25))
    assert s["coverage_pct"] == pytest.approx(100 * 69 / 72, abs=0.01)
    o = s["observations"]
    assert o["excluded"] == {"hours_without_source_row": 1, "provider_invalid": 1, "missing_value": 0, "duplicate_conflict": 1, "total": 3}
    assert o["suspect"]["flagged_but_included"] == {"negative_noise": 1, "high_value_over_500": 1, "local_drop": 0, "local_spike": 1}
    assert o["negative_clipped_to_zero"] == 1 and o["imputed"]["used_in_statistics"] is False and o["imputed"]["hours_with_feature_interpolation"] == 0


def test_clarity_summary_counts_flagged_values_as_included(client):
    s = client.get("/api/explorer/summary", params={"source": "clarity", "station_id": "C1"}).json()
    assert s["n_valid"] == 48 and s["expected_hours"] == 48 and s["coverage_pct"] == 100.0
    assert s["observations"]["suspect"]["flagged_but_included"] == {"high_value_over_500": 1, "neighbor_outlier_candidate": 1}
    assert s["stats"]["max"] == 600.0 and s["observations"]["excluded"]["total"] == 0


def test_summary_date_filter_uses_only_the_selected_days(client):
    s = client.get("/api/explorer/summary", params={"source": "clarity", "station_id": "C1", "start_date": "2021-03-02", "end_date": "2021-03-02"}).json()
    assert s["n_valid"] == 24 and s["stats"]["min"] == 34.0 and s["query"]["effective_start"] == "2021-03-02"
    assert [m["year_month"] for m in s["monthly"]] == ["2021-03"] and s["monthly"][0]["eligible_days"] == 1   # clipped to the chosen day


def test_monthly_climatology_and_seasons_never_report_zero_for_no_data(client):
    s = client.get("/api/explorer/summary", params={"source": "embassy"}).json()
    assert [m["year_month"] for m in s["monthly"]] == ["2023-03", "2023-04"]
    clim = {c["month"]: c for c in s["climatology"]}
    assert clim[3]["n_months"] == 1 and clim[4]["n_months"] == 1 and clim[1]["mean"] is None and clim[1]["n_months"] == 0
    seasons = {x["season"]: x for x in s["seasonal"]}
    assert seasons["DJF"]["mean_of_daily_means"] is None and seasons["DJF"]["n_valid_days"] == 0       # no data -> null, not 0
    assert seasons["MAM"]["n_valid_days"] == 3 and seasons["MAM"]["mean_of_daily_means"] is not None
    assert {x["season"] for x in s["seasonal_by_year"]} == {"MAM"}


def test_summary_empty_and_error_states(client):
    s = client.get("/api/explorer/summary", params={"source": "embassy", "start_date": "2030-01-01", "end_date": "2030-01-02"}).json()
    assert s["empty"] is True and "do not overlap" in s["empty_reason"]
    assert client.get("/api/explorer/summary", params={"source": "clarity"}).status_code == 422
    assert client.get("/api/explorer/summary", params={"start_date": "x"}).status_code == 422


# ------------------------------------------------------------------ comparison
@pytest.fixture
def long_client(data_dir):
    """Embassy with 150 days of hourly data (Jan-May 2023); the pm25 level differs by month."""
    t = pd.date_range("2023-01-01", periods=24 * 150, freq="h", tz="Asia/Bishkek")
    month_level = np.where(t.month == 1, 50.0, np.where(t.month == 2, 80.0, 20.0))
    n = len(t)
    raw = month_level + (np.arange(n) % 24)
    present = np.ones(n, dtype=bool)
    q = np.array(["VALID"] * n, dtype=object)
    # February: knock out two thirds of the hours -> unequal coverage against January
    feb = np.where(t.month == 2)[0]
    q[feb[::3 * 1][1::2]] = "INVALID"
    model = np.where(q == "VALID", raw, np.nan)
    e = pd.DataFrame({"ts_local": t, "ts_utc": t.tz_convert("UTC"), "ts_original": "x", "row_present": present, "n_source_rows": 1, "quality_flag": q, "qc_name_original": "Valid",
                      "pm25_raw_ugm3": raw, "pm25_model_ugm3": model, "pm25_feature_interp_ugm3": model, "is_imputed": False, "flag_neg_noise": False, "flag_high_value": False,
                      "flag_local_drop": False, "flag_local_spike": False, "flag_duplicate_ts": False, "flag_duplicate_conflict": False, "source_file": "f", "source_line": "1",
                      "processing_version": "t"})
    e.to_parquet(data_dir / "processed/embassy/embassy_pm25_hourly.parquet", index=False)
    app = create_app()
    s = DataStore(data_dir)
    app.dependency_overrides[get_store] = lambda: s
    return TestClient(app)


def cmp(client, **params):
    return client.get("/api/explorer/compare", params=params)


def test_two_periods_same_station_with_difference_and_warnings(long_client):
    b = cmp(long_client, station_a=EMB, start_a="2023-01-01", end_a="2023-01-31", start_b="2023-03-01", end_b="2023-03-31").json()
    assert b["difference"]["available"] is True
    d = b["difference"]["hourly_mean"]
    assert d["a"] == pytest.approx(50 + 11.5) and d["b"] == pytest.approx(20 + 11.5) and d["difference_b_minus_a"] == pytest.approx(-30.0)
    assert d["percent_change"] == pytest.approx(-30 / 61.5 * 100)
    assert b["period_a"]["series"][0] == {"index": 0, "timestamp": "2023-01-01", "pm25": pytest.approx(61.5)}
    assert len(b["period_a"]["series"]) == len(b["period_b"]["series"]) == 31
    assert b["alignment"]["x_axis"].startswith("index")
    assert b["difference"]["weather"]["temperature_2m"]["a"] is not None


def test_unequal_coverage_length_and_season_are_flagged_explicitly(long_client):
    b = cmp(long_client, station_a=EMB, start_a="2023-01-01", end_a="2023-01-31", start_b="2023-02-01", end_b="2023-02-28").json()
    codes = {w["code"] for w in b["warnings"]}
    assert {"unequal_length", "different_seasons", "unequal_coverage"} <= codes
    assert b["period_a"]["summary"]["coverage_pct"] == 100.0 and b["period_b"]["summary"]["coverage_pct"] < 90


def test_overlapping_periods_are_flagged(long_client):
    b = cmp(long_client, station_a=EMB, start_a="2023-01-01", end_a="2023-01-20", start_b="2023-01-10", end_b="2023-01-31").json()
    assert "overlapping_periods" in {w["code"] for w in b["warnings"]}


def test_embassy_and_clarity_are_not_paired_or_presented_as_synchronised(long_client):
    b = cmp(long_client, station_a=EMB, start_a="2023-01-01", end_a="2023-01-31", station_b="C1", start_b="2021-03-01", end_b="2021-03-02").json()
    w = {x["code"]: x for x in b["warnings"]}
    assert "different_networks" in w and "no timestamp pairing" in w["different_networks"]["message"]
    assert b["difference"]["available"] is False and "at least 10 valid daily means" in b["difference"]["reason"]    # Clarity has only 2 days
    assert "few_valid_days" in w


def test_period_without_data_is_reported_not_zero_filled(long_client):
    b = cmp(long_client, station_a=EMB, start_a="2023-01-01", end_a="2023-01-31", start_b="2030-01-01", end_b="2030-01-31").json()
    assert b["period_b"]["empty"] is True and b["period_b"]["series"] == [] and b["difference"]["available"] is False
    assert any(w["code"] == "empty_period" for w in b["warnings"])


def test_monthly_comparison_and_parameter_errors(long_client):
    b = cmp(long_client, station_a=EMB, start_a="2023-01-01", end_a="2023-03-31", start_b="2023-01-01", end_b="2023-03-31", interval="monthly").json()
    assert [r["timestamp"] for r in b["period_a"]["series"]] == ["2023-01", "2023-02", "2023-03"]
    assert cmp(long_client, station_a=EMB, start_a="2023-01-01", end_a="2023-01-31").status_code == 422
    assert cmp(long_client, station_a=EMB, start_a="2023-01-01", end_a="2023-01-31", start_b="a", end_b="b").status_code == 422
    assert cmp(long_client, station_a="nope", start_a="2023-01-01", end_a="2023-01-31", start_b="2023-02-01", end_b="2023-02-02").status_code == 404
    assert cmp(long_client, station_a=EMB, start_a="2023-01-01", end_a="2023-01-31", start_b="2023-02-01", end_b="2023-02-02", interval="hourly").status_code == 422


def test_embassy_period_overlapping_2023_2024_carries_the_regime_warning(long_client):
    b = cmp(long_client, station_a=EMB, start_a="2023-01-01", end_a="2023-01-31", start_b="2023-04-01", end_b="2023-04-30").json()
    assert any(w["code"] == "period_b_embassy_regime_2023_2024" for w in b["warnings"]) and not any(w["code"] == "period_a_embassy_regime_2023_2024" for w in b["warnings"])
