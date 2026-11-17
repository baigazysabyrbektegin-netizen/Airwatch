"""Spatial and Embassy-comparison functions, plus an end-to-end run of Stage 3 on a synthetic workbook."""

import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from pipeline import clarity_clean as cc
from pipeline import clarity_embassy as cem
from pipeline import clarity_spatial as cs
from pipeline import manifest as mf
from pipeline import process_clarity as pc
from pipeline.tests.test_clarity_clean import loc_df
from pipeline.tests.xlsx_helpers import clarity_workbook, data_row

# ------------------------------------------------------------------ spatial
def _sensor_frame(n=12, hours=10, step_lon=0.01, slope=0.1):
    """n sensors on an east-west line; ln level rises by `slope` per sensor, common hourly variation on top."""
    rows = []
    t0 = pd.Timestamp("2021-03-01 00:00", tz="Asia/Bishkek")
    rng = np.random.default_rng(3)
    common = np.exp(rng.normal(0, 0.3, hours))
    for i in range(n):
        for h in range(hours):
            ts = t0 + pd.Timedelta(hours=h)
            rows.append({"device_id": f"S{i:02d}", "ts_start_local": ts, "ts_start_utc": ts.tz_convert("UTC"), "pm25_ugm3": 10 * common[h] * np.exp(slope * i)})
    inv = cc.build_inventory(loc_df([(f"S{i:02d}", "42.85", f"{74.50 + step_lon * i:.4f}") for i in range(n)]), cc.ClarityConfig())
    inv["status"] = "has_data_in_file"
    return pd.DataFrame(rows), inv


def test_device_levels_are_relative_to_the_city_median():
    h, _ = _sensor_frame()
    lv = cs.device_levels(h, {3}, min_sensors=10, min_hours=5).set_index("device_id")
    assert len(lv) == 12 and (lv["n_hours"] == 10).all()
    assert np.diff(lv["a"].to_numpy()) == pytest.approx(0.1)               # the built-in level steps are recovered
    assert cs.device_levels(h, {3}, min_sensors=13, min_hours=5).empty     # not enough sensors per hour -> nothing


def test_semivariance_rises_with_distance_and_mantel_detects_structure():
    h, inv = _sensor_frame()
    lv = cs.device_levels(h, {3}, min_sensors=10, min_hours=5).merge(inv[["device_id", "latitude", "longitude"]], on="device_id")
    a, D = lv["a"].to_numpy(), cs.distance_matrix(lv["latitude"], lv["longitude"])
    sem = cs.semivariogram(a, D)
    assert sem["semivariance"].is_monotonic_increasing and sem["n_pairs"].sum() == 66
    m = cs.mantel(a, D, n_perm=199, seed=1)
    assert m["pearson_r"] > 0.9 and m["p_one_sided"] <= 0.01
    rnd = np.random.default_rng(5).normal(size=len(a))
    assert cs.mantel(rnd, D, n_perm=199, seed=1)["p_one_sided"] > 0.05


def test_mantel_is_reproducible_with_a_seed():
    rng = np.random.default_rng(2)
    a, D = rng.normal(size=10), cs.distance_matrix(42.8 + rng.random(10) / 10, 74.5 + rng.random(10) / 10)
    assert cs.mantel(a, D, n_perm=99, seed=9) == cs.mantel(a, D, n_perm=99, seed=9)


def test_distance_at_fraction_of_sill_interpolates():
    sem = pd.DataFrame({"mean_distance_km": [1.0, 2.0, 4.0], "semivariance": [0.1, 0.2, 0.4]})
    assert cs.distance_at_fraction_of_sill(sem, 0.4, 0.25) == 1.0           # already reached in the first bin
    assert cs.distance_at_fraction_of_sill(sem, 0.4, 0.5) == pytest.approx(2.0)
    assert cs.distance_at_fraction_of_sill(sem, 0.4, 0.9) == pytest.approx(3.6)
    assert cs.distance_at_fraction_of_sill(sem, 1.0, 0.9) is None            # never reached


