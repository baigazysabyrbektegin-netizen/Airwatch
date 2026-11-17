"""Cleaning and validation of the US Embassy Bishkek hourly PM2.5 files.

Design rules (see pipeline/README.md for the full list):
* Source values are never modified: every original field is kept as text under a ``src_`` column.
* ``Raw Conc.`` is the measurement; ``NowCast Conc.`` is kept only for reference.
* ``-999`` is "missing" and is never turned into zero.
* Negative values are kept and flagged ``flag_neg_noise``; a separate model column is clipped at 0.
* High or oddly isolated readings are flagged, never deleted.
* Only quality-flag ``Valid`` records feed the primary (model) column.
* Interpolation exists only in a separate feature column and is always flagged.
* Local timestamps are ``Asia/Bishkek`` (UTC+6, no daylight saving in the data period) and UTC.
  The source does not say whether ``Date (LT)`` marks the start or end of the hour; we keep it as given.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

PROCESSING_VERSION = "embassy-clean-1.0.0"
TIMEZONE = "Asia/Bishkek"
TS_FORMAT = "%Y-%m-%d %I:%M %p"
MISSING_SENTINEL = -999.0

EXPECTED_COLUMNS = [
    "Site", "Parameter", "Date (LT)", "Year", "Month", "Day", "Hour", "NowCast Conc.", "AQI",
    "AQI Category", "Raw Conc.", "Conc. Unit", "Duration", "QC Name",
]
SRC_COLUMNS = {
    "Site": "src_site", "Parameter": "src_parameter", "Date (LT)": "src_date_lt", "Year": "src_year",
    "Month": "src_month", "Day": "src_day", "Hour": "src_hour", "NowCast Conc.": "src_nowcast_conc",
    "AQI": "src_aqi", "AQI Category": "src_aqi_category", "Raw Conc.": "src_raw_conc",
    "Conc. Unit": "src_conc_unit", "Duration": "src_duration", "QC Name": "src_qc_name",
}
QC_CLASSES = {"valid": "VALID", "invalid": "INVALID", "missing": "MISSING", "suspect": "SUSPECT"}


class CleaningError(Exception):
    pass


@dataclass(frozen=True)
class CleaningConfig:
    high_value_ugm3: float = 500.0      # flag_high_value: raw >= this (flag only, never deleted)
    interp_max_gap_h: int = 3           # feature interpolation only fills gaps up to this many hours
    local_window_h: int = 2             # neighbours on each side for the local drop/spike checks
    local_min_neighbours: int = 3
    drop_ratio: float = 0.1             # drop: value < 10 % of the local median ...
    drop_min_median: float = 50.0       # ... when the local median is at least this (ug/m3)
    spike_ratio: float = 5.0            # spike: value > 5x the local median ...
    spike_min_delta: float = 100.0      # ... and at least this far above it (ug/m3)
    expected_unit: str = "UG/M3"

    def as_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------------------- reading
def read_source_csvs(directory: Path) -> pd.DataFrame:
    """Read every CSV in ``directory`` as text, unchanged, in file-name order."""
    files = sorted(Path(directory).glob("*.csv"))
    if not files:
        raise CleaningError(f"No CSV files found in {directory}")
    frames = []
    for f in files:
        df = pd.read_csv(f, dtype=str, keep_default_na=False, na_filter=False, encoding="utf-8-sig")
        if list(df.columns) != EXPECTED_COLUMNS:
            raise CleaningError(
                f"{f.name}: unexpected columns.\n  expected: {EXPECTED_COLUMNS}\n  found:    {list(df.columns)}"
            )
        df = df.rename(columns=SRC_COLUMNS)
        df.insert(0, "source_line", np.arange(2, len(df) + 2))  # physical CSV line (header = line 1)
        df.insert(0, "source_file", f.name)
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out.insert(0, "source_order", np.arange(len(out)))
    return out


# ----------------------------------------------------------------------------- row level
def classify_qc(qc: pd.Series) -> pd.Series:
    """Standardise ``QC Name`` (case/whitespace-insensitive). Unrecognised values -> UNKNOWN."""
    key = qc.astype(str).str.strip().str.lower()
    return key.map(QC_CLASSES).fillna("UNKNOWN").astype(str)


def clean_rows(source: pd.DataFrame, cfg: CleaningConfig | None = None) -> pd.DataFrame:
    """Return the auditable row-level dataset: all source rows, original fields, explicit flags."""
    cfg = cfg or CleaningConfig()
    a = source.copy()

    # --- timestamps
    a["ts_original"] = a["src_date_lt"]
    naive = pd.to_datetime(a["src_date_lt"], format=TS_FORMAT, errors="coerce")
    a["flag_time_unparsed"] = naive.isna()
    a["ts_local"] = naive.dt.tz_localize(TIMEZONE, ambiguous="raise", nonexistent="raise")
    a["ts_utc"] = a["ts_local"].dt.tz_convert("UTC")
    parts = {k: pd.to_numeric(a[f"src_{k}"], errors="coerce") for k in ("year", "month", "day", "hour")}
    a["flag_time_field_mismatch"] = (
        naive.notna()
        & ((naive.dt.year != parts["year"]) | (naive.dt.month != parts["month"])
           | (naive.dt.day != parts["day"]) | (naive.dt.hour != parts["hour"]))
    )

    # --- quality flag and values
    a["qc_name_original"] = a["src_qc_name"]
    a["qc_class"] = classify_qc(a["src_qc_name"])
    raw_numeric = pd.to_numeric(a["src_raw_conc"], errors="coerce")
    a["flag_sentinel_999"] = raw_numeric == MISSING_SENTINEL
    a["pm25_raw_ugm3"] = raw_numeric.where(~a["flag_sentinel_999"])          # NaN, never 0, for missing
    a["flag_missing_value"] = a["pm25_raw_ugm3"].isna() | (a["qc_class"] == "MISSING")
    now = pd.to_numeric(a["src_nowcast_conc"], errors="coerce")
    a["nowcast_ugm3"] = now.where(now != MISSING_SENTINEL)                    # reference only
    a["flag_unit_mismatch"] = a["src_conc_unit"].str.strip().str.upper() != cfg.expected_unit
    a["flag_neg_noise"] = a["pm25_raw_ugm3"] < 0
    a["flag_high_value"] = a["pm25_raw_ugm3"] >= cfg.high_value_ugm3

    # --- duplicates (same instant), and which row (if any) is the primary measurement
    candidate = (
        (a["qc_class"] == "VALID") & ~a["flag_missing_value"] & ~a["flag_unit_mismatch"] & ~a["flag_time_unparsed"]
    )
    key = a["ts_utc"]
    a["dup_group_size"] = key.groupby(key).transform("size").where(key.notna(), 1).astype(int)
    a["flag_duplicate_ts"] = a["dup_group_size"] > 1
    cand_vals = a["pm25_raw_ugm3"].where(candidate)
    n_distinct = cand_vals.groupby(key).transform("nunique")
    n_cand = candidate.astype(int).groupby(key).transform("sum")
    a["flag_duplicate_conflict"] = a["flag_duplicate_ts"] & (n_distinct > 1)
    first_cand_order = a["source_order"].where(candidate).groupby(key).transform("min")
    is_first_cand = candidate & (a["source_order"] == first_cand_order)
    a["is_primary_valid"] = candidate & ~a["flag_duplicate_conflict"] & (is_first_cand | ~a["flag_duplicate_ts"])
    a["is_primary_valid"] = a["is_primary_valid"] & (n_cand >= 1)
    a["pm25_model_ugm3"] = a["pm25_raw_ugm3"].clip(lower=0).where(a["is_primary_valid"])

    # --- single standardised row-level flag (priority order)
    q = np.select(
        [
            a["flag_time_unparsed"], a["flag_unit_mismatch"], a["flag_duplicate_conflict"],
            a["flag_missing_value"], a["qc_class"] == "INVALID", a["qc_class"] == "SUSPECT",
            a["qc_class"] == "UNKNOWN",
        ],
        ["TIME_UNPARSED", "UNIT_MISMATCH", "DUP_CONFLICT", "MISSING_VALUE", "INVALID", "SUSPECT", "UNKNOWN_QC"],
        default="VALID",
    )
    a["quality_flag"] = pd.Series(q, index=a.index).astype(str)
    a["processing_version"] = PROCESSING_VERSION
    return a


# ----------------------------------------------------------------------------- hourly series
def find_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Return (start, end_exclusive) index pairs for each run of True values."""
    runs, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    return runs


