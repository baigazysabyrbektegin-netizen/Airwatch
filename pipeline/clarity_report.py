"""Statistics and human-readable reports for the Clarity 2021 data (Stage 3)."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from pipeline import clarity_clean as cc
from pipeline.embassy_report import _table

CALIBRATION_STATEMENT = (
    "Calibration: the source paper reports that the low-cost sensors had \"periodic calibrations\". We found no calibration "
    "method, schedule or per-device calibration record in the workbook or in the paper text we could read. Calibration is therefore "
    "NOT independently verified here: the readings are used as supplied, sensors are not assumed to be equally calibrated, and the "
    "single Embassy comparison below is not a calibration."
)


def _r(x, n=1):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), n)


def _table_rows(ref: str) -> int | None:
    m = re.match(r"^[A-Z]+(\d+):[A-Z]+(\d+)$", ref or "")
    return int(m.group(2)) - int(m.group(1)) if m else None  # excludes the header row


def compute_stats(audit, hourly, daily, monthly, coverage, inventory, wb, cfg) -> dict:
    st: dict = {"processing_version": cc.PROCESSING_VERSION, "config": cfg.as_dict(), "timestamp_assumptions": cfg.timestamp_assumptions()}
    rec = audit[audit["row_kind"] == "record"]
    blank = audit[audit["row_kind"] == "blank_row"]
    tbl = next((t for t in wb["tables"] if t["name"] == "Table1"), None)
    st["workbook"] = {
        "sheets": wb["inventory"], "tables": wb["tables"], "defined_names": wb["defined_names"],
        "doc_properties": wb["doc_properties"], "data_sheet_formulas": wb["data_sheet_formulas"],
        "locations_formulas": wb["locations_formulas"],
    }
    st["rows"] = {
        "data_sheet_rows_excluding_header": int(len(audit)),
        "blank_rows": int(len(blank)),
        "blank_rows_first_excel_row": int(blank["excel_row"].min()) if len(blank) else None,
        "blank_rows_last_excel_row": int(blank["excel_row"].max()) if len(blank) else None,
        "records": int(len(rec)),
        "table1_declared_ref": tbl["ref"] if tbl else None,
        "table1_declared_data_rows": _table_rows(tbl["ref"]) if tbl else None,
        "quality_flag_counts": {k: int(v) for k, v in audit["quality_flag"].value_counts().items()},
        "valid_readings": int(audit["is_primary_valid"].sum()),
        "invalid_or_excluded_records": int(len(rec) - audit["is_primary_valid"].sum()),
        "records_with_empty_hour": int(rec["src_hour"].isna().sum()),
        "records_with_empty_pm25": int(rec["flag_pm25_missing"].sum()),
        "time_issue_counts": {k: int(v) for k, v in rec["time_issue"].value_counts().items() if k},
        "duplicate_device_hour_rows": int(audit["flag_duplicate_device_hour"].sum()),
        "duplicate_conflict_rows": int(audit["flag_duplicate_conflict"].sum()),
        "unknown_device_records": int(audit["flag_unknown_device"].sum()),
    }
    lab = hourly["hour_label"].value_counts().sort_index()
    st["hour_labels"] = {"counts": {int(k): int(v) for k, v in lab.items()}, "min": int(lab.index.min()), "max": int(lab.index.max()),
                         "hour_24_records": int(lab.get(24, 0)), "hour_0_records": int(lab.get(0, 0))}
    st["time_range"] = {"first_interval_start_local": str(hourly["ts_start_local"].min()), "last_interval_start_local": str(hourly["ts_start_local"].max()),
                        "first_interval_start_utc": str(hourly["ts_start_utc"].min()), "last_interval_start_utc": str(hourly["ts_start_utc"].max())}

    v = hourly["pm25_ugm3"]
    src_min = float(v.min())
    st["pm25"] = {
        "n": int(len(v)), "min": _r(v.min(), 2), "p1": _r(v.quantile(.01), 2), "median": _r(v.median(), 2), "mean": _r(v.mean(), 2),
        "p95": _r(v.quantile(.95), 2), "p99": _r(v.quantile(.99), 2), "max": _r(v.max(), 2),
        "n_within_0_05_of_minimum": int((v <= src_min + 0.05).sum()),
        "share_below_5_pct": _r(100 * (v < 5).mean(), 2),
        "n_nonpositive": int((v <= 0).sum()),
        "note": (f"No reading is below {src_min:.2f} µg/m³ and the counts start abruptly there ({int((v <= src_min + 0.05).sum())} readings within 0.05 of the minimum, "
                 "instead of tapering). That looks like a floor or left-censoring at about 4 µg/m³ (cause unknown), so clean-air statistics from this data are biased high."),
    }
    hi = hourly[hourly["flag_high_value"]]
    no = hourly[hourly["flag_neighbor_outlier"]]
    st["high_values"] = {
        "threshold_ugm3": cfg.high_value_ugm3, "n_above_threshold": int(len(hi)), "n_above_1000": int((v > 1000).sum()),
        "n_above_threshold_also_neighbor_outlier": int((hourly["flag_high_value"] & hourly["flag_neighbor_outlier"]).sum()),
        "n_above_threshold_with_neighbor_check": int((hi["n_neighbors"] >= cfg.neighbor_min_others).sum()),
        "n_above_threshold_not_neighbor_outlier": int((hi["n_neighbors"] >= cfg.neighbor_min_others).sum() - (hi["flag_neighbor_outlier"]).sum()),
        "neighbor_rule": f"reading > {cfg.neighbor_ratio} x median of the other sensors in the same interval, judged only when >= {cfg.neighbor_min_others} other sensors report",
        "n_neighbor_outlier_candidates": int(len(no)),
        "neighbor_outliers_by_size": {">=500": int((no["pm25_ugm3"] > 500).sum()), "100-500": int(no["pm25_ugm3"].between(100, 500).sum()),
                                      "50-100": int(no["pm25_ugm3"].between(50, 100, inclusive="left").sum()), "<50": int((no["pm25_ugm3"] < 50).sum())},
        "devices_with_most_high_values": {k: int(n) for k, n in hi["device_id"].value_counts().head(8).items()},
        "high_values_by_month": {k: int(n) for k, n in hi.groupby(hi["ts_start_local"].dt.strftime("%Y-%m")).size().items()},
        "top10_readings": [
            {"device_id": r.device_id, "start_local": str(r.ts_start_local), "pm25": _r(r.pm25_ugm3, 1), "n_neighbors": int(r.n_neighbors),
             "neighbor_median": _r(r.neighbor_median_ugm3, 1), "ratio": _r(r.neighbor_ratio, 1)}
            for r in hourly.nlargest(10, "pm25_ugm3").itertuples()
        ],
    }

    st["inventory"] = {
        "n_listed": int(len(inventory)), "n_with_data": int((inventory["status"] == "has_data_in_file").sum()),
        "n_no_data_in_file": int((inventory["status"] == "no_data_in_file").sum()),
        "n_duplicate_device_rows": int(inventory["flag_duplicate_device_row"].sum()),
        "n_invalid_coordinates": int((~inventory["flag_coordinates_valid"]).sum()),
        "n_outside_study_domain": int((~inventory["flag_in_study_domain"]).sum()),
        "shared_coordinate_groups": [
            {"coordinates": [g["latitude_src"].iloc[0], g["longitude_src"].iloc[0]], "devices": list(g["device_id"]),
             "devices_with_data": list(g.loc[g["status"] == "has_data_in_file", "device_id"])}
            for _, g in inventory[inventory["flag_shared_coordinates"]].groupby("shared_coordinate_group")
        ],
        "quadrant_rule_matches_workbook_lookup": {str(k): int(n) for k, n in inventory["quadrant_consistent"].value_counts(dropna=False).items()},
        "data_devices_missing_from_locations": int(audit["flag_unknown_device"].sum()),
        "lat_range": [float(inventory["latitude"].min()), float(inventory["latitude"].max())],
        "lon_range": [float(inventory["longitude"].min()), float(inventory["longitude"].max())],
    }

    c = coverage
    st["coverage"] = {
        "n_devices": int(len(c)), "expected_hours_network_window": int(c["expected_hours_network_window"].iloc[0]),
        "coverage_pct_network_window": {"min": _r(c["coverage_pct_network_window"].min()), "median": _r(c["coverage_pct_network_window"].median()),
                                         "max": _r(c["coverage_pct_network_window"].max())},
        "low_coverage_threshold_pct": cfg.low_coverage_pct, "n_low_coverage": int(c["flag_low_coverage"].sum()),
        "devices_low_coverage": list(c.loc[c["flag_low_coverage"], "device_id"]),
        "lowest_5": [{"device_id": r.device_id, "coverage_pct": _r(r.coverage_pct_network_window, 1), "missing_hours": int(r.missing_hours_network_window)}
                     for r in c.nsmallest(5, "coverage_pct_network_window").itertuples()],
        "longest_gaps_top5": [{"device_id": r.device_id, "hours": int(r.longest_gap_hours)} for r in c.nlargest(5, "longest_gap_hours").itertuples()],
        "drift_candidates": list(c.loc[c["flag_drift_candidate"], "device_id"]), "n_drift_candidates": int(c["flag_drift_candidate"].sum()),
        "drift_rule": f">= {cfg.drift_min_months} months, |Spearman rho vs month| >= {cfg.drift_min_abs_rho}, max/min ratio-to-network >= {cfg.drift_min_range_ratio}",
        "drift_range_factor_top5": [{"device_id": r.device_id, "range_factor": _r(r.ratio_range_factor, 2), "rho": _r(r.spearman_rho_vs_month, 2)}
                                    for r in c.nlargest(5, "ratio_range_factor").itertuples()],
        "records_per_device": {"min": int(c["total_records"].min()), "median": int(c["total_records"].median()), "max": int(c["total_records"].max())},
    }

    st["daily"] = {
        "n_device_days": int(len(daily)), "n_meeting_min_hours": int(daily["meets_min_hours"].sum()),
        "min_valid_hours": cfg.daily_min_valid_hours, "share_meeting_pct": _r(100 * daily["meets_min_hours"].mean()),
        "hours_in_days_below_threshold": int(daily.loc[~daily["meets_min_hours"], "n_valid_hours"].sum()),
        "valid_hours_per_day_distribution": {int(k): int(n) for k, n in daily["n_valid_hours"].value_counts().sort_index().items()},
    }
    mm = monthly
    st["monthly"] = {
        "n_device_months": int(len(mm)), "n_meeting_threshold": int(mm["meets_threshold"].sum()), "min_day_fraction": cfg.monthly_min_day_fraction,
        "by_month": {k: {"devices_with_mean": int(g["meets_threshold"].sum()), "devices_listed": int(len(g)),
                         "network_median_of_monthly_means": _r(g["pm25_monthly_mean_ugm3"].median()), "eligible_days": int(g["eligible_days"].iloc[0])}
                     for k, g in mm.groupby("year_month")},
    }
    return st


# ----------------------------------------------------------------------------- renderers
def render_main(st: dict, limitations: list[str], decisions: list[str], other_reports: list[str]) -> str:
    w, r, ta = st["workbook"], st["rows"], st["timestamp_assumptions"]
    L = ["# Clarity 2021 sensor data: validation report\n",
         f"Processing version `{st['processing_version']}`. Source: Zenodo record 12720883, `bishkek_monitoring_claritysensor_2021.xlsx` "
         "(CC BY 4.0 at record level; see limitations). PM2.5 in µg/m³ as supplied (not calibrated or corrected).\n",
         "Companion reports: " + ", ".join(f"`{x}`" for x in other_reports) + ".\n"]
    L.append("## 1. Workbook structure\n")
    L.append(_table(["sheet", "state", "dimension", "rows in file", "non-empty cells", "formula cells"],
                    [[s["sheet"], s["state"], s["dimension"], f"{s['rows_in_file']:,}", f"{s['non_empty_cells']:,}", f"{s['formula_cells']:,}"] for s in w["sheets"]]))
    L.append(f"\nData lives in an Excel table `Table1` with declared range **{r['table1_declared_ref']}** "
             f"({r['table1_declared_data_rows']:,} data rows). Document properties: "
             + ", ".join(f"{k.split(':')[1]} = {v}" for k, v in w["doc_properties"].items() if k.startswith("core")) + ". "
             f"No defined names ({w['defined_names']}); no cell comments or notes were found, so the workbook carries **no documentation of the timestamp convention or time zone**.\n")
    L.append(f"The `Quadrant` column of the data sheet is a **spreadsheet formula**, not a source field: `{w['data_sheet_formulas'].get('G')}`. "
             "The `locations` sheet derives quadrants from coordinates with fixed thresholds "
             f"(latitude {st['config']['quadrant_lat']}, longitude {st['config']['quadrant_lon']}); the lookup covers only the devices that have readings.\n")

    L.append("## 2. Row counts (corrects the earlier exploratory summary)\n")
    L.append(_table(["measure", "value"], [
        ["data-sheet rows below the header", f"{r['data_sheet_rows_excluding_header']:,}"],
        ["of which entirely blank rows (no device, no value)", f"{r['blank_rows']:,} (Excel rows {r['blank_rows_first_excel_row']:,}–{r['blank_rows_last_excel_row']:,}, below the table range)"],
        ["**measurement records**", f"**{r['records']:,}**"],
        ["records with an empty hour", r["records_with_empty_hour"]], ["records with an empty PM2.5", r["records_with_empty_pm25"]],
        ["records with an invalid timestamp component", sum(r["time_issue_counts"].values())],
        ["duplicate (device, interval) rows", r["duplicate_device_hour_rows"]], ["records for a device absent from `locations`", r["unknown_device_records"]],
        ["valid readings (primary analysis table)", f"{r['valid_readings']:,}"], ["invalid or excluded records", f"{r['invalid_or_excluded_records']:,}"]]))
    L.append("\nThe earlier figures of 242,142 \"measurement rows\" and 10,826 \"records with an empty hour\" were wrong: the 10,826 rows are blank "
             "rows outside the data table, so they were never measurements. Every real record has a complete year, month, day and hour.\n")

    L.append("## 3. Timestamps (summary; details in the timestamp report)\n")
    h = st["hour_labels"]
    L.append(f"Hour labels run {h['min']}–{h['max']}; label 24 occurs {h['hour_24_records']:,} times, label 0 never. "
             f"Provisional interpretation: **{ta['hour_convention']}**, time zone **{ta['source_timezone']}**, extra shift **{ta['extra_shift_hours']} h**. "
             f"Status: *{ta['status']}*. Mapping: {ta['mapping']}. Interval starts range "
             f"{st['time_range']['first_interval_start_local']} to {st['time_range']['last_interval_start_local']} (local).\n")

    p = st["pm25"]
    L.append("## 4. PM2.5 values\n")
    L.append(_table(["n", "min", "p1", "median", "mean", "p95", "p99", "max"], [[f"{p['n']:,}", p["min"], p["p1"], p["median"], p["mean"], p["p95"], p["p99"], p["max"]]]))
    L.append(f"\n{p['note']}\n")

    hv = st["high_values"]
    L.append("## 5. High and suspicious readings\n")
    L.append(f"- Readings above {hv['threshold_ugm3']:.0f} µg/m³: **{hv['n_above_threshold']}** (above 1000: {hv['n_above_1000']}), all in Oct–Dec. Flagged `flag_high_value`, kept.\n"
             f"- Neighbour rule: {hv['neighbor_rule']}. Candidates: **{hv['n_neighbor_outlier_candidates']:,}** (by size: {hv['neighbor_outliers_by_size']}). "
             "A candidate is a reading well above the other sensors at that moment; it can be a local source close to that sensor or a sensor problem, and this check cannot tell which.\n"
             f"- Of the {hv['n_above_threshold']} readings above the threshold, {hv['n_above_threshold_with_neighbor_check']} could be compared with enough neighbours; "
             f"{hv['n_above_threshold_also_neighbor_outlier']} of them are also neighbour-outlier candidates, while "
             f"{hv['n_above_threshold_not_neighbor_outlier']} are not (the other sensors were also high at the time). Not being flagged is not proof that a reading is correct.\n"
             f"- Devices with most high values: {hv['devices_with_most_high_values']}. By month: {hv['high_values_by_month']}.\n"
             "- Nothing was deleted; every original value is preserved in the audit dataset.\n")
    L.append(_table(["device", "start (local)", "PM2.5", "other sensors", "their median", "ratio"],
                    [[x["device_id"], x["start_local"], x["pm25"], x["n_neighbors"], x["neighbor_median"], x["ratio"]] for x in hv["top10_readings"]]))

    iv = st["inventory"]
    L.append("\n## 6. Devices and locations\n")
    L.append(f"{iv['n_listed']} devices are listed with coordinates; **{iv['n_with_data']}** have readings, **{iv['n_no_data_in_file']}** have none "
             f"(status `no_data_in_file`; kept in the inventory, not treated as active sensors). Duplicate device rows: {iv['n_duplicate_device_rows']}. "
             f"Invalid coordinates: {iv['n_invalid_coordinates']}; outside the study domain: {iv['n_outside_study_domain']}. "
             f"Latitude {iv['lat_range'][0]:.4f}–{iv['lat_range'][1]:.4f}, longitude {iv['lon_range'][0]:.4f}–{iv['lon_range'][1]:.4f}. "
             f"Devices in the data but missing from `locations`: {iv['data_devices_missing_from_locations']}.\n")
    for g in iv["shared_coordinate_groups"]:
        L.append(f"- **{len(g['devices'])} devices share the identical coordinate pair {tuple(g['coordinates'])}**: {', '.join(g['devices'])}. "
                 f"Only {g['devices_with_data']} has readings. Identical coordinates for several devices may mean placeholder, default or shared positions; the cause is unknown. "
                 "Coordinates were NOT altered or inferred, and the no-data devices are not treated as located sensors.\n")

    c = st["coverage"]
    L.append("## 7. Device coverage\n")
    L.append(f"Expected hours (first to last interval start in the whole file): {c['expected_hours_network_window']:,}. Coverage per device: "
             f"min {c['coverage_pct_network_window']['min']} %, median {c['coverage_pct_network_window']['median']} %, max {c['coverage_pct_network_window']['max']} % "
             f"(earlier exploratory figures of 85–94 % omitted the hour-24 readings). Devices below {c['low_coverage_threshold_pct']} %: {c['n_low_coverage']}. "
             f"Lowest: {c['lowest_5']}. Longest gaps (hours): {c['longest_gaps_top5']}. Full table: `clarity_device_coverage.csv`.\n")
    L.append(f"Monthly drift check (ratio of a device's monthly mean to the network median; rule: {c['drift_rule']}): **{c['n_drift_candidates']}** candidates. "
             f"Largest ratio ranges: {c['drift_range_factor_top5']}. Because winter spatial spread is larger than summer spread, ratios change with season for real reasons; "
             "this check is exploratory and is not evidence of sensor faults or of calibration.\n")

    d, m = st["daily"], st["monthly"]
    L.append("## 8. Daily and monthly aggregates\n")
    L.append(f"A day is the 24 intervals starting on a local calendar date. A daily mean needs at least {d['min_valid_hours']} valid hours: "
             f"**{d['n_meeting_min_hours']:,} of {d['n_device_days']:,} device-days** ({d['share_meeting_pct']} %) qualify; the remaining days contain "
             f"{d['hours_in_days_below_threshold']:,} valid hours that are excluded from daily means. A monthly mean (mean of valid daily means) needs at least "
             f"{m['min_day_fraction']:.0%} of the eligible days (calendar days of that month inside the file's date range): **{m['n_meeting_threshold']} of {m['n_device_months']}** "
             "device-months qualify. February has a single partial eligible day (28 Feb, starting at 01:00) and therefore yields no monthly means. "
             "The timestamp convention changes which hours fall on which day (hour-label 24 moves to the next day under `hour_start`) and so can change which days pass the threshold; see the sensitivity table in the timestamp report.\n")
    L.append(_table(["month", "devices with mean", "devices listed", "eligible days", "network median of monthly means"],
                    [[k, x["devices_with_mean"], x["devices_listed"], x["eligible_days"], x["network_median_of_monthly_means"]] for k, x in m["by_month"].items()]))

    L.append("\n## 9. Spatial and Embassy results\nSee `clarity_spatial_report.md` and `clarity_embassy_comparison.md`. " + (st.get("spatial_headline", "") or "") + "\n")
    L.append("## 10. Calibration and reliability\n" + CALIBRATION_STATEMENT + "\n")
    L.append("## 11. Cleaning decisions\n")
    L += [f"- {x}" for x in decisions]
    L.append("\n## 12. Limitations\n")
    L += [f"- {x}" for x in limitations]
    return "\n".join(L) + "\n"


def render_timestamp(st: dict, sens: list[dict], diurnal: dict | None) -> str:
    h, r, ta = st["hour_labels"], st["rows"], st["timestamp_assumptions"]
    L = ["# Clarity timestamp issue report\n"]
    L.append("## What the file contains\n")
    L.append(f"- Fields: `year`, `month`, `day`, `hour` (plain integers; no time zone, no minute, no timestamp column).\n"
             f"- Hour labels: {h['min']}–{h['max']}. Counts per label: {h['counts']}. Label 0 never occurs ({h['hour_0_records']}); label 24 occurs **{h['hour_24_records']:,}** times "
             "(about 1/24 of all records, i.e. a normal share, not an anomaly).\n"
             f"- Records with a missing hour: **{r['records_with_empty_hour']}**; with any invalid component: **{sum(r['time_issue_counts'].values())}** "
             f"({r['time_issue_counts'] or 'none'}). The 10,826 blank rows below the table have no components at all and are not records.\n"
             f"- Duplicate (device, interval) pairs: {r['duplicate_device_hour_rows']}.\n")
    L.append("## Evidence about the convention (nothing is conclusive)\n")
    L.append("- The workbook has no note, comment, defined name or documentation of the convention or time zone.\n"
             "- Its own summary sheets label hours `H01`–`H24`, matching the 1–24 labels of the data sheet. That points to a 1–24 numbering chosen by the author.\n"
             "- Clarity's help pages (help.clarity.io) say dashboard CSV downloads and the API are in **UTC**, and that a native hourly mean is labelled by the START of its UTC hour "
             "(for example 04:00 UTC covers 04:00:00–04:59:59). Those pages do not use 1–24 labels. So the workbook was evidently recoded from Clarity's native 0–23 UTC form, "
             "and the recoding rule is unknown. (The API v2 page describing period fields could not be loaded.)\n")
    if diurnal:
        L.append(f"- Diurnal timing ({diurnal['months']}, 2021): Clarity network mean peaks at label **{diurnal['clarity_peak_hour_label']}**; the Embassy mean peaks at stamp hour "
                 f"**{diurnal['embassy_peak_stamp_hour']}**. If the Clarity labels were UTC the local peak would be {diurnal['local_hour_if_clarity_labels_were_utc']}:00. "
                 f"{diurnal['reading']}\n")
    L.append("## Provisional setting (configurable, not established)\n")
    L.append(f"`hour_convention = {ta['hour_convention']}`, `source_timezone = {ta['source_timezone']}`, `extra_shift_hours = {ta['extra_shift_hours']}`.\n\n"
             f"{ta['mapping']}. Hour 24 therefore maps to 00:00 of the following day as the label instant. Local time is always shown as Asia/Bishkek (UTC+6) and UTC is stored too. "
             "Every output records the setting; the Parquet files carry it in their metadata. **No reading was shifted to line up with the Embassy series.**\n")
    L.append("## Effect of alternative interpretations on aggregates\n")
    L.append(_table(["interpretation", "first interval start (local)", "device-days with ≥18 valid h", "device-months passing", "intervals that change local date vs default"],
                    [[s["label"], s["first_start_local"], f"{s['days_meeting']:,} of {s['days_total']:,}", s["months_meeting"], s["date_changed_vs_default"]] for s in sens]))
    L.append("\nThe +1 h alignment with the Embassy data is an open question (see the Embassy comparison); it is not applied.\n")
    return "\n".join(L) + "\n"


def render_devices(cov: pd.DataFrame, inv: pd.DataFrame) -> str:
    L = ["# Clarity device coverage report\n",
         "Coverage = valid readings / hours between the first and last interval start in the whole file. Own-span coverage uses each device's own first and last reading. "
         "Missing hours are counted against the network window.\n"]
    t = cov[["device_id", "valid_readings", "coverage_pct_network_window", "missing_hours_network_window", "longest_gap_hours", "pm25_mean", "pm25_median",
             "n_high_value", "n_neighbor_outlier", "ratio_range_factor", "flag_low_coverage", "flag_drift_candidate"]].sort_values("coverage_pct_network_window")
    L.append(_table(list(t.columns), [[(_r(v, 1) if isinstance(v, float) else v) for v in row] for row in t.itertuples(index=False)]))
    nd = inv[inv["status"] == "no_data_in_file"]
    L.append(f"\n## Devices without readings in this file ({len(nd)})\n")
    L.append("Status `no_data_in_file`: kept in the inventory with their original coordinates, not treated as active sensors.\n")
    L.append(_table(["device", "latitude", "longitude", "shares coordinates with another device"],
                    [[x.device_id, x.latitude_src, x.longitude_src, bool(x.flag_shared_coordinates)] for x in nd.itertuples()]))
    return "\n".join(L) + "\n"


def render_spatial(sp: dict, hull_note: str) -> str:
    L = ["# Clarity spatial analysis (exploratory)\n", "```", sp["method"].strip(), "```\n"]
    g = sp["geometry"]
    rd = lambda dct: {k: round(v, 2) for k, v in dct.items()}  # noqa: E731
    for name in ("with_data", "all_listed"):
        x = g[name]
        L.append(f"## Sensor distribution: {name.replace('_', ' ')}\n")
        L.append(f"{x['n']} sensors; nearest-neighbour distance km {rd(x['nearest_neighbour_km'])}; pair distance km {rd(x['pair_distance_km'])}; "
                 f"convex-hull area {x['convex_hull_area_km2']:.0f} km², density {x['devices_per_km2_in_hull']:.3f} per km²; quadrants (workbook rule) {x['quadrant_counts_workbook_rule']}; "
                 f"sensors sharing an identical coordinate pair: {x['n_sharing_identical_coordinates']}.\n")
    L.append(f"The hull spans the built-up area only. {hull_note}\n")
    for name, p in sp["periods"].items():
        L.append(f"## Period `{name}` (months {p['months']})\n")
        if p.get("insufficient_data"):
            L.append(f"{p['note']} ({p['n_sensors']} sensors qualify).\n")
            continue
        L.append(f"{p['n_sensors']} sensors, {p['n_pairs']} pairs. Between-sensor spread of mean ln(reading/city median): SD {p['sd_log_ratio']:.3f} "
                 f"(≈ {p['implied_between_sensor_spread_pct']:.0f} %). Device mean PM2.5 range {p['mean_pm25_range'][0]:.1f}–{p['mean_pm25_range'][1]:.1f} µg/m³ "
                 f"(CV {p['cv_of_device_mean_pm25']:.2f}). Variance (sill reference) {p['variance_log_ratio_sill_ref']:.4f}; mean semivariance at ≥ 9 km {p['plateau_semivariance_ge_9km']:.4f}.\n")
        L.append(_table(["bin km", "pairs", "mean distance km", "semivariance"], [[b["bin_km"], b["n_pairs"], round(b["mean_distance_km"], 2), round(b["semivariance"], 4)] for b in p["semivariogram"]]))
        e, m = p["exponential_fit"], p["mantel"]
        d = lambda x: "not reached" if x is None else f"{x:.1f} km"  # noqa: E731
        L.append(f"\nSemivariance reaches 25 % / 50 % / 90 % of the sill at about {d(p['distance_km_at_25pct_of_sill'])} / {d(p['distance_km_at_50pct_of_sill'])} / {d(p['distance_km_at_90pct_of_sill'])}. "
                 f"Exponential fit: nugget {e['nugget']:.3f}, partial sill {e['partial_sill']:.3f}, range parameter {e['range_param_km']} km (practical range ≈ {e['practical_range_km_3r']} km; {e['note']}). "
                 f"Mantel-type test of |difference| against distance: Pearson r {m['pearson_r']:.2f}, Spearman ρ {m['spearman_rho']:.2f}, one-sided permutation p = {m['p_one_sided']:.3f} ({m['n_perm']} permutations, seed {m['seed']}).\n")
    s = sp["winter_vs_summer_sd_ratio"]
    L.append("## Winter versus summer\n")
    if "estimate" not in s:
        L.append("Not enough sensors present in both periods.\n")
    else:
        L.append(f"The between-sensor SD of relative level is {s['estimate']:.1f}× larger in Nov–Dec than in Jun–Aug (95 % bootstrap interval {s['ci95'][0]:.1f}–{s['ci95'][1]:.1f}, "
                 f"resampling {s['n_sensors']} sensors). \"Winter\" here means Nov–Dec only: the file has no January or February data.\n")
    L.append("## How to read this\n"
             "- No two sensors are closer than about 1.1 km, so nothing is known about shorter distances; where a fraction of the sill is already reached in the first bin, the distance reported is that bin's mean distance (about 1.6 km), not a true minimum.\n"
             "- The distances at which semivariance rises describe how similar sensors in THIS year's data are; they are not a universal representativeness radius.\n"
             "- One year, 33 sensors inside the built-up area, uncertain calibration and a single heating-season window limit what can be concluded; other sites, seasons or years may differ.\n"
             "- No citywide prediction is made and no cause (heating, traffic, topography) is attributed.\n"
             "- Binning, the minimum-hours rule and the use of the city median are choices; different choices move the numbers.\n")
    return "\n".join(L) + "\n"


def render_embassy(res: dict, diurnal: dict | None) -> str:
    L = ["# Clarity vs US Embassy: exploratory comparison\n"]
    loc = res["embassy_location"]
    L.append(f"**Embassy location is {loc['status']}**: ({loc['latitude']}, {loc['longitude']}), {loc['source']}. Distances below are to this point, not to the monitor itself.\n")
    L.append("Alignment `k` = hours added to the Clarity label instant before pairing it with the Embassy timestamp as given. "
             "Neither k = 0 nor k = +1 is declared correct; the Embassy file's own hour convention is also undocumented. Clarity is regressed on Embassy "
             "(Clarity = slope × Embassy + intercept). Samples are hourly and strongly autocorrelated, so intervals from the day-block bootstrap are more honest than naive ones.\n")
    L.append(_table(["device", "status", "distance km"], [[d["device_id"], d["status"], round(d["distance_to_embassy_approx_km"], 2)] for d in res["nearby_devices"]]))
    # which alignment gives the highest r, per device and period
    best_rows = []
    for dev in dict.fromkeys(c["device_id"] for c in res["comparisons"]):
        for per in ("all_year", "winter_nov_dec", "summer_jun_aug"):
            cand = [(c["shift_k_hours"], c["all_valid"].get("pearson_r")) for c in res["comparisons"]
                    if c["device_id"] == dev and c["period"] == per and c["all_valid"].get("pearson_r") is not None]
            if cand:
                k, rv = max(cand, key=lambda t: t[1])
                best_rows.append([dev, per, k, round(rv, 3), round(dict(cand).get(0, float("nan")), 3), round(dict(cand).get(1, float("nan")), 3)])
    L.append("\n## Which alignment gives the highest correlation?\n")
    L.append(_table(["device", "period", "k with highest r", "r there", "r at k = 0", "r at k = +1"], best_rows))
    L.append("\nThe maximum falls at the same k for every nearby sensor wherever there is a clear signal (all-year and Nov–Dec), so the offset looks systematic between the two files' "
             "time labels rather than a quirk of one sensor pair. Its cause (Clarity convention, Embassy convention, a time-zone difference, or a combination) is **not established** and no shift was applied. "
             "In summer (Jun–Aug) correlations are low at every k (concentrations vary little), so agreement in the all-year numbers is driven by the winter pollution episodes.\n")
    order = [d["device_id"] for d in res["nearby_devices"] if d["status"] == "has_data_in_file"]
    for dev in order:
        sub = [c for c in res["comparisons"] if c["device_id"] == dev]
        L.append(f"\n## {dev} (≈ {sub[0]['distance_km']:.2f} km from the approximate location)\n")
        rows = []
        for c in sub:
            a = c["all_valid"]
            ci = a.get("pearson_r_ci95_day_block_bootstrap")
            rows.append([c["period"], c["shift_k_hours"], a.get("n"), _r(a.get("pearson_r"), 3), _r(a.get("slope"), 2), _r(a.get("intercept"), 1),
                         _r(a.get("mean_difference_clarity_minus_embassy"), 1), f"{ci[0]:.3f}–{ci[1]:.3f}" if ci else "",
                         _r(c["excluding_flagged_values"].get("pearson_r"), 3), c["excluding_flagged_values"].get("n")])
        L.append(_table(["period", "k", "n pairs", "Pearson r", "slope", "intercept", "mean diff", "r 95 % (day blocks)", "r excl. flagged", "n excl."], rows))
    if diurnal:
        L.append(f"\n## Diurnal timing\nClarity network peak label {diurnal['clarity_peak_hour_label']}, Embassy peak stamp hour {diurnal['embassy_peak_stamp_hour']} "
                 f"(Nov–Dec 2021). {diurnal['reading']}\n")
    L.append("## Caveats\n"
             "- Only one Clarity sensor is within ~0.1 km of the approximate location; its \"co-location\" depends on that approximate point. Other devices are 1.7 km or more away and are shown only for context.\n"
             "- The Embassy monitor is reference-grade per the source materials; the Clarity sensors' calibration is not verified. A good correlation at one site does not show the other 32 sensors are calibrated, "
             "and this is not a calibration (no correction factors were derived or applied).\n"
             "- A higher r at one shift is evidence about relative timing of two series with undocumented conventions, not proof of which convention is right.\n"
             "- The Embassy series has its own documented quality issues (see its validation report); 2021 is one of the better-behaved years.\n")
    return "\n".join(L) + "\n"
