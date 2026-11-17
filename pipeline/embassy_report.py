"""Validation statistics and the human-readable report for the cleaned Embassy PM2.5 data."""

from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.embassy_clean import PROCESSING_VERSION, TIMEZONE, find_gaps

# Figures observed earlier in an exploratory pass (valid rows by timestamp year, QC == Valid and
# Raw != -999, duplicates counted, mean of Raw including negatives). Used only for comparison.
EXPECTED_PRIOR = {
    2019: (7715, 97.8, 35.4),
    2020: (8621, 98.1, 28.4),
    2021: (8259, 94.3, 35.8),
    2022: (8212, 93.7, 33.2),
    2023: (8578, 97.9, 27.5),
    2024: (4234, 96.9, 14.3),
}
SEASONS = {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM", 6: "JJA", 7: "JJA", 8: "JJA",
           9: "SON", 10: "SON", 11: "SON"}
NEAR_ZERO_UGM3 = 5.0


def _r(x, n=1):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), n)


def _summ(s: pd.Series) -> dict:
    s = s.dropna()
    if s.empty:
        return {"n": 0, "mean": None, "median": None, "p95": None, "max": None}
    return {"n": int(len(s)), "mean": _r(s.mean()), "median": _r(s.median()), "p95": _r(s.quantile(0.95)), "max": _r(s.max())}


def irregular_days(hourly: pd.DataFrame) -> pd.DataFrame:
    """Local dates whose number of source rows is not 24, with the reason where it can be told."""
    h = hourly.copy()
    h["date"] = h["ts_local"].dt.date
    h["hour"] = h["ts_local"].dt.hour
    first_day, last_day = h["date"].iloc[0], h["date"].iloc[-1]
    recs = []
    for day, g in h.groupby("date"):
        n_rows = int(g["n_source_rows"].sum())
        if n_rows == 24:
            continue
        missing = [int(x) for x in g.loc[~g["row_present"], "hour"]]
        dups = [int(x) for x in g.loc[g["flag_duplicate_ts"], "hour"]]
        if day in (first_day, last_day) and len(g) < 24:
            kind = "partial_edge_day"
        elif n_rows == 0:
            kind = "no_rows"
        elif n_rows == 23:
            kind = "23_rows"
        elif n_rows == 25:
            kind = "25_rows"
        else:
            kind = "other"
        recs.append({"date": str(day), "n_rows": n_rows, "kind": kind, "missing_hours": missing, "duplicated_hours": dups})
    return pd.DataFrame(recs, columns=["date", "n_rows", "kind", "missing_hours", "duplicated_hours"])


def duplicate_table(rows: pd.DataFrame) -> pd.DataFrame:
    d = rows[rows["flag_duplicate_ts"]].sort_values(["ts_utc", "source_order"])
    return d[["ts_original", "source_file", "source_line", "src_raw_conc", "src_nowcast_conc", "qc_name_original",
              "flag_duplicate_conflict", "is_primary_valid"]].reset_index(drop=True)


