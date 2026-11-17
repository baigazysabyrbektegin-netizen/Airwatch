from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from pipeline import clarity_clean as cc
from pipeline import process_clarity as pc

CFG = cc.ClarityConfig()
COLS = ["Device ID", "PM25", "year", "month", "day", "hour", "Quadrant"]


def data_df(rows):
    """Extracted-sheet style frame: text cells, None -> NaN, Excel row numbers from 2."""
    df = pd.DataFrame([[None if v is None else str(v) for v in r] + [None] * (7 - len(r)) for r in rows], columns=COLS, dtype="str")
    df.insert(0, "excel_row", np.arange(2, len(df) + 2))
    return df


def loc_df(devs):
    """Locations sheet frame (letter columns, as read_grid returns it)."""
    rows = []
    for i, t in enumerate(devs, start=2):
        d = {"excel_row": i, "A": str(i - 1), "B": t[0], "C": t[1], "D": t[2]}
        if len(t) > 3 and t[3]:
            d.update({"M": t[0], "P": t[3]})
        rows.append(d)
    df = pd.DataFrame(rows, dtype="str")
    df["excel_row"] = df["excel_row"].astype(int)
    hdr = pd.DataFrame([{"excel_row": 1, "B": "Device ID", "C": "latitude", "D": "longitude"}], dtype="str")
    hdr["excel_row"] = 1
    return pd.concat([hdr, df], ignore_index=True)


def inv_for(*ids, cfg=CFG):
    return cc.build_inventory(loc_df([(d, f"42.{80 + i}", f"74.{50 + i}") for i, d in enumerate(ids)]), cfg)


def audit(rows, devs=("D1", "D2"), cfg=CFG):
    return cc.build_audit(data_df(rows), inv_for(*devs, cfg=cfg), cfg)


def local(ts) -> str:
    return str(pd.Timestamp(ts).tz_convert("Asia/Bishkek"))


# ------------------------------------------------------------------ inventory
def test_inventory_matches_flags_and_preserves_original_text():
    inv = cc.build_inventory(loc_df([
        ("D1", "42.9", "74.7", "Q1"), ("D2", "42.8", "74.5", "Q3"), ("D3", "42.8", "74.5"), ("D4", "abc", "74.5"),
        ("D1", "42.95", "74.7"), ("D5", "42.9", "74.7", "Q3"), ("D6", "50.0", "74.5")]), CFG)
    by = inv.drop_duplicates("device_id", keep="last").set_index("device_id")
    assert inv["latitude_src"].tolist()[:4] == ["42.9", "42.8", "42.8", "abc"]           # original text kept
    assert inv.loc[inv["device_id"] == "D1", "flag_duplicate_device_row"].all()          # D1 listed twice
    assert by.loc["D2", "flag_shared_coordinates"] and by.loc["D3", "flag_shared_coordinates"]
    assert by.loc["D2", "shared_coordinate_group"] == by.loc["D3", "shared_coordinate_group"]
    assert not by.loc["D4", "flag_coordinates_valid"] and np.isnan(by.loc["D4", "latitude"])   # nothing invented
    assert pd.isna(by.loc["D4", "quadrant_rule"])                                       # no quadrant without coordinates
    assert by.loc["D6", "flag_coordinates_valid"] and not by.loc["D6", "flag_in_study_domain"]
    first = inv[inv["device_id"] == "D1"].iloc[0]
    assert (first["quadrant_rule"], first["quadrant_workbook_lookup"]) == ("Q1", "Q1") and first["quadrant_consistent"] is True
    d5 = by.loc["D5"]
    assert (d5["quadrant_rule"], d5["quadrant_workbook_lookup"]) == ("Q1", "Q3") and d5["quadrant_consistent"] is False   # disagreement is visible
    assert pd.isna(by.loc["D3", "quadrant_workbook_lookup"]) and by.loc["D3", "quadrant_consistent"] is None


def test_quadrant_rule_uses_workbook_thresholds_and_boundaries():
    q = lambda a, b: cc.quadrant_from_coords(a, b, CFG)  # noqa: E731
    assert (q(42.9, 74.7), q(42.8, 74.7), q(42.8, 74.5), q(42.9, 74.5)) == ("Q1", "Q2", "Q3", "Q4")
    assert q(42.87, 74.7) is None and q(42.9, 74.6) is None and q(None, 74.5) is None