def test_geometry_summary_hull_density_and_shared_coordinates():
    inv = cc.build_inventory(loc_df([("A", "42.80", "74.50"), ("B", "42.90", "74.50"), ("C", "42.90", "74.60"), ("D", "42.80", "74.60"),
                                     ("E", "42.85", "74.55"), ("F", "42.85", "74.55")]), cc.ClarityConfig())
    inv["status"] = ["has_data_in_file"] * 5 + ["no_data_in_file"]
    g = cs.geometry_summary(inv, inv[inv["status"] == "has_data_in_file"])
    assert g["all_listed"]["n"] == 6 and g["with_data"]["n"] == 5
    assert g["with_data"]["convex_hull_area_km2"] == pytest.approx(90.0, abs=3)
    assert g["all_listed"]["nearest_neighbour_km"]["min"] == 0.0 and g["all_listed"]["n_sharing_identical_coordinates"] == 2
    assert g["with_data"]["devices_per_km2_in_hull"] == pytest.approx(5 / g["with_data"]["convex_hull_area_km2"])


def test_spatial_analysis_reports_insufficient_data_instead_of_inventing_results():
    h, inv = _sensor_frame(n=4)
    res = cs.spatial_analysis(h, inv)
    assert all(p.get("insufficient_data") for p in res["periods"].values())
    assert res["winter_vs_summer_sd_ratio"].get("estimate") is None


# ------------------------------------------------------------------ Embassy comparison
def _series(n=500, seed=1):
    rng = np.random.default_rng(seed)
    t = pd.date_range("2021-03-01 00:00", periods=n, freq="h", tz="UTC")
    v = 20 + 15 * np.sin(np.arange(n) / 5) + rng.normal(0, 2, n) + 25 * (np.arange(n) % 24 == 20)
    return t, np.clip(v, 1, None)


def _embassy(times, values):
    return pd.DataFrame({"ts_utc": times, "ts_local": times.tz_convert("Asia/Bishkek"), "quality_flag": "VALID", "pm25_model_ugm3": values,
                         "flag_local_drop": False, "flag_local_spike": False})


def _clarity(label_times, values, dev="C1"):
    return pd.DataFrame({"device_id": dev, "ts_label_utc": label_times, "pm25_ugm3": values, "flag_high_value": False, "flag_neighbor_outlier": False})


def test_alignment_comparison_prefers_the_true_offset_and_applies_no_shift_itself():
    t, v = _series()
    emb = _embassy(t, v)
    cl = _clarity(t - pd.Timedelta(hours=1), v + np.random.default_rng(4).normal(0, 0.5, len(v)))   # Clarity label = Embassy stamp - 1 h
    r = {k: cem.regression(*[j[c].to_numpy() for c in ("embassy", "clarity")])["pearson_r"]
         for k in (-2, -1, 0, 1, 2) for j in [cem.paired(cl, emb, k, set(range(1, 13)))]}
    assert max(r, key=r.get) == 1 and r[1] > 0.99 and r[0] < r[1] - 0.1
    assert len(cem.paired(cl, emb, 0, set(range(1, 13)))) == len(v) - 1    # k = 0 pairs identical label instants only; data untouched
    assert (cl["ts_label_utc"] == t - pd.Timedelta(hours=1)).all()


def test_regression_statistics():
    x = np.arange(1, 101, dtype=float)
    s = cem.regression(x, 2 * x + 1)
    assert s["n"] == 100 and s["slope"] == pytest.approx(2.0) and s["intercept"] == pytest.approx(1.0) and s["pearson_r"] == pytest.approx(1.0)
    assert s["mean_difference_clarity_minus_embassy"] == pytest.approx(x.mean() + 1)
    assert cem.regression(x[:5], x[:5]) == {"n": 5}                         # too few pairs: no statistics
    assert cem.regression(np.ones(20), np.arange(20.0)) == {"n": 20}        # no variation: no statistics


def test_day_block_bootstrap_interval_contains_r():
    t, v = _series()
    j = cem.paired(_clarity(t, v + np.random.default_rng(4).normal(0, 3, len(v))), _embassy(t, v), 0, set(range(1, 13)))
    r = cem.regression(j["embassy"].to_numpy(), j["clarity"].to_numpy())["pearson_r"]
    lo, hi = cem.block_bootstrap_r(j, n_boot=100, seed=2)
    assert lo <= r <= hi and cem.block_bootstrap_r(j.head(10)) is None


