"""ERA5 weather endpoint on synthetic data (temperature_2m == hour index, so pairing and filtering are exactly checkable)."""

import pandas as pd

START = pd.Timestamp("2021-02-27 00:00", tz="UTC")


def hour_index(local: str) -> float:
    return (pd.Timestamp(local, tz="Asia/Bishkek").tz_convert("UTC") - START) / pd.Timedelta(hours=1)


def get(client, **params):
    return client.get("/api/weather", params=params)


def test_hourly_timestamps_are_local_and_values_come_from_the_right_utc_hour(client):
    r = get(client, interval="hourly", start_date="2021-03-01", end_date="2021-03-01")
    assert r.status_code == 200
    b = r.json()
    assert b["count"] == 24 and b["total"] == 24
    first = b["records"][0]
    assert first["timestamp"] == "2021-03-01T00:00:00+06:00"             # Asia/Bishkek
    assert first["temperature_2m"] == hour_index("2021-03-01 00:00") == 42.0   # UTC 2021-02-28 18:00
    assert b["records"][-1]["timestamp"] == "2021-03-01T23:00:00+06:00"
    assert len({x["timestamp"] for x in b["records"]}) == 24             # no duplicate timestamps


def test_units_and_provenance_are_returned_and_era5_is_not_presented_as_a_forecast(client):
    b = get(client, interval="daily", start_date="2021-03-01", end_date="2021-03-02").json()
    assert b["variables"]["wind_speed_10m"]["unit"] == "m/s" and b["variables"]["temperature_2m"]["unit"] == "°C"
    p = b["provenance"]
    assert p["is_forecast"] is False and p["is_local_measurement"] is False and "reanalysis" in p["data_kind"]
    assert p["grid"]["returned_latitude"] == 43.0 and p["grid"]["requested_latitude"] == 42.917
    assert any("Open-Meteo" in a for a in p["attribution"]) and "non-commercial" in p["licence"]
    assert any("NOT a weather forecast" in n for n in b["notes"])


def test_daily_aggregation_rules(client):
    recs = {x["timestamp"]: x for x in get(client, interval="daily", start_date="2021-03-01", end_date="2021-03-06").json()["records"]}
    d1 = recs["2021-03-01"]
    assert d1["temperature_2m"] == 53.5                                   # mean of hour indices 42..65
    assert d1["precipitation"] == 1.5                                     # summed, not averaged
    assert d1["wind_direction_10m"] is not None
    assert min(d1["wind_direction_10m"], 360 - d1["wind_direction_10m"]) < 1e-6   # circular mean of 350/10 is ~0, not 180
    assert d1["valid_counts"]["temperature_2m"] == 24
    d5 = recs["2021-03-05"]
    assert d5["temperature_2m"] is None and d5["valid_counts"]["temperature_2m"] == 12   # under 18 valid hours -> null, never a partial mean
    assert d5["relative_humidity_2m"] is not None                          # other variables of that day are unaffected


def test_missing_weather_values_are_null_not_zero(client):
    h = get(client, interval="hourly", start_date="2021-05-10", end_date="2021-05-10", variables="boundary_layer_height,temperature_2m").json()
    assert all(x["boundary_layer_height"] is None for x in h["records"]) and all(x["temperature_2m"] is not None for x in h["records"])
    m = {x["timestamp"]: x for x in get(client, interval="monthly", variables="boundary_layer_height", start_date="2021-04-01", end_date="2021-06-30").json()["records"]}
    assert m["2021-05"]["boundary_layer_height"] is None and m["2021-04"]["boundary_layer_height"] == 300.0


def test_monthly_needs_75_percent_of_days(client):
    m = {x["timestamp"]: x for x in get(client, interval="monthly", variables="temperature_2m", start_date="2021-03-01", end_date="2021-03-31").json()["records"]}
    assert m["2021-03"]["temperature_2m"] is not None                      # 30 of 31 days valid
    assert m["2021-03"]["valid_counts"]["temperature_2m"] == 30


def test_date_filtering_and_paging(client):
    b = get(client, interval="hourly", start_date="2021-03-02", end_date="2021-03-03", limit=10).json()
    assert b["total"] == 48 and b["count"] == 10 and b["truncated"] is True
    assert b["records"][0]["timestamp"].startswith("2021-03-02T00:00")
    assert get(client, interval="daily", start_date="2030-01-01", end_date="2030-01-05").json()["count"] == 0


def test_variable_selection(client):
    b = get(client, interval="daily", start_date="2021-03-01", end_date="2021-03-01", variables="temperature_2m,precipitation").json()
    assert set(b["variables"]) == {"temperature_2m", "precipitation"} and "wind_speed_10m" not in b["records"][0]


def test_invalid_parameters_are_explained(client):
    for params, text in (({"variables": "dew_point"}, "Unknown weather variable"), ({"start_date": "03/01/2021"}, "calendar date"), ({"start_date": "2021-03-05", "end_date": "2021-03-01"}, "later than"),
                         ({"end_date": "2021-02-30"}, "not a valid date")):
        r = get(client, **params)
        assert r.status_code == 422 and text in r.json()["message"], params
    assert get(client, interval="weekly").status_code == 422


def test_missing_weather_file_gives_a_503_with_the_pipeline_hint(data_dir, store, client):
    (data_dir / "processed/weather/era5_hourly.parquet").unlink()
    r = get(client)
    assert r.status_code == 503 and "weather" in r.json()["message"] and "process_weather" in r.json()["hint"]


def test_open_meteo_is_never_called_by_the_api(client, monkeypatch):
    import socket

    def boom(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", boom)
    assert get(client, interval="daily", start_date="2021-03-01", end_date="2021-03-01").status_code == 200