def test_inventory_status_no_data_in_file_and_not_active():
    a = audit([("D1", 10, 2021, 3, 1, 1)], devs=("D1", "D2", "D3"))
    a = cc.add_neighbor_flags(a, CFG)
    h = cc.build_hourly(a)
    inv = cc.finish_inventory(inv_for("D1", "D2", "D3"), a, h)
    assert inv.set_index("device_id")["status"].to_dict() == {"D1": "has_data_in_file", "D2": "no_data_in_file", "D3": "no_data_in_file"}
    assert inv["n_records_in_file"].tolist() == [1, 0, 0] and len(inv) == 3          # nothing deleted


# ------------------------------------------------------------------ records, missing values, invalid timestamps
def test_blank_rows_are_not_records():
    a = audit([("D1", 10, 2021, 3, 1, 1), (None,) * 6])
    assert a["row_kind"].tolist() == ["record", "blank_row"]
    assert a["quality_flag"].tolist() == ["VALID", "BLANK_ROW"] and not a["flag_time_invalid"].any()


def test_missing_nonnumeric_and_unknown_values_are_flagged_never_zero():
    a = audit([("D1", None, 2021, 3, 1, 2), ("D1", "abc", 2021, 3, 1, 3), ("ZZ", 5, 2021, 3, 1, 4), (None, 5, 2021, 3, 1, 6), ("D1", 7, 2021, 3, 1, 5)])
    assert a["quality_flag"].tolist() == ["PM_MISSING", "PM_NONNUMERIC", "UNKNOWN_DEVICE", "DEVICE_MISSING", "VALID"]
    assert a["pm25_ugm3"].isna().tolist()[:2] == [True, True] and not (a["pm25_ugm3"] == 0).any()
    assert a["src_pm25"].tolist()[:2] == [np.nan, "abc"] or (pd.isna(a["src_pm25"].iloc[0]) and a["src_pm25"].iloc[1] == "abc")
    assert a["is_primary_valid"].tolist() == [False, False, False, False, True]
    assert len(a) == 5                                                       # nothing dropped from the audit data


@pytest.mark.parametrize(
    "row,issue",
    [
        (("D1", 9, 2021, 3, 1, None), "hour_missing"),
        (("D1", 9, None, 3, 1, 5), "year_missing"),
        (("D1", 9, 2021, 13, 1, 5), "invalid_calendar_date"),
        (("D1", 9, 2021, 2, 30, 5), "invalid_calendar_date"),
        (("D1", 9, 2021, 3, 1, 25), "hour_out_of_range"),
        (("D1", 9, 2021, 3, 1, 0), "hour_out_of_range"),
        (("D1", 9, 2021, 3, 1, "x"), "hour_not_integer"),
        (("D1", 9, 2021, 3, 1, "3.5"), "hour_not_integer"),
        (("D1", 9, "20x1", 3, 1, 5), "year_not_integer"),
    ],
)
def test_invalid_or_missing_timestamp_components_are_flagged_and_kept(row, issue):
    a = audit([row])
    r = a.iloc[0]
    assert r["flag_time_invalid"] and r["time_issue"] == issue and r["quality_flag"] == "TIME_INVALID"
    assert pd.isna(r["ts_start_utc"]) and pd.isna(r["ts_label_utc"])        # no guessed timestamp
    assert not r["is_primary_valid"] and r["pm25_ugm3"] == 9.0              # value and row preserved in the audit data
    assert len(cc.build_hourly(cc.add_neighbor_flags(a.copy(), CFG))) == 0


def test_original_components_are_preserved_as_text():
    a = audit([("D1", "12.50", "2021", "03", "01", "024.0")])
    r = a.iloc[0]
    assert (r["src_year"], r["src_month"], r["src_day"], r["src_hour"], r["src_pm25"]) == ("2021", "03", "01", "024.0", "12.50")
    assert r["hour_label"] == 24 and r["time_issue"] == ""


# ------------------------------------------------------------------ hour 1-24, rollover, configuration
def test_hour_1_to_24_default_hour_ending_and_hour_24_rollover():
    a = audit([("D1", 1, 2021, 3, 1, 1), ("D1", 1, 2021, 3, 1, 12), ("D1", 1, 2021, 3, 1, 24), ("D1", 1, 2021, 12, 31, 24)])
    label = a["ts_label_utc"].map(local).tolist()
    start = a["ts_start_local"].astype(str).tolist()
    assert label == ["2021-03-01 01:00:00+06:00", "2021-03-01 12:00:00+06:00", "2021-03-02 00:00:00+06:00", "2022-01-01 00:00:00+06:00"]
    assert start == ["2021-03-01 00:00:00+06:00", "2021-03-01 11:00:00+06:00", "2021-03-01 23:00:00+06:00", "2021-12-31 23:00:00+06:00"]
    assert (a["ts_end_utc"] - a["ts_start_utc"] == pd.Timedelta(hours=1)).all()
    assert a["ts_start_local"].iloc[2].date() == a["ts_start_local"].iloc[0].date()      # hour 24 stays on its own day under hour_ending