def test_compare_lists_nearby_devices_labels_location_approximate_and_covers_all_shifts():
    t, v = _series()
    inv = pd.DataFrame({"device_id": ["NEAR", "FAR", "NODATA"], "status": ["has_data_in_file", "has_data_in_file", "no_data_in_file"],
                        "latitude": [42.8275, 42.95, 42.8276], "longitude": [74.5833, 74.7, 74.5839], "flag_coordinates_valid": True})
    hourly = pd.concat([_clarity(t - pd.Timedelta(hours=1), v, "NEAR"), _clarity(t, v, "FAR")])
    res = cem.compare(hourly, inv, _embassy(t, v))
    assert "APPROXIMATE" in res["embassy_location"]["status"]
    assert [d["device_id"] for d in res["nearby_devices"]] == ["NEAR", "NODATA"]            # FAR is outside 2 km
    assert res["nearby_devices"][0]["distance_to_embassy_approx_km"] < 0.2
    comps = [c for c in res["comparisons"] if c["period"] == "all_year"]
    assert sorted(c["shift_k_hours"] for c in comps) == [-2, -1, 0, 1, 2]                 # every alignment reported, none privileged
    assert all(c["device_id"] == "NEAR" for c in res["comparisons"])                       # no-data device is never compared


def test_load_embassy_hourly_verifies_checksum(tmp_path):
    t, v = _series(50)
    p = tmp_path / "embassy_pm25_hourly.parquet"
    _embassy(t, v).to_parquet(p, index=False)
    good = {"outputs": [{"path": "data/processed/embassy/embassy_pm25_hourly.parquet", "sha256": mf.hash_file(p, ("sha256",))["sha256"]}]}
    m = tmp_path / "m.json"
    mf.write_manifest_atomic(m, good)
    assert len(cem.load_embassy_hourly(p, m)) == 50
    mf.write_manifest_atomic(m, {"outputs": [{"path": "x/embassy_pm25_hourly.parquet", "sha256": "0" * 64}]})
    with pytest.raises(cem.EmbassyComparisonError, match="checksum"):
        cem.load_embassy_hourly(p, m)
    mf.write_manifest_atomic(m, {"outputs": []})
    with pytest.raises(cem.EmbassyComparisonError, match="not listed"):
        cem.load_embassy_hourly(p, m)
    with pytest.raises(cem.EmbassyComparisonError, match="not found"):
        cem.load_embassy_hourly(tmp_path / "nope.parquet", m)


# ------------------------------------------------------------------ end to end
@pytest.fixture
def env(tmp_path):
    devs = [("D1", "42.8275", "74.5833", "Q3"), ("D2", "42.90", "74.65", "Q1"), ("D3", "42.80", "74.55", "Q3"), ("D4", "42.85", "74.62", "Q3"),
            ("D5", "42.92", "74.55", "Q4"), ("D6", "42.83", "74.70", "Q2"), ("D7", "42.88", "74.52", "Q4"), ("D8", "42.81", "74.64", "Q3"),
            ("NA1", "42.90", "74.65"), ("NA2", "42.99", "74.40")]       # NA1 shares D2's coordinates and has no data
    recs, base = [], pd.date_range("2021-03-01", periods=72, freq="h")
    sig = 20 + 10 * np.sin(np.arange(72) / 4)
    for di, d in enumerate([x[0] for x in devs[:8]]):
        for i, ts in enumerate(base):
            hour = ts.hour + 1                                           # labels 1..24 on the date of the interval start
            recs.append(data_row(d, round(float(sig[i] + di), 2), ts.year, ts.month, ts.day, hour, "Q1"))
    wb = clarity_workbook(tmp_path / "raw" / pc.WORKBOOK_NAME, recs, devs, blank_rows=4)
    h = mf.hash_file(wb, ("md5", "sha256"))
    zen = {"record": {"record_id": 1, "doi": "10.0/x", "licence": {"id": "cc-by-4.0"}},
           "files": [{"filename": pc.WORKBOOK_NAME, "local_path": str(wb), "download_status": "downloaded_verified", "size_bytes": wb.stat().st_size,
                      "zenodo_checksum": {"algorithm": "md5", "value": h["md5"]}, "calculated_checksums": h, "provenance": {"review_required": True}}]}
    zpath = tmp_path / "zenodo.json"
    zpath.write_text(json.dumps(zen), encoding="utf-8")
    # Embassy stamp = Clarity hour-ending label instant + 1 h
    label = base + pd.Timedelta(hours=1)
    stamp = (pd.DatetimeIndex(label).tz_localize("Asia/Bishkek") + pd.Timedelta(hours=1)).tz_convert("UTC")
    emb = _embassy(stamp, sig)
    ep = tmp_path / "embassy" / "embassy_pm25_hourly.parquet"
    ep.parent.mkdir()
    emb.to_parquet(ep, index=False)
    em = tmp_path / "embassy_processing.json"
    mf.write_manifest_atomic(em, {"outputs": [{"path": "data/processed/embassy/embassy_pm25_hourly.parquet", "sha256": mf.hash_file(ep, ("sha256",))["sha256"]}]})
    return type("E", (), dict(wb=wb, zpath=zpath, ep=ep, em=em, tmp=tmp_path, zen=zen))