def fill_short_gaps(values: pd.Series, max_gap: int) -> tuple[pd.Series, pd.Series]:
    """Linear interpolation of gaps of at most ``max_gap`` hours that have a valid value on both
    sides. Leading/trailing gaps and longer gaps stay NaN. Returns (filled, imputed_mask)."""
    arr = values.to_numpy(dtype=float, copy=True)
    imputed = np.zeros(len(arr), dtype=bool)
    for start, end in find_runs(np.isnan(arr)):
        length = end - start
        if start > 0 and end < len(arr) and length <= max_gap:
            left, right = arr[start - 1], arr[end]
            arr[start:end] = left + (right - left) * np.arange(1, length + 1) / (length + 1)
            imputed[start:end] = True
    return pd.Series(arr, index=values.index), pd.Series(imputed, index=values.index)


def local_flags(model: pd.Series, cfg: CleaningConfig) -> tuple[pd.Series, pd.Series]:
    """Flag isolated drops / spikes relative to the median of the surrounding valid hours."""
    shifts = [model.shift(k) for k in range(-cfg.local_window_h, cfg.local_window_h + 1) if k != 0]
    nb = pd.concat(shifts, axis=1)
    med = nb.median(axis=1)
    ok = model.notna() & (nb.notna().sum(axis=1) >= cfg.local_min_neighbours)
    drop = ok & (med >= cfg.drop_min_median) & (model < cfg.drop_ratio * med)
    spike = ok & (model > cfg.spike_ratio * med) & ((model - med) >= cfg.spike_min_delta)
    return drop, spike