def test_hour_start_convention_moves_hour_24_to_the_next_day():
    c = cc.ClarityConfig(hour_convention="hour_start")
    a = audit([("D1", 1, 2021, 3, 1, 1), ("D1", 1, 2021, 3, 1, 24)], cfg=c)
    assert a["ts_start_local"].astype(str).tolist() == ["2021-03-01 01:00:00+06:00", "2021-03-02 00:00:00+06:00"]


def test_timezone_and_extra_shift_configuration():
    base = audit([("D1", 1, 2021, 3, 1, 1)]).iloc[0]
    utc = audit([("D1", 1, 2021, 3, 1, 1)], cfg=cc.ClarityConfig(source_timezone="UTC")).iloc[0]
    shifted = audit([("D1", 1, 2021, 3, 1, 1)], cfg=cc.ClarityConfig(extra_shift_hours=1)).iloc[0]
    assert str(base["ts_start_utc"]) == "2021-02-28 18:00:00+00:00"          # 00:00 Bishkek
    assert str(utc["ts_start_utc"]) == "2021-03-01 00:00:00+00:00"           # label read as UTC
    assert utc["ts_start_utc"] - base["ts_start_utc"] == pd.Timedelta(hours=6)
    assert shifted["ts_start_utc"] - base["ts_start_utc"] == pd.Timedelta(hours=1)
    assert str(base["ts_start_local"].tz) == "Asia/Bishkek"


def test_default_never_shifts_and_config_is_validated_and_documented():
    assert CFG.extra_shift_hours == 0
    with pytest.raises(cc.ClarityCleanError):
        cc.ClarityConfig(hour_convention="nonsense")
    with pytest.raises(cc.ClarityCleanError):
        cc.ClarityConfig(source_timezone="Mars/Base")
    with pytest.raises(cc.ClarityCleanError):
        cc.ClarityConfig(extra_shift_hours=1.5)
    ta = CFG.timestamp_assumptions()
    assert ta["status"].startswith("ASSUMPTION") and "24 -> 00:00 next day" in ta["mapping"]


# ------------------------------------------------------------------ high values, neighbours, duplicates
def test_high_value_flag_threshold_and_not_deleted():
    a = audit([("D1", 500, 2021, 3, 1, 1), ("D1", 500.01, 2021, 3, 1, 2), ("D1", 900, 2021, 3, 1, 3)])
    assert a["flag_high_value"].tolist() == [False, True, True]
    assert cc.build_hourly(cc.add_neighbor_flags(a, CFG))["pm25_ugm3"].tolist() == [500.0, 500.01, 900.0]
    assert a["src_pm25"].tolist() == ["500", "500.01", "900"]


def test_neighbor_outlier_flag_needs_enough_other_sensors_and_deletes_nothing():
    ids = [f"D{i}" for i in range(7)]
    rows = [(d, 10, 2021, 3, 1, 5) for d in ids[:6]] + [("D6", 80, 2021, 3, 1, 5)]
    a = cc.add_neighbor_flags(audit(rows, devs=ids), CFG)
    assert a["flag_neighbor_outlier"].tolist() == [False] * 6 + [True]
    assert a["neighbor_median_ugm3"].iloc[6] == 10 and a["neighbor_ratio"].iloc[6] == 8 and a["n_neighbors"].iloc[6] == 6
    assert a["is_primary_valid"].all() and len(cc.build_hourly(a)) == 7              # flagged, not discarded
    few = cc.add_neighbor_flags(audit([("D0", 10, 2021, 3, 1, 5), ("D1", 10, 2021, 3, 1, 5), ("D2", 100, 2021, 3, 1, 5)], devs=ids), CFG)
    assert not few["flag_neighbor_outlier"].any() and few["n_neighbors"].tolist() == [2, 2, 2]  # too few peers: no verdict
    low = cc.ClarityConfig(neighbor_min_others=2)
    assert cc.add_neighbor_flags(audit([("D0", 10, 2021, 3, 1, 5), ("D1", 10, 2021, 3, 1, 5), ("D2", 100, 2021, 3, 1, 5)], devs=ids, cfg=low), low)["flag_neighbor_outlier"].tolist() == [False, False, True]