def run(env, **kw):
    msgs = []
    kw.setdefault("embassy_hourly", env.ep)
    kw.setdefault("embassy_manifest", env.em)
    code = pc.run(zenodo_manifest=env.zpath, interim_dir=env.tmp / "interim", processed_dir=env.tmp / "processed",
                  manifest_path=env.tmp / "clarity_processing.json", out=msgs.append, **kw)
    return code, msgs


def test_end_to_end_outputs_manifest_and_inventory(env):
    wb_before, zen_before, emb_before = env.wb.read_bytes(), env.zpath.read_bytes(), env.ep.read_bytes()
    code, _ = run(env)
    assert code == 0
    assert (env.wb.read_bytes(), env.zpath.read_bytes(), env.ep.read_bytes()) == (wb_before, zen_before, emb_before)   # inputs untouched
    p, i = env.tmp / "processed", env.tmp / "interim"
    hourly = pd.read_parquet(p / "clarity_pm25_hourly.parquet")
    assert len(hourly) == 8 * 72 and hourly["device_id"].nunique() == 8 and list(hourly.columns) == cc.HOURLY_COLUMNS
    assert (p / "clarity_pm25_hourly.csv").exists() and (p / "clarity_daily.parquet").exists() and (p / "clarity_monthly.parquet").exists()
    inv = pd.read_csv(p / "clarity_device_inventory.csv")
    assert inv.set_index("device_id")["status"].to_dict()["NA1"] == "no_data_in_file" and len(inv) == 10
    assert bool(inv.set_index("device_id").loc["NA1", "flag_shared_coordinates"])
    audit = pd.read_parquet(i / "cleaned" / "clarity_audit_rows.parquet")
    assert (audit["row_kind"] == "blank_row").sum() == 4 and (audit["row_kind"] == "record").sum() == 8 * 72
    for f in ("clarity_validation_report.md", "clarity_validation_stats.json", "clarity_timestamp_report.md", "clarity_device_coverage_report.md",
              "clarity_spatial_report.md", "clarity_embassy_comparison.md"):
        assert (i / "reports" / f).exists(), f
    emb_md = (i / "reports" / "clarity_embassy_comparison.md").read_text(encoding="utf-8")
    assert "APPROXIMATE" in emb_md and "Neither k = 0 nor k = +1 is declared correct" in emb_md
    ts_md = (i / "reports" / "clarity_timestamp_report.md").read_text(encoding="utf-8")
    assert "No reading was shifted" in ts_md and "open question" in ts_md
    main_md = (i / "reports" / "clarity_validation_report.md").read_text(encoding="utf-8")
    assert "NOT independently verified" in main_md and "no_data_in_file" in main_md and "blank rows" in main_md

    m = json.loads((env.tmp / "clarity_processing.json").read_text(encoding="utf-8"))
    assert m["source"]["filename"] == pc.WORKBOOK_NAME and m["source"]["zenodo_record_id"] == 1
    assert m["source"]["sha256"] == mf.hash_file(env.wb, ("sha256",))["sha256"] and m["source"]["opened_read_only"] is True
    assert m["workbook"]["sheet_names"] == ["2021", "locations", "monthly"] and m["workbook"]["data_sheet_rows"]["blank_rows"] == 4
    assert m["timestamp_assumptions"]["hour_convention"] == "hour_ending" and m["timestamp_assumptions"]["status"].startswith("ASSUMPTION")
    assert m["cleaning_rules"] and m["quality_flags"]["VALID"] and m["known_limitations"]
    assert m["licence"]["record_level_licence"] == {"id": "cc-by-4.0"} and m["licence"]["attribution_required"] is True
    assert {d["device_id"]: d["status"] for d in m["device_inventory"]}["D1"] == "has_data_in_file"
    assert all(len(o["sha256"]) == 64 and o["size_bytes"] > 0 for o in m["outputs"])
    out = {o["path"].rsplit("/", 1)[-1]: o for o in m["outputs"]}
    assert out["clarity_pm25_hourly.parquet"]["sha256"] == mf.hash_file(p / "clarity_pm25_hourly.parquet", ("sha256",))["sha256"]
    assert {e["filename"] for e in m["extraction"]["files"]} >= {"sheet_2021.csv", "sheet_locations.csv"}


