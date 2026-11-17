from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from pipeline import embassy_clean as ec
from pipeline import embassy_report as er
from pipeline import process_embassy as pe
from pipeline.tests.emb_helpers import T0, clean, hours, row, write_csv


# ------------------------------------------------------------------ reading
def test_read_preserves_source_text_exactly(tmp_path):
    rows = [row(T0, "12.0"), row(T0 + timedelta(hours=1), "-999.0", qc="Missing", now="-999.0")]
    source, _, _ = clean(tmp_path, rows)
    assert source["src_raw_conc"].tolist() == ["12.0", "-999.0"]
    assert source["src_nowcast_conc"].tolist() == ["10.0", "-999.0"]
    assert source["src_qc_name"].tolist() == ["Valid", "Missing"]
    assert source["source_line"].tolist() == [2, 3]  # header is line 1


def test_read_rejects_unexpected_columns(tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    (d / "x.csv").write_text("Site,Foo\r\nA,B\r\n", encoding="utf-8")
    with pytest.raises(ec.CleaningError, match="unexpected columns"):
        ec.read_source_csvs(d)


def test_read_fails_without_csv(tmp_path):
    with pytest.raises(ec.CleaningError, match="No CSV"):
        ec.read_source_csvs(tmp_path)


# ------------------------------------------------------------------ timestamps and timezone
def test_timestamp_parsing_12_hour_clock(tmp_path):
    base = datetime(2020, 3, 5)
    rows = [row(base + timedelta(hours=h), 10) for h in (0, 1, 11, 12, 13, 23)]
    _, a, _ = clean(tmp_path, rows)
    assert a["ts_original"].tolist()[0] == "2020-03-05 12:00 AM"   # original text preserved
    assert a["ts_original"].tolist()[3] == "2020-03-05 12:00 PM"
    assert a["ts_local"].dt.hour.tolist() == [0, 1, 11, 12, 13, 23]
    assert not a["flag_time_unparsed"].any() and not a["flag_time_field_mismatch"].any()


def test_unparsable_timestamp_is_flagged_not_dropped(tmp_path):
    rows = [row(T0, 10), row(T0, 11, ts_text="2020-13-45 99:99 XM")]
    _, a, h = clean(tmp_path, rows)
    assert len(a) == 2
    bad = a[a["flag_time_unparsed"]]
    assert len(bad) == 1 and bad["quality_flag"].iloc[0] == "TIME_UNPARSED"
    assert bad["ts_utc"].isna().all() and bad["pm25_model_ugm3"].isna().all()
    assert len(h) == 1  # the unparsable row cannot be placed on the timeline


def test_time_fields_disagreeing_with_timestamp_are_flagged(tmp_path):
    r = row(T0, 10)
    r[6] = "07"  # Hour column contradicts Date (LT)
    _, a, _ = clean(tmp_path, [r])
    assert a["flag_time_field_mismatch"].iloc[0]


def test_timezone_conversion_bishkek_to_utc(tmp_path):
    _, a, h = clean(tmp_path, [row(datetime(2021, 7, 1, 6, 0), 10)])
    assert str(a["ts_local"].dt.tz) == "Asia/Bishkek" and str(a["ts_utc"].dt.tz) == "UTC"
    assert a["ts_local"].iloc[0].utcoffset() == timedelta(hours=6)
    assert a["ts_utc"].iloc[0] == pd.Timestamp("2021-07-01 00:00", tz="UTC")
    assert h["ts_utc"].iloc[0] == a["ts_utc"].iloc[0] and h["ts_local"].iloc[0] == a["ts_local"].iloc[0]


def test_no_daylight_saving_shift_in_data_period(tmp_path):
    # Spring/autumn dates when US/Europe change clocks must not create gaps or shifts here.
    rows = [row(t, 10) for t in hours(datetime(2019, 3, 10, 0), 6)]
    _, a, h = clean(tmp_path, rows)
    assert h["n_source_rows"].tolist() == [1] * 6
    assert (a["ts_utc"].diff().dropna() == pd.Timedelta(hours=1)).all()


# ------------------------------------------------------------------ values and flags
def test_sentinel_999_is_missing_never_zero(tmp_path):
    rows = [row(T0, "-999.0", qc="Missing", now="-999.0"), row(T0 + timedelta(hours=1), 5)]
    _, a, h = clean(tmp_path, rows)
    first = a.iloc[0]
    assert first["flag_sentinel_999"] and first["flag_missing_value"]
    assert np.isnan(first["pm25_raw_ugm3"]) and np.isnan(first["pm25_model_ugm3"]) and np.isnan(first["nowcast_ugm3"])
    assert first["quality_flag"] == "MISSING_VALUE"
    assert (h["pm25_model_ugm3"].dropna() != 0).all()
    assert h["quality_flag"].tolist() == ["MISSING_VALUE", "VALID"]


def test_valid_qc_with_sentinel_value_is_still_missing(tmp_path):
    _, a, _ = clean(tmp_path, [row(T0, "-999.0", qc="Valid")])
    assert a["flag_missing_value"].iloc[0] and a["quality_flag"].iloc[0] == "MISSING_VALUE"
    assert np.isnan(a["pm25_model_ugm3"].iloc[0])


def test_negative_values_kept_flagged_and_clipped_for_model(tmp_path):
    _, a, h = clean(tmp_path, [row(T0, "-3.0"), row(T0 + timedelta(hours=1), "0.0"), row(T0 + timedelta(hours=2), "4")])
    assert a["pm25_raw_ugm3"].tolist() == [-3.0, 0.0, 4.0]            # original kept
    assert a["flag_neg_noise"].tolist() == [True, False, False]
    assert a["pm25_model_ugm3"].tolist() == [0.0, 0.0, 4.0]           # clipped copy
    assert h["pm25_raw_ugm3"].iloc[0] == -3.0 and h["flag_neg_noise"].tolist() == [True, False, False]


def test_high_values_flagged_not_removed(tmp_path):
    _, a, h = clean(tmp_path, [row(T0, 900), row(T0 + timedelta(hours=1), 120)])
    assert a["flag_high_value"].tolist() == [True, False]
    assert h["pm25_model_ugm3"].tolist() == [900.0, 120.0]            # still in the primary series
    cfg = ec.CleaningConfig(high_value_ugm3=100)
    _, a2, _ = clean(tmp_path / "b", [row(T0, 120)], cfg)
    assert a2["flag_high_value"].iloc[0]


@pytest.mark.parametrize(
    "text,expected",
    [("Valid", "VALID"), ("valid", "VALID"), (" VALID ", "VALID"), ("Invalid", "INVALID"), ("Missing", "MISSING"),
     ("Suspect", "SUSPECT"), ("Weird", "UNKNOWN"), ("", "UNKNOWN")],
)
def test_qc_classification(text, expected):
    assert ec.classify_qc(pd.Series([text])).iloc[0] == expected


def test_original_qc_preserved_and_non_valid_get_no_model_value(tmp_path):
    rows = [row(T0, 10, qc="Valid"), row(T0 + timedelta(hours=1), 20, qc="Invalid"),
            row(T0 + timedelta(hours=2), 30, qc="Suspect"), row(T0 + timedelta(hours=3), 40, qc="Weird")]
    _, a, h = clean(tmp_path, rows)
    assert a["qc_name_original"].tolist() == ["Valid", "Invalid", "Suspect", "Weird"]
    assert a["quality_flag"].tolist() == ["VALID", "INVALID", "SUSPECT", "UNKNOWN_QC"]
    assert h["pm25_model_ugm3"].notna().tolist() == [True, False, False, False]
    assert h["pm25_raw_ugm3"].tolist() == [10.0, 20.0, 30.0, 40.0]    # raw still visible for audit
    assert len(a) == 4                                                 # nothing dropped


def test_unit_mismatch_excluded_from_model(tmp_path):
    _, a, h = clean(tmp_path, [row(T0, 10, unit="PPB")])
    assert a["flag_unit_mismatch"].iloc[0] and a["quality_flag"].iloc[0] == "UNIT_MISMATCH"
    assert h["pm25_model_ugm3"].isna().all()


# ------------------------------------------------------------------ duplicates
def test_conflicting_duplicates_are_excluded_and_flagged(tmp_path):
    rows = [row(T0, 27), row(T0, 26), row(T0 + timedelta(hours=1), 30)]
    _, a, h = clean(tmp_path, rows)
    assert a["flag_duplicate_ts"].tolist() == [True, True, False]
    assert a["flag_duplicate_conflict"].tolist() == [True, True, False]
    assert a["quality_flag"].tolist()[:2] == ["DUP_CONFLICT", "DUP_CONFLICT"]
    assert not a["is_primary_valid"].iloc[:2].any()
    first = h.iloc[0]
    assert first["quality_flag"] == "DUP_CONFLICT" and first["n_source_rows"] == 2 and first["flag_duplicate_conflict"]
    assert np.isnan(first["pm25_model_ugm3"]) and np.isnan(first["pm25_raw_ugm3"])
    assert len(h) == 2


def test_identical_duplicates_keep_first_row_only(tmp_path):
    _, a, h = clean(tmp_path, [row(T0, 27), row(T0, 27.0)])
    assert a["flag_duplicate_ts"].all() and not a["flag_duplicate_conflict"].any()
    assert a["is_primary_valid"].tolist() == [True, False]
    assert h["pm25_model_ugm3"].iloc[0] == 27.0 and h["n_source_rows"].iloc[0] == 2


def test_duplicate_with_one_valid_one_invalid_uses_the_valid_row(tmp_path):
    _, a, h = clean(tmp_path, [row(T0, 500, qc="Invalid"), row(T0, 12, qc="Valid")])
    assert not a["flag_duplicate_conflict"].any()
    assert a["is_primary_valid"].tolist() == [False, True]
    assert h["pm25_model_ugm3"].iloc[0] == 12.0 and h["quality_flag"].iloc[0] == "VALID"


def test_duplicates_across_files_are_detected(tmp_path):
    d = tmp_path / "src"
    write_csv(d, "Bishkek_PM2.5_2021_YTD.csv", [row(datetime(2022, 1, 1, 1), 15)])
    write_csv(d, "Bishkek_PM2.5_2022_YTD.csv", [row(datetime(2022, 1, 1, 1), 38)])
    a = ec.clean_rows(ec.read_source_csvs(d))
    assert a["flag_duplicate_conflict"].all()
    assert sorted(a["source_file"]) == ["Bishkek_PM2.5_2021_YTD.csv", "Bishkek_PM2.5_2022_YTD.csv"]


# ------------------------------------------------------------------ timeline, gaps
def test_missing_hours_are_detected_and_timeline_is_complete(tmp_path):
    rows = [row(T0 + timedelta(hours=i), 10) for i in (0, 1, 4)]
    _, _, h = clean(tmp_path, rows)
    assert len(h) == 5
    assert h["row_present"].tolist() == [True, True, False, False, True]
    assert h["quality_flag"].tolist() == ["VALID", "VALID", "NO_ROW", "NO_ROW", "VALID"]
    assert (h["ts_utc"].diff().dropna() == pd.Timedelta(hours=1)).all()
    assert h.loc[~h["row_present"], "pm25_model_ugm3"].isna().all()   # absent hours are never filled with 0


def test_gap_calculation_runs_and_kinds(tmp_path):
    spec = {0: "Valid", 3: "Valid", 4: "Invalid", 5: "Valid", 6: "Invalid", 8: "Valid"}  # 1,2 and 7 have no row
    rows = [row(T0 + timedelta(hours=i), 10, qc=q) for i, q in spec.items()]
    _, _, h = clean(tmp_path, rows)
    g = ec.find_gaps(h)
    assert g[["hours", "kind"]].values.tolist() == [[2, "no_source_rows"], [1, "rows_not_valid"], [2, "mixed"]]
    assert g["hours"].sum() == 5 and g["hours"].max() == 2
    m = ec.find_gaps(h, only_missing_rows=True)
    assert m["hours"].tolist() == [2, 1]


def test_find_runs_edges():
    assert ec.find_runs(np.array([True, True, False, True])) == [(0, 2), (3, 4)]
    assert ec.find_runs(np.array([False, False])) == []
    assert ec.find_runs(np.array([], dtype=bool)) == []


def test_irregular_days_23_and_25_rows(tmp_path):
    day = lambda d: [datetime(2020, 5, d, h) for h in range(24)]  # noqa: E731
    rows = [row(t, 10) for t in day(1)]
    rows += [row(t, 10) for t in day(2) if t.hour != 5]            # 23 rows
    rows += [row(t, 10) for t in day(3)] + [row(datetime(2020, 5, 3, 7), 10)]  # 25 rows (identical duplicate)
    rows += [row(t, 10) for t in day(4)]
    _, _, h = clean(tmp_path, rows)
    irr = er.irregular_days(h)
    assert irr[["date", "n_rows", "kind"]].values.tolist() == [["2020-05-02", 23, "23_rows"], ["2020-05-03", 25, "25_rows"]]
    assert irr.iloc[0]["missing_hours"] == [5] and irr.iloc[1]["duplicated_hours"] == [7]


# ------------------------------------------------------------------ local flags, interpolation
def test_local_drop_and_spike_flags():
    s = pd.Series([150, 152, 1.2, 151, 149, 150, 100, 100, 900, 100, 100], dtype=float)
    drop, spike = ec.local_flags(s, ec.CleaningConfig())
    assert drop.tolist() == [False, False, True] + [False] * 8
    assert spike.tolist() == [False] * 8 + [True, False, False]


def test_local_flags_need_enough_neighbours_and_a_high_median():
    s = pd.Series([1.2, np.nan, np.nan, np.nan, 150.0])
    drop, spike = ec.local_flags(s, ec.CleaningConfig())
    assert not drop.any() and not spike.any()                       # too few neighbours: no verdict
    low = pd.Series([6.0, 5.0, 0.2, 5.0, 6.0])
    assert not ec.local_flags(low, ec.CleaningConfig())[0].any()    # median below 50: a low value is normal


def test_short_gaps_interpolated_only_in_feature_column_and_flagged(tmp_path):
    vals = {0: 10, 3: 40, 4: 41, 9: 100}   # gap of 2 (hours 1,2) and gap of 4 (hours 5-8)
    rows = [row(T0 + timedelta(hours=i), v) for i, v in vals.items()]
    _, _, h = clean(tmp_path, rows)
    assert h["pm25_feature_interp_ugm3"].iloc[1:3].round(6).tolist() == [20.0, 30.0]
    assert h["is_imputed"].tolist() == [False, True, True] + [False] * 7
    assert h["pm25_model_ugm3"].iloc[1:3].isna().all()              # the target is untouched
    assert h["pm25_feature_interp_ugm3"].iloc[5:9].isna().all()     # longer gap stays empty
    assert h.loc[~h["is_imputed"], "pm25_feature_interp_ugm3"].equals(h.loc[~h["is_imputed"], "pm25_model_ugm3"])


def test_fill_short_gaps_does_not_fill_edges():
    s = pd.Series([np.nan, 1.0, np.nan, 3.0, np.nan])
    out, imp = ec.fill_short_gaps(s, 3)
    assert np.isnan(out.iloc[0]) and np.isnan(out.iloc[4]) and out.iloc[2] == 2.0
    assert imp.tolist() == [False, False, True, False, False]


# ------------------------------------------------------------------ preservation, schema, reproducibility
def test_audit_dataset_keeps_every_original_field_unchanged(tmp_path):
    rows = [row(T0, "-3.0", now="7.5"), row(T0 + timedelta(hours=1), "-999.0", qc="Missing", now="-999.0"),
            row(T0 + timedelta(hours=2), "012.50")]
    source, a, _ = clean(tmp_path, rows)
    for col in (c for c in source.columns if c.startswith("src_")):
        assert a[col].tolist() == source[col].tolist()
    assert a["src_raw_conc"].tolist() == ["-3.0", "-999.0", "012.50"]
    assert len(a) == len(source)


def test_hourly_output_schema(tmp_path):
    _, _, h = clean(tmp_path, [row(T0 + timedelta(hours=i), 10 + i) for i in range(3)])
    assert list(h.columns) == [
        "ts_local", "ts_utc", "ts_original", "row_present", "n_source_rows", "quality_flag", "qc_name_original",
        "pm25_raw_ugm3", "pm25_model_ugm3", "pm25_feature_interp_ugm3", "is_imputed", "flag_neg_noise", "flag_high_value",
        "flag_local_drop", "flag_local_spike", "flag_duplicate_ts", "flag_duplicate_conflict", "source_file", "source_line",
        "processing_version",
    ]
    assert str(h["ts_local"].dtype).startswith("datetime64") and "Asia/Bishkek" in str(h["ts_local"].dtype)
    assert "UTC" in str(h["ts_utc"].dtype)
    for c in ("row_present", "is_imputed", "flag_neg_noise", "flag_high_value", "flag_local_drop", "flag_local_spike",
              "flag_duplicate_ts", "flag_duplicate_conflict"):
        assert h[c].dtype == bool, c
    assert (h["processing_version"] == ec.PROCESSING_VERSION).all()
    assert h["source_file"].iloc[0] == "Bishkek_PM2.5_2020_YTD.csv"


def test_parquet_round_trip_preserves_values_and_timezones(tmp_path):
    rows = [row(T0, "-3.0"), row(T0 + timedelta(hours=1), "-999.0", qc="Missing", now="-999.0"), row(T0 + timedelta(hours=3), 55),
            row(T0 + timedelta(hours=3), 56)]
    _, a, h = clean(tmp_path, rows)
    for name, df in (("hourly", h), ("rows", a)):
        p = tmp_path / f"{name}.parquet"
        pe.write_parquet(df, p)
        back = pd.read_parquet(p)
        assert list(back.columns) == list(df.columns)
        pd.testing.assert_frame_equal(back.reset_index(drop=True), df.reset_index(drop=True), check_dtype=False)
        if name == "hourly":
            assert str(back["ts_local"].dt.tz) == "Asia/Bishkek" and str(back["ts_utc"].dt.tz) == "UTC"
            assert back["pm25_raw_ugm3"].iloc[0] == -3.0 and back["pm25_model_ugm3"].iloc[0] == 0.0


def test_hourly_csv_has_iso_timestamps_and_empty_missing(tmp_path):
    _, _, h = clean(tmp_path, [row(T0, 10), row(T0 + timedelta(hours=2), 12)])
    p = tmp_path / "h.csv"
    pe.write_csv(h, p)
    text = p.read_text(encoding="utf-8")
    assert "2020-01-10 00:00:00+06:00" in text and "2020-01-09 18:00:00+00:00" in text
    back = pd.read_csv(p)
    assert pd.isna(back["pm25_model_ugm3"].iloc[1]) and back["quality_flag"].iloc[1] == "NO_ROW"


def test_processing_is_reproducible(tmp_path):
    rows = [row(T0 + timedelta(hours=i), v, qc=q) for i, (v, q) in enumerate(
        [(10, "Valid"), (-2, "Valid"), ("-999.0", "Missing"), (800, "Valid"), (5, "Invalid"), (12, "Valid")])]
    rows.append(row(T0, 11))  # duplicate conflict
    _, a1, h1 = clean(tmp_path / "one", rows)
    _, a2, h2 = clean(tmp_path / "two", rows)
    pd.testing.assert_frame_equal(a1, a2)
    pd.testing.assert_frame_equal(h1, h2)
    pe.write_csv(h1, tmp_path / "1.csv")
    pe.write_csv(h2, tmp_path / "2.csv")
    assert (tmp_path / "1.csv").read_bytes() == (tmp_path / "2.csv").read_bytes()
    pe.write_parquet(h1, tmp_path / "1.parquet")
    pe.write_parquet(h2, tmp_path / "2.parquet")
    assert (tmp_path / "1.parquet").read_bytes() == (tmp_path / "2.parquet").read_bytes()


# ------------------------------------------------------------------ statistics
def test_compute_stats_counts_on_synthetic_data(tmp_path):
    rows = [row(T0, 10), row(T0 + timedelta(hours=1), "-999.0", qc="Missing"), row(T0 + timedelta(hours=2), -4),
            row(T0 + timedelta(hours=2), -5), row(T0 + timedelta(hours=3), 700), row(T0 + timedelta(hours=6), 9, qc="Invalid")]
    _, a, h = clean(tmp_path, rows)
    st = er.compute_stats(a, h)
    assert st["rows_read"] == 6 and st["n_source_files"] == 1
    assert st["qc_counts_original"] == {"Valid": 4, "Missing": 1, "Invalid": 1}
    assert st["sentinel_999_rows"] == 1 and st["duplicate_timestamps"] == 1 and st["duplicate_conflicts"] == 1
    assert st["missing_timestamps"] == 2 and st["hours_in_timeline"] == 7
    assert st["valid_hours"] == 2 and st["high_value_hours_valid"] == 1
    assert st["gaps_missing_timestamps"]["count"] == 1 and st["gaps_missing_timestamps"]["total_hours"] == 2
    cov = st["coverage_by_year"][2020]
    assert cov["expected_hours"] == 7 and cov["valid_hours"] == 2
    md = er.render_markdown(st, ec.CleaningConfig().as_dict(), ["limit"], ["decision"])
    assert "validation report" in md and "## 14. Limitations" in md
