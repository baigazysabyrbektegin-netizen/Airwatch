"""Weather pipeline tests on a synthetic Open-Meteo response. No internet access is used or required."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from pipeline import io_utils as io
from pipeline import process_weather as pw
from pipeline import weather_core as wc


def make_doc(n_hours=48, start="2021-03-01T00:00", *, drop=None, dup=None, units=None, null_var=None, lat=43.0, lon=74.5):
    t = pd.date_range(start, periods=n_hours, freq="h")
    times = [x.strftime("%Y-%m-%dT%H:%M") for x in t]
    hourly = {"time": times}
    for i, v in enumerate(wc.VARIABLES):
        hourly[v] = [round(float(i + k * 0.1), 2) for k in range(n_hours)]
    hourly["relative_humidity_2m"] = [50 + (k % 40) for k in range(n_hours)]
    hourly["wind_direction_10m"] = [float((k * 15) % 360) for k in range(n_hours)]
    hourly["precipitation"] = [0.0] * n_hours
    hourly["surface_pressure"] = [935.0 + k * 0.1 for k in range(n_hours)]
    if null_var:
        for k in range(10, 20):
            hourly[null_var][k] = None
    if drop is not None:
        for key in hourly:
            del hourly[key][drop]
    if dup is not None:
        for key in hourly:
            hourly[key].insert(dup, hourly[key][dup])
    u = {"time": "iso8601", **{v: wc.VARIABLES[v]["unit"] for v in wc.VARIABLES}}
    u.update(units or {})
    return {"latitude": lat, "longitude": lon, "elevation": 702.0, "utc_offset_seconds": 0, "timezone": "GMT", "generationtime_ms": 1.0, "hourly_units": u, "hourly": hourly}


class FakeHttp:
    def __init__(self, doc=None, status=200, body=None):
        self.calls = []
        self.doc, self.status, self.body = doc, status, body

    def __call__(self, url):
        self.calls.append(url)
        body = self.body if self.body is not None else json.dumps(self.doc).encode()
        return self.status, body, {"Content-Type": "application/json"}


REQ = wc.Request(42.917, 74.510, "2021-03-01", "2021-03-02")


def test_request_names_era5_explicitly_and_uses_metric_units():
    url = REQ.url()
    assert "models=era5" in url and "timezone=UTC" in url and "wind_speed_unit=ms" in url
    assert "boundary_layer_height" in url and "start_date=2021-03-01" in url


def test_fetch_downloads_once_and_reuses_the_cache(tmp_path):
    http = FakeHttp(make_doc())
    a = wc.fetch(REQ, tmp_path, http)
    assert a.downloaded and len(http.calls) == 1
    b = wc.fetch(REQ, tmp_path, http)
    assert not b.downloaded and len(http.calls) == 1 and a.sha256 == b.sha256
    meta = json.loads(a.request_path.read_text())
    assert meta["url"] == REQ.url() and meta["response_sha256"] == a.sha256 and meta["retrieved_at_utc"]
    assert a.raw_path.read_bytes() == json.dumps(make_doc()).encode()  # original bytes preserved


def test_refresh_downloads_again(tmp_path):
    http = FakeHttp(make_doc())
    wc.fetch(REQ, tmp_path, http)
    wc.fetch(REQ, tmp_path, http, refresh=True)
    assert len(http.calls) == 2


def test_tampered_cache_is_refused(tmp_path):
    http = FakeHttp(make_doc())
    f = wc.fetch(REQ, tmp_path, http)
    f.raw_path.write_bytes(b"{}")
    with pytest.raises(wc.WeatherError, match="checksum"):
        wc.fetch(REQ, tmp_path, http)


def test_http_error_and_bad_body_are_not_cached(tmp_path):
    with pytest.raises(wc.WeatherError, match="HTTP 429"):
        wc.fetch(REQ, tmp_path, FakeHttp(status=429, body=b'{"reason":"limit"}'))
    with pytest.raises(wc.WeatherError, match="not JSON"):
        wc.fetch(REQ, tmp_path, FakeHttp(body=b"<html>"))
    with pytest.raises(wc.WeatherError, match="hourly"):
        wc.fetch(REQ, tmp_path, FakeHttp(body=b'{"a": 1}'))
    assert list(tmp_path.glob("*.json")) == []


def test_timestamps_become_utc_and_asia_bishkek():
    df, info = wc.parse(make_doc(3, "2021-03-01T18:00"))
    assert str(df["ts_utc"].dt.tz) == "UTC" and str(df["ts_local"].dt.tz) == "Asia/Bishkek"
    assert df["ts_utc"].iloc[0].isoformat() == "2021-03-01T18:00:00+00:00"
    assert df["ts_local"].iloc[0].isoformat() == "2021-03-02T00:00:00+06:00"   # UTC+6, no DST
    assert info["returned_latitude"] == 43.0 and info["elevation_m"] == 702.0


def test_units_are_preserved_and_wrong_units_are_rejected():
    _, info = wc.parse(make_doc())
    assert info["units"]["wind_speed_10m"] == "m/s" and info["units"]["temperature_2m"] == "°C"
    with pytest.raises(wc.WeatherError, match="Unexpected units"):
        wc.parse(make_doc(units={"wind_speed_10m": "km/h"}))


def test_missing_values_stay_missing_and_are_reported():
    df, _ = wc.parse(make_doc(null_var="boundary_layer_height"))
    assert df["boundary_layer_height"].isna().sum() == 10
    rep = wc.validate(df)
    v = rep["variables"]["boundary_layer_height"]
    assert v["n_missing"] == 10 and v["missing_runs"] == 1 and v["longest_missing_run_hours"] == 10
    assert rep["variables"]["temperature_2m"]["n_missing"] == 0


def test_variable_absent_from_response_is_reported_not_substituted():
    doc = make_doc()
    del doc["hourly"]["boundary_layer_height"]
    df, info = wc.parse(doc)
    assert info["variables_missing_from_response"] == ["boundary_layer_height"]
    assert df["boundary_layer_height"].isna().all()


def test_hourly_gap_and_duplicates_are_detected():
    df, _ = wc.parse(make_doc(drop=5))
    rep = wc.validate(df)
    assert rep["missing_hours"] == 1 and rep["hourly_coverage_pct"] < 100
    df2, _ = wc.parse(make_doc(dup=7))
    assert wc.validate(df2)["duplicate_timestamps"] == 1
    clean, stats = pw.dedupe(df2, tuple(wc.VARIABLES))
    assert stats["exact_duplicates_collapsed"] == 1 and len(clean) == 48 and clean["ts_utc"].is_unique


def test_conflicting_duplicates_are_blanked_not_guessed():
    df, _ = wc.parse(make_doc(dup=7))
    i = df.index[df["ts_utc"].duplicated(keep="last")][0]
    df.loc[i, "temperature_2m"] += 5.0
    clean, stats = pw.dedupe(df, tuple(wc.VARIABLES))
    assert stats["conflicting_duplicate_timestamps"] == 1
    assert clean[clean["ts_utc"] == df.loc[i, "ts_utc"]][list(wc.VARIABLES)].isna().all(axis=None)


def test_out_of_range_values_are_counted_not_altered():
    df, _ = wc.parse(make_doc())
    df.loc[0, "relative_humidity_2m"] = 130.0
    rep = wc.validate(df)
    assert rep["variables"]["relative_humidity_2m"]["n_outside_plausible_range"] == 1
    assert df.loc[0, "relative_humidity_2m"] == 130.0


def test_full_run_writes_outputs_and_a_machine_readable_manifest(tmp_path):
    http = FakeHttp(make_doc(null_var="boundary_layer_height"))
    emb = tmp_path / "embassy.json"
    emb.write_text(json.dumps({"dataset": {"first_timestamp_local": "2021-03-01 06:00:00+06:00", "last_timestamp_local": "2021-03-02 23:00:00+06:00"}}))
    dirs = {k: tmp_path / k for k in ("raw", "interim", "processed")}
    mpath = tmp_path / "weather_processing.json"
    m = pw.run(dirs["raw"], dirs["interim"], dirs["processed"], mpath, http_get=http, embassy_manifest=emb)
    assert (m["request"]["start_date"], m["request"]["end_date"]) == ("2021-03-01", "2021-03-02")
    assert m["request"]["period_source"].endswith("embassy_processing.json")
    saved = json.loads(mpath.read_text())
    assert saved["grid"]["returned_latitude"] == 43.0 and saved["grid"]["requested_latitude"] == 42.917
    assert saved["variables"]["wind_speed_10m"]["unit"] == "m/s" and saved["retrieval"]["response_sha256"] and saved["source"]["model"] == "era5"
    assert saved["validation"]["variables"]["boundary_layer_height"]["n_missing"] == 10
    assert any("boundary_layer_height has 10 missing" in x for x in saved["known_limitations"])
    assert "non-commercial" in saved["licence"]["usage"].lower() and "Open-Meteo" in saved["licence"]["attribution"]["open_meteo"]
    out = pd.read_parquet(dirs["processed"] / "era5_hourly.parquet")
    assert len(out) == 48 and out["boundary_layer_height"].isna().sum() == 10          # not interpolated
    assert (dirs["interim"] / "weather_validation_report.md").is_file()
    assert len(http.calls) == 1
    # second run: cached response, identical outputs, no overwrite error, no new request
    m2 = pw.run(dirs["raw"], dirs["interim"], dirs["processed"], mpath, http_get=http, embassy_manifest=emb)
    assert len(http.calls) == 1 and not m2["retrieval"]["downloaded_in_this_run"]
    assert m2["outputs"]["processed_hourly"]["status"] == "unchanged"


def test_changed_output_is_not_overwritten_without_approval(tmp_path):
    dirs = {k: tmp_path / k for k in ("raw", "interim", "processed")}
    emb = tmp_path / "e.json"
    emb.write_text("{}")  # unreadable manifest -> fallback period; override explicitly instead
    pw.run(dirs["raw"], dirs["interim"], dirs["processed"], tmp_path / "m.json", start="2021-03-01", end="2021-03-02", http_get=FakeHttp(make_doc()), embassy_manifest=emb)
    p = dirs["processed"] / "era5_hourly.parquet"
    pd.DataFrame({"x": [1]}).to_parquet(p)
    with pytest.raises(io.OutputExistsError):
        pw.run(dirs["raw"], dirs["interim"], dirs["processed"], tmp_path / "m.json", start="2021-03-01", end="2021-03-02", http_get=FakeHttp(make_doc()), embassy_manifest=emb)


def test_embassy_period_falls_back_when_manifest_is_missing(tmp_path):
    s, e, src = pw.embassy_period(tmp_path / "nope.json")
    assert (s, e) == pw.FALLBACK_PERIOD and "fallback" in src


def test_haversine_sanity():
    assert pw.haversine_km(42.917, 74.510, 43.0, 74.5) == pytest.approx(9.3, abs=0.2)
    assert np.isclose(pw.haversine_km(0, 0, 0, 0), 0)