def test_end_to_end_embassy_alignment_is_reported_not_applied(env):
    run(env)
    st = json.loads((env.tmp / "interim" / "reports" / "clarity_validation_stats.json").read_text(encoding="utf-8"))
    comps = [c for c in st["embassy_comparison"]["comparisons"] if c["device_id"] == "D1" and c["period"] == "all_year"]
    r = {c["shift_k_hours"]: c["all_valid"]["pearson_r"] for c in comps}
    assert r[1] > 0.99 > r[0]                                    # data built with a +1 h offset; both alignments are shown
    h = pd.read_parquet(env.tmp / "processed" / "clarity_pm25_hourly.parquet")
    d1 = h[h["device_id"] == "D1"].sort_values("ts_start_utc")
    assert str(d1["ts_start_local"].iloc[0]) == "2021-03-01 00:00:00+06:00"         # first reading not moved by the comparison


def test_end_to_end_is_reproducible_and_never_overwrites_a_changed_output(env):
    run(env)
    code, msgs = run(env)
    assert code == 0 and all("[unchanged]" in m for m in msgs if m.strip().endswith("]") and "/" in m and "manifest" not in m.lower())
    with pytest.raises(pc.OutputExistsError, match="allow-overwrite"):
        run(env, cfg=cc.ClarityConfig(hour_convention="hour_start"))
    code, _ = run(env, cfg=cc.ClarityConfig(hour_convention="hour_start"), allow_overwrite=True)
    assert code == 0
    h = pd.read_parquet(env.tmp / "processed" / "clarity_pm25_hourly.parquet")
    assert str(h.sort_values("ts_start_utc")["ts_start_local"].iloc[0]) == "2021-03-01 01:00:00+06:00"   # explicit config change took effect


def test_end_to_end_refuses_tampered_workbook_and_unverified_entry(env):
    env.wb.write_bytes(env.wb.read_bytes() + b"x")
    with pytest.raises(Exception, match="differs from the Zenodo manifest"):
        run(env)
    assert not (env.tmp / "processed").exists()
    z = json.loads(env.zpath.read_text(encoding="utf-8"))
    z["files"][0]["download_status"] = "failed"
    env.zpath.write_text(json.dumps(z), encoding="utf-8")
    code, msgs = run(env)
    assert code == 2 and any("not a verified download" in m for m in msgs)
    env.zpath.unlink()
    assert run(env)[0] == 2


def test_skip_embassy_still_completes(env):
    code, _ = run(env, skip_embassy=True)
    assert code == 0 and not (env.tmp / "interim" / "reports" / "clarity_embassy_comparison.md").exists()