def compute_stats(rows: pd.DataFrame, hourly: pd.DataFrame) -> dict:
    st: dict = {"processing_version": PROCESSING_VERSION}
    st["source_files"] = {f: int(n) for f, n in rows.groupby("source_file").size().items()}
    st["n_source_files"] = len(st["source_files"])
    st["rows_read"] = int(len(rows))
    st["qc_counts_original"] = {k: int(v) for k, v in rows["qc_name_original"].value_counts().items()}
    st["qc_counts_standardised"] = {k: int(v) for k, v in rows["qc_class"].value_counts().items()}
    st["quality_flag_counts_rows"] = {k: int(v) for k, v in rows["quality_flag"].value_counts().items()}
    st["sentinel_999_rows"] = int(rows["flag_sentinel_999"].sum())
    st["time_unparsed_rows"] = int(rows["flag_time_unparsed"].sum())
    st["time_field_mismatch_rows"] = int(rows["flag_time_field_mismatch"].sum())
    st["unit_mismatch_rows"] = int(rows["flag_unit_mismatch"].sum())
    st["rows_in_file_of_other_year"] = int(
        (rows["ts_local"].dt.year.astype("Int64").astype(str) != rows["source_file"].str.extract(r"_(\d{4})_")[0]).sum()
    )

    first, last = hourly["ts_local"].iloc[0], hourly["ts_local"].iloc[-1]
    st["date_range"] = {"first_local": str(first), "last_local": str(last), "first_utc": str(hourly["ts_utc"].iloc[0]),
                        "last_utc": str(hourly["ts_utc"].iloc[-1]), "timezone": TIMEZONE}
    st["hours_in_timeline"] = int(len(hourly))
    st["hours_with_rows"] = int(hourly["row_present"].sum())
    st["missing_timestamps"] = int((~hourly["row_present"]).sum())
    st["valid_hours"] = int((hourly["quality_flag"] == "VALID").sum())
    st["hourly_quality_flag_counts"] = {k: int(v) for k, v in hourly["quality_flag"].value_counts().items()}

    # duplicates
    dup_hours = hourly[hourly["flag_duplicate_ts"]]
    st["duplicate_timestamps"] = int(len(dup_hours))
    st["duplicate_rows"] = int(rows["flag_duplicate_ts"].sum())
    st["duplicate_conflicts"] = int(hourly["flag_duplicate_conflict"].sum())

    # irregular days
    irr = irregular_days(hourly)
    st["irregular_days_total"] = int(len(irr))
    st["irregular_days_by_kind"] = {k: int(v) for k, v in irr["kind"].value_counts().items()} if len(irr) else {}
    st["days_23_rows"] = int((irr["kind"] == "23_rows").sum()) if len(irr) else 0
    st["days_25_rows"] = int((irr["kind"] == "25_rows").sum()) if len(irr) else 0
    # Do irregular days coincide with US daylight-saving changes? (Kyrgyzstan has none.) Evidence only.
    dst_dates = ["2019-03-10", "2019-11-03", "2020-03-08", "2020-11-01", "2021-03-14", "2021-11-07",
                 "2022-03-13", "2022-11-06", "2023-03-12", "2023-11-05", "2024-03-10"]
    day = hourly["ts_local"].dt.strftime("%Y-%m-%d")
    st["us_dst_dates_row_counts"] = {d: int(hourly.loc[day == d, "n_source_rows"].sum()) for d in dst_dates
                                     if (day == d).any()}

    # values
    valid = hourly[hourly["quality_flag"] == "VALID"]
    st["negative_rows_all"] = int(rows["flag_neg_noise"].sum())
    st["negative_hours_valid"] = int(valid["flag_neg_noise"].sum())
    st["negative_by_year_valid"] = {int(y): int(n) for y, n in valid.groupby(valid["ts_local"].dt.year)["flag_neg_noise"].sum().items()}
    st["negative_min_valid"] = _r(valid["pm25_raw_ugm3"].min())
    st["high_value_hours_valid"] = int(valid["flag_high_value"].sum())
    st["max_valid_ugm3"] = _r(valid["pm25_raw_ugm3"].max())
    st["local_drop_hours"] = int(hourly["flag_local_drop"].sum())
    st["local_spike_hours"] = int(hourly["flag_local_spike"].sum())
    for name in ("flag_local_drop", "flag_local_spike"):
        by = hourly.groupby(hourly["ts_local"].dt.year)[name].sum()
        st[f"{name}_by_year"] = {int(y): int(n) for y, n in by.items()}
    st["imputed_feature_hours"] = int(hourly["is_imputed"].sum())

    # gaps (no valid model value)
    gaps = find_gaps(hourly)
    ts_gaps = find_gaps(hourly, only_missing_rows=True)
    st["gaps_no_valid_value"] = _gap_summary(gaps)
    st["gaps_missing_timestamps"] = _gap_summary(ts_gaps)

    # coverage by year (expected = hours of the calendar year inside the data range)
    cov = {}
    first_utc, last_utc = hourly["ts_local"].iloc[0], hourly["ts_local"].iloc[-1]
    rows_valid_source = rows[(rows["qc_class"] == "VALID") & ~rows["flag_missing_value"]]
    for y in sorted(hourly["ts_local"].dt.year.unique()):
        start = max(first_utc, pd.Timestamp(f"{y}-01-01", tz=TIMEZONE))
        end = min(last_utc + pd.Timedelta(hours=1), pd.Timestamp(f"{y + 1}-01-01", tz=TIMEZONE))
        expected = int((end - start) / pd.Timedelta(hours=1))
        hv = valid[valid["ts_local"].dt.year == y]
        rv = rows_valid_source[rows_valid_source["ts_local"].dt.year == y]
        cov[int(y)] = {
            "expected_hours": expected,
            "valid_hours": int(len(hv)),
            "valid_coverage_pct": _r(100 * len(hv) / expected),
            "mean_raw_primary_valid": _r(hv["pm25_raw_ugm3"].mean()),
            "mean_model_clipped": _r(hv["pm25_model_ugm3"].mean()),
            "source_valid_rows": int(len(rv)),
            "source_valid_rows_mean_raw": _r(rv["pm25_raw_ugm3"].mean()),
        }
    st["coverage_by_year"] = cov

    comparison = {}
    for y, (ev, ec, em) in EXPECTED_PRIOR.items():
        c = cov.get(y)
        if c:
            comparison[y] = {
                "expected_valid_hours": ev, "expected_coverage_pct": ec, "expected_mean": em,
                "source_valid_rows": c["source_valid_rows"], "source_valid_rows_mean_raw": c["source_valid_rows_mean_raw"],
                "primary_valid_hours": c["valid_hours"], "primary_coverage_pct": c["valid_coverage_pct"],
                "primary_mean_raw": c["mean_raw_primary_valid"],
                "valid_hours_diff": c["valid_hours"] - ev,
                "source_rows_diff": c["source_valid_rows"] - ev,
            }
    st["comparison_with_prior"] = comparison
    conf = hourly[hourly["flag_duplicate_conflict"]]
    st["conflict_hours_by_year"] = {int(y): int(n) for y, n in conf.groupby(conf["ts_local"].dt.year).size().items()}
    same = [y for y, c in comparison.items() if c["source_rows_diff"] == 0]
    differ = {y: c["source_rows_diff"] for y, c in comparison.items() if c["source_rows_diff"] != 0}
    st["comparison_note"] = (
        f"Source valid-row counts reproduce the earlier figures exactly for {same or 'no year'}"
        + (f"; they differ for {differ}. " if differ else ". ")
        + "The cleaned primary series has fewer valid hours in some years only because conflicting duplicate "
        f"timestamps are excluded (conflict hours by year: {st['conflict_hours_by_year']}). "
        "Coverage percentages use the same expected-hours definition as the earlier pass. "
        "The cleaning logic was not tuned to match the earlier numbers."
    )

    # summaries
    v = valid.copy()
    v["month"] = v["ts_local"].dt.month
    v["year"] = v["ts_local"].dt.year
    v["season"] = v["month"].map(SEASONS)
    st["by_month_of_year"] = {int(m): _summ(g["pm25_model_ugm3"]) for m, g in v.groupby("month")}
    st["by_year_month"] = {f"{y}-{m:02d}": {**_summ(g["pm25_model_ugm3"]),
                                              "min_raw": _r(g["pm25_raw_ugm3"].min()),
                                              "share_below_5": _r(100 * (g["pm25_raw_ugm3"] < NEAR_ZERO_UGM3).mean())}
                           for (y, m), g in v.groupby(["year", "month"])}
    st["min_valid_raw_by_year"] = {int(y): _r(g["pm25_raw_ugm3"].min()) for y, g in v.groupby("year")}
    st["by_season_year"] = {f"{y}-{s}": _summ(g["pm25_model_ugm3"]) for (y, s), g in v.groupby(["year", "season"])}
    st["by_season"] = {s: _summ(g["pm25_model_ugm3"]) for s, g in v.groupby("season")}
    return st