def test_neighbor_comparison_only_within_the_same_interval():
    ids = [f"D{i}" for i in range(7)]
    rows = [(d, 10, 2021, 3, 1, 5) for d in ids[:6]] + [("D6", 80, 2021, 3, 1, 6)]      # outlier is alone in its hour
    a = cc.add_neighbor_flags(audit(rows, devs=ids), CFG)
    assert not a["flag_neighbor_outlier"].any()


def test_duplicate_device_interval_identical_vs_conflicting():
    same = audit([("D1", 10, 2021, 3, 1, 5), ("D1", 10.0, 2021, 3, 1, 5)])
    assert same["flag_duplicate_device_hour"].all() and not same["flag_duplicate_conflict"].any()
    assert same["is_primary_valid"].tolist() == [True, False]
    diff = audit([("D1", 10, 2021, 3, 1, 5), ("D1", 12, 2021, 3, 1, 5), ("D2", 5, 2021, 3, 1, 5)])
    assert diff["flag_duplicate_conflict"].tolist() == [True, True, False]
    assert diff["quality_flag"].tolist() == ["DUP_CONFLICT", "DUP_CONFLICT", "VALID"]
    assert diff["is_primary_valid"].tolist() == [False, False, True]


# ------------------------------------------------------------------ coverage, aggregates
def _hourly(rows, devs=("D1", "D2", "D3"), cfg=CFG):
    a = cc.add_neighbor_flags(audit(rows, devs=devs, cfg=cfg), cfg)
    return a, cc.build_hourly(a)


def test_device_coverage_missing_hours_and_gaps():
    rows = [("D2", 10, 2021, 3, 1, h) for h in range(1, 21)] + [("D1", 10, 2021, 3, 1, h) for h in (1, 2, 3, 6, 7, 8, 9, 10)]
    a, h = _hourly(rows)
    d, m = cc.build_daily(h, CFG), None
    m = cc.build_monthly(d, h, CFG)
    cov = cc.device_coverage(a, h, m, cc.finish_inventory(inv_for("D1", "D2", "D3"), a, h), CFG).set_index("device_id")
    assert cov.loc["D2", ["valid_readings", "coverage_pct_network_window", "missing_hours_network_window"]].tolist() == [20, 100.0, 0]
    assert cov.loc["D1", "valid_readings"] == 8 and cov.loc["D1", "expected_hours_network_window"] == 20
    assert cov.loc["D1", "coverage_pct_network_window"] == pytest.approx(40.0) and cov.loc["D1", "missing_hours_network_window"] == 12
    assert cov.loc["D1", "own_span_hours"] == 10 and cov.loc["D1", "coverage_pct_own_span"] == pytest.approx(80.0)
    assert cov.loc["D1", "longest_gap_hours"] == 2 and bool(cov.loc["D1", "flag_low_coverage"])
    assert "D3" not in cov.index                                         # no readings: stays in the inventory only
    assert cov.loc["D1", "total_records"] == 8 and cov.loc["D1", "invalid_or_excluded_records"] == 0


def test_daily_threshold_requires_18_valid_hours():
    rows = [("D1", 10 + h, 2021, 3, 1, h) for h in range(1, 18)] + [("D1", 10, 2021, 3, 2, h) for h in range(1, 19)]
    _, h = _hourly(rows)
    daily = cc.build_daily(h, CFG)
    d = daily.set_index(daily["date_local"].dt.strftime("%Y-%m-%d"))
    assert d.loc["2021-03-01", "n_valid_hours"] == 17 and not d.loc["2021-03-01", "meets_min_hours"]
    assert np.isnan(d.loc["2021-03-01", "pm25_daily_mean_ugm3"])                    # too few hours: no daily mean
    assert d.loc["2021-03-01", "pm25_mean_all_valid"] == pytest.approx(19.0)       # mean of 10+h, h=1..17, kept for reference only
    assert d.loc["2021-03-02", "n_valid_hours"] == 18 and d.loc["2021-03-02", "pm25_daily_mean_ugm3"] == 10.0
    d2 = cc.build_daily(h, cc.ClarityConfig(daily_min_valid_hours=17))
    assert d2["meets_min_hours"].all()