def build_hourly(rows: pd.DataFrame, cfg: CleaningConfig | None = None) -> pd.DataFrame:
    """One row per hour from the first to the last source timestamp (gaps shown, not hidden)."""
    cfg = cfg or CleaningConfig()
    a = rows[rows["ts_utc"].notna()].copy()
    if a.empty:
        raise CleaningError("No parsable timestamps.")
    idx = pd.date_range(a["ts_utc"].min(), a["ts_utc"].max(), freq="h", tz="UTC", name="ts_utc")

    a["_prio"] = (~a["is_primary_valid"]).astype(int)
    rep = a.sort_values(["ts_utc", "_prio", "source_order"]).drop_duplicates("ts_utc", keep="first").set_index("ts_utc")
    grp = a.groupby("ts_utc")
    join = lambda s: "|".join(dict.fromkeys(s.astype(str)))  # noqa: E731
    agg = pd.DataFrame(
        {
            "n_source_rows": grp["source_order"].size(),
            "qc_name_original": grp["qc_name_original"].agg(join),
            "ts_original": grp["ts_original"].agg(join),
            "source_file": grp["source_file"].agg(join),
            "source_line": grp["source_line"].agg(join),
            "flag_duplicate_conflict": grp["flag_duplicate_conflict"].max(),
        }
    )
    h = pd.DataFrame(index=idx)
    h = h.join(agg)
    h["row_present"] = h["n_source_rows"].notna()
    h["n_source_rows"] = h["n_source_rows"].fillna(0).astype(int)
    conflict = h["flag_duplicate_conflict"].fillna(False).astype(bool)
    h["flag_duplicate_conflict"] = conflict
    h["flag_duplicate_ts"] = h["n_source_rows"] > 1

    row_q = rep["quality_flag"].reindex(idx)
    h["quality_flag"] = np.where(~h["row_present"], "NO_ROW", np.where(conflict, "DUP_CONFLICT", row_q))
    h["quality_flag"] = h["quality_flag"].astype(str)
    raw = rep["pm25_raw_ugm3"].reindex(idx).where(~conflict)
    h["pm25_raw_ugm3"] = raw
    h["flag_neg_noise"] = (raw < 0).fillna(False).astype(bool)
    h["flag_high_value"] = (raw >= cfg.high_value_ugm3).fillna(False).astype(bool)
    h["pm25_model_ugm3"] = raw.clip(lower=0).where(h["quality_flag"] == "VALID")

    filled, imputed = fill_short_gaps(h["pm25_model_ugm3"], cfg.interp_max_gap_h)
    h["pm25_feature_interp_ugm3"] = filled
    h["is_imputed"] = imputed
    drop, spike = local_flags(h["pm25_model_ugm3"], cfg)
    h["flag_local_drop"] = drop
    h["flag_local_spike"] = spike

    h["processing_version"] = PROCESSING_VERSION
    h = h.reset_index()
    h.insert(0, "ts_local", h["ts_utc"].dt.tz_convert(TIMEZONE))
    cols = [
        "ts_local", "ts_utc", "ts_original", "row_present", "n_source_rows", "quality_flag",
        "qc_name_original", "pm25_raw_ugm3", "pm25_model_ugm3", "pm25_feature_interp_ugm3", "is_imputed",
        "flag_neg_noise", "flag_high_value", "flag_local_drop", "flag_local_spike", "flag_duplicate_ts",
        "flag_duplicate_conflict", "source_file", "source_line", "processing_version",
    ]
    return h[cols]


# ----------------------------------------------------------------------------- gaps
def find_gaps(hourly: pd.DataFrame, *, only_missing_rows: bool = False) -> pd.DataFrame:
    """Runs of hours without a valid model value (or, with ``only_missing_rows``, without any row)."""
    mask = (~hourly["row_present"]).to_numpy() if only_missing_rows else hourly["pm25_model_ugm3"].isna().to_numpy()
    recs = []
    for gid, (s, e) in enumerate(find_runs(mask), start=1):
        seg = hourly.iloc[s:e]
        no_row = int((~seg["row_present"]).sum())
        kind = "no_source_rows" if no_row == len(seg) else ("rows_not_valid" if no_row == 0 else "mixed")
        recs.append(
            {
                "gap_id": gid,
                "start_local": seg["ts_local"].iloc[0],
                "end_local": seg["ts_local"].iloc[-1],
                "hours": len(seg),
                "kind": kind,
                "hours_without_row": no_row,
            }
        )
    cols = ["gap_id", "start_local", "end_local", "hours", "kind", "hours_without_row"]
    return pd.DataFrame(recs, columns=cols)