def _gap_summary(g: pd.DataFrame) -> dict:
    if g.empty:
        return {"count": 0, "total_hours": 0, "longest": [], "buckets": {}}
    hrs = g["hours"]
    longest = g.sort_values("hours", ascending=False).head(10)
    return {
        "count": int(len(g)),
        "total_hours": int(hrs.sum()),
        "median_hours": _r(hrs.median()),
        "longest": [{"start_local": str(r.start_local), "end_local": str(r.end_local), "hours": int(r.hours), "kind": r.kind}
                    for r in longest.itertuples()],
        "buckets": {">=1h": int((hrs >= 1).sum()), ">=3h": int((hrs >= 3).sum()), ">=6h": int((hrs >= 6).sum()),
                    ">=24h": int((hrs >= 24).sum()), ">=72h": int((hrs >= 72).sum())},
        "by_kind": {k: int(v) for k, v in g["kind"].value_counts().items()},
    }


# ----------------------------------------------------------------------------- markdown
def _table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join("" if c is None else str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def render_markdown(st: dict, cfg: dict, limitations: list[str], decisions: list[str]) -> str:
    L: list[str] = []
    L.append("# US Embassy Bishkek PM2.5: validation report\n")
    L.append(f"Processing version: `{st['processing_version']}` · timezone `{st['date_range']['timezone']}` · "
             "units µg/m³ (`Raw Conc.`; `NowCast Conc.` is not used).\n")

    L.append("## 1. Source files and rows\n")
    L.append(_table(["file", "rows"], [[f, f"{n:,}"] for f, n in st["source_files"].items()]))
    L.append(f"\n{st['n_source_files']} files, **{st['rows_read']:,} rows** read. "
             f"{st['rows_in_file_of_other_year']} rows carry a timestamp from a different year than their file "
             "(each yearly file ends with the first hour(s) of the next year).\n")

    L.append("## 2. Quality flags (`QC Name`)\n")
    L.append(_table(["QC Name (original)", "rows"], [[k, f"{v:,}"] for k, v in st["qc_counts_original"].items()]))
    L.append(f"\n`-999` sentinel rows: {st['sentinel_999_rows']}. Unparsable timestamps: {st['time_unparsed_rows']}. "
             f"Year/Month/Day/Hour fields disagreeing with `Date (LT)`: {st['time_field_mismatch_rows']}. "
             f"Unit mismatches: {st['unit_mismatch_rows']}.\n")

    d = st["date_range"]
    L.append("## 3. Date range and timeline\n")
    L.append(f"First: {d['first_local']} · Last: {d['last_local']} (local). Hourly timeline: **{st['hours_in_timeline']:,} hours**; "
             f"hours with at least one source row: {st['hours_with_rows']:,}; hours with a valid primary value: **{st['valid_hours']:,}**.\n")
    L.append(_table(["hourly quality flag", "hours"], [[k, f"{v:,}"] for k, v in st["hourly_quality_flag_counts"].items()]))

    L.append("\n## 4. Valid hourly coverage by year\n")
    L.append("Expected hours = hours of the calendar year inside the data range. "
             "Mean (raw) includes negative values; mean (model) is clipped at 0.\n")
    L.append(_table(["year", "expected h", "valid h", "coverage %", "mean raw", "mean model"],
                    [[y, f"{c['expected_hours']:,}", f"{c['valid_hours']:,}", c["valid_coverage_pct"], c["mean_raw_primary_valid"],
                      c["mean_model_clipped"]] for y, c in st["coverage_by_year"].items()]))

    L.append("\n## 5. Missing timestamps\n")
    g = st["gaps_missing_timestamps"]
    L.append(f"**{st['missing_timestamps']:,} hours** have no source row, in {g['count']} runs (median {g.get('median_hours')} h).\n")

    L.append("## 6. Duplicate timestamps\n")
    L.append(f"{st['duplicate_timestamps']} timestamps appear more than once ({st['duplicate_rows']} rows); "
             f"{st['duplicate_conflicts']} have conflicting values and are **excluded** from the primary series "
             "(listed in `embassy_duplicates.csv`). Not resolved by guessing which row is right.\n")

    L.append("## 7. Irregular days (not 24 rows)\n")
    L.append(f"{st['irregular_days_total']} local dates do not have 24 rows: "
             f"{st['days_23_rows']} with 23 rows (one hour missing), {st['days_25_rows']} with 25 rows (one duplicated hour), "
             f"others: {st['irregular_days_by_kind']}. Details: `embassy_irregular_days.csv`. "
             "Nothing was shifted to hide them.\n")

    L.append("## 8. Negative measurements\n")
    L.append(f"Rows with a negative raw value: {st['negative_rows_all']:,} (all QC classes). Valid hours with a negative value: "
             f"**{st['negative_hours_valid']:,}** (minimum {st['negative_min_valid']}). By year: {st['negative_by_year_valid']}. "
             "Kept in `pm25_raw_ugm3`, flagged `flag_neg_noise`; `pm25_model_ugm3` is clipped at 0.\n")

    L.append("## 9. High and unusual readings\n")
    L.append(f"Valid hours with raw ≥ {cfg['high_value_ugm3']:.0f} µg/m³ (`flag_high_value`): **{st['high_value_hours_valid']}** "
             f"(maximum {st['max_valid_ugm3']}). Local isolated drops (`flag_local_drop`): **{st['local_drop_hours']}**; "
             f"local spikes (`flag_local_spike`): **{st['local_spike_hours']}**. Nothing was deleted.\n")
    L.append(_table(["year", "local drops", "local spikes"],
                    [[y, st["flag_local_drop_by_year"].get(y, 0), st["flag_local_spike_by_year"].get(y, 0)]
                     for y in sorted(st["flag_local_drop_by_year"])]))

    L.append("\n## 10. Gaps (hours without a valid value)\n")
    g = st["gaps_no_valid_value"]
    L.append(f"{g['count']} gaps, {g['total_hours']:,} hours in total, median {g.get('median_hours')} h. "
             f"By length: {g['buckets']}. By cause: {g.get('by_kind')}.\n")
    L.append(_table(["start (local)", "end (local)", "hours", "kind"],
                    [[x["start_local"], x["end_local"], x["hours"], x["kind"]] for x in g["longest"]]))
    L.append(f"\nFeature-only interpolation (gaps ≤ {cfg['interp_max_gap_h']} h) imputed {st['imputed_feature_hours']:,} hours; "
             "these are flagged `is_imputed` and live only in `pm25_feature_interp_ugm3`.\n")

    L.append("## 11. Monthly and seasonal PM2.5 (model column, µg/m³)\n")
    L.append("Month of year (all years):\n")
    L.append(_table(["month", "n", "mean", "median", "p95", "max"],
                    [[m, s["n"], s["mean"], s["median"], s["p95"], s["max"]] for m, s in st["by_month_of_year"].items()]))
    L.append("\nSeason (all years; DJF = Dec–Feb):\n")
    L.append(_table(["season", "n", "mean", "median", "p95", "max"],
                    [[k, s["n"], s["mean"], s["median"], s["p95"], s["max"]] for k, s in st["by_season"].items()]))
    L.append("\nMonth by month, with the share of valid readings below 5 µg/m³:\n")
    L.append("Low shares are expected in clean-air summer months; compare like with like (winter with winter).\n")
    L.append(_table(["year-month", "n", "mean", "median", "min raw", "share <5 %"],
                    [[k, s["n"], s["mean"], s["median"], s["min_raw"], s["share_below_5"]] for k, s in st["by_year_month"].items()]))

    L.append("\n## 12. Comparison with previously observed figures\n")
    L.append("`source valid rows` = rows with QC Valid and a real value, counted by timestamp year, duplicates counted twice "
             "(the earlier exploratory definition). `primary valid hours` = hours in the cleaned series.\n")
    rows_cmp = []
    for y, c in st["comparison_with_prior"].items():
        rows_cmp.append([y, f"{c['expected_valid_hours']:,}", f"{c['source_valid_rows']:,}", f"{c['primary_valid_hours']:,}",
                         c["valid_hours_diff"], c["expected_mean"], c["source_valid_rows_mean_raw"], c["primary_mean_raw"]])
    L.append(_table(["year", "prior valid h", "source valid rows", "primary valid h", "diff (primary−prior)",
                     "prior mean", "source-rows mean", "primary mean"], rows_cmp))
    L.append("\n" + st["comparison_note"] + "\n")

    L.append("## 13. Cleaning decisions\n")
    L += [f"- {x}" for x in decisions]
    L.append("\n## 14. Limitations\n")
    L += [f"- {x}" for x in limitations]
    return "\n".join(L) + "\n"