def test_monthly_threshold_requires_75_percent_of_eligible_days():
    rows = []
    for day, val in zip(range(1, 7), (10, 20, 30, 40, 50, 60)):          # D1: 6 full days
        rows += [("D1", val, 2021, 3, day, h) for h in range(1, 25)]
    for day in range(1, 6):                                              # D2: 5 full days
        rows += [("D2", 7, 2021, 3, day, h) for h in range(1, 25)]
    rows += [("D3", 5, 2021, 3, 1, 1), ("D3", 5, 2021, 3, 8, 1)]         # D3 only defines the window: 8 eligible days
    _, h = _hourly(rows)
    d = cc.build_daily(h, CFG)
    m = cc.build_monthly(d, h, CFG).set_index("device_id")
    assert m.loc["D1", "eligible_days"] == 8 and m.loc["D1", "n_valid_days"] == 6 and m.loc["D1", "valid_day_fraction"] == 0.75
    assert bool(m.loc["D1", "meets_threshold"]) and m.loc["D1", "pm25_monthly_mean_ugm3"] == pytest.approx(35.0)
    assert m.loc["D2", "valid_day_fraction"] == pytest.approx(0.625) and not m.loc["D2", "meets_threshold"]
    assert np.isnan(m.loc["D2", "pm25_monthly_mean_ugm3"])
    assert not m.loc["D3", "meets_threshold"]


def test_monthly_drift_flags_only_a_monotonic_large_change():
    months = [f"2021-{m:02d}" for m in range(3, 11)]
    mk = lambda dev, vals: pd.DataFrame({"device_id": dev, "year_month": months, "pm25_monthly_mean_ugm3": vals})  # noqa: E731
    monthly = pd.concat([mk("A", [10, 12, 14, 17, 20, 24, 27, 30]), mk("B", [10] * 8), mk("C", [10] * 8)])
    drift = cc.monthly_drift(monthly, CFG).set_index("device_id")
    assert bool(drift.loc["A", "flag_drift_candidate"]) and drift.loc["A", "spearman_rho_vs_month"] == pytest.approx(1.0)
    assert drift.loc["A", "ratio_range_factor"] == pytest.approx(3.0)
    assert not drift.loc["B", "flag_drift_candidate"] and not drift.loc["C", "flag_drift_candidate"]


def test_spearman_helper():
    assert cc.spearman([1, 2, 3, 4], [10, 20, 30, 45]) == pytest.approx(1.0)
    assert cc.spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert np.isnan(cc.spearman([1, 1, 1], [1, 2, 3]))


# ------------------------------------------------------------------ reproducibility, parquet, guards
def test_processing_is_reproducible():
    rows = [("D1", 10 + h, 2021, 3, 1, h) for h in range(1, 25)] + [("D2", None, 2021, 3, 1, 3), (None,) * 6, ("D1", 5, 2021, 13, 1, 1)]
    a1, a2 = audit(rows), audit(rows)
    pd.testing.assert_frame_equal(a1, a2)
    pd.testing.assert_frame_equal(cc.build_hourly(cc.add_neighbor_flags(a1, CFG)), cc.build_hourly(cc.add_neighbor_flags(a2, CFG)))


def test_hourly_parquet_round_trip_with_assumptions_in_metadata(tmp_path):
    rows = [("D1", 10 + h, 2021, 3, 1, h) for h in range(1, 25)]
    _, h = _hourly(rows)
    p = tmp_path / "h.parquet"
    meta = {"timestamp_assumptions": CFG.timestamp_assumptions()}
    assert pc.write_parquet(h, p, meta, False) == "written"
    back = pd.read_parquet(p)
    pd.testing.assert_frame_equal(back, h, check_dtype=False)
    assert str(back["ts_start_local"].dt.tz) == "Asia/Bishkek" and str(back["ts_start_utc"].dt.tz) == "UTC"
    stored = pq.read_schema(p).metadata[b"airwatch.processing"].decode()
    assert "hour_ending" in stored and "ASSUMPTION" in stored
    assert list(back.columns) == cc.HOURLY_COLUMNS and back["pm25_ugm3"].tolist() == h["pm25_ugm3"].tolist()


def test_guarded_write_never_replaces_a_different_file_without_approval(tmp_path):
    p = tmp_path / "x.csv"
    df = pd.DataFrame({"a": [1]})
    assert pc.write_csv(df, p, False) == "written" and pc.write_csv(df, p, False) == "unchanged"
    with pytest.raises(pc.OutputExistsError, match="allow-overwrite"):
        pc.write_csv(pd.DataFrame({"a": [2]}), p, False)
    assert p.read_text() == "a\n1\n"
    assert pc.write_csv(pd.DataFrame({"a": [2]}), p, True) == "overwritten_with_approval" and p.read_text() == "a\n2\n"
    assert [f.name for f in tmp_path.iterdir()] == ["x.csv"]            # no temp files left behind
