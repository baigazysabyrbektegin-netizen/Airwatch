"""Stage 3: extract, clean and validate the Clarity 2021 sensor workbook.

Run from the repository root with the backend virtual environment:

    python -m pipeline.process_clarity

The workbook is checksum-verified against ``data/manifest/zenodo_12720883.json`` and read, never modified.
Outputs go to ``data/interim/clarity/`` and ``data/processed/clarity/``; the provenance record is
``data/manifest/clarity_processing.json``. An existing output that would change is never overwritten
unless ``--allow-overwrite`` is given (for example after changing the timestamp settings).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pyarrow
import pyarrow.parquet as pq

from pipeline import clarity_clean as cc
from pipeline import clarity_embassy as cem
from pipeline import clarity_extract as cx
from pipeline import clarity_report as cr
from pipeline import clarity_spatial as cs
from pipeline import manifest as mf

REPO_ROOT = Path(__file__).resolve().parents[1]
ZENODO_MANIFEST = REPO_ROOT / "data" / "manifest" / "zenodo_12720883.json"
EMBASSY_MANIFEST = REPO_ROOT / "data" / "manifest" / "embassy_processing.json"
EMBASSY_HOURLY = REPO_ROOT / "data" / "processed" / "embassy" / "embassy_pm25_hourly.parquet"
PROCESSING_MANIFEST = REPO_ROOT / "data" / "manifest" / "clarity_processing.json"
WORKBOOK_NAME = "bishkek_monitoring_claritysensor_2021.xlsx"
SCRIPT = "pipeline/process_clarity.py"

CLEANING_RULES = [
    "The workbook is opened read-only; every cell is read as the text stored in the file (numbers keep their stored digits). Formula cells contribute their cached value.",
    "Data sheet rows with no value in any of the 7 columns are blank rows (outside the declared table range A1:G231317); they are kept in the audit dataset as BLANK_ROW and are not records.",
    "The `Quadrant` column is a spreadsheet formula (lookup into `locations`), not a source measurement; it is preserved but not used. A quadrant is re-derived from coordinates with the workbook's own thresholds only for the inventory.",
    "year/month/day/hour must be integer text; hour must be within the configured label range (1-24); the date must exist. Failing records get TIME_INVALID with a reason and stay in the audit dataset with no timestamp.",
    "Timestamp interpretation is a CONFIGURABLE ASSUMPTION (hour_convention, source_timezone, extra_shift_hours); the defaults are provisional and recorded in every output. No reading is shifted to match the Embassy series.",
    "Hour 24 maps to 00:00 of the following day as the label instant. Local time is Asia/Bishkek (UTC+6, no daylight saving); UTC is stored as well.",
    "PM2.5 is the numeric value of `PM25`. Missing or non-numeric values are never turned into zero; such records stay in the audit dataset (PM_MISSING / PM_NONNUMERIC). Values are not calibrated, corrected, smoothed or filled.",
    "Readings above 500 ug/m3 are flagged (flag_high_value). A reading above 5x the median of the other sensors in the same interval (when at least 5 others report) is flagged as an outlier candidate (flag_neighbor_outlier). Neither flag removes anything.",
    "Duplicate (device, interval) records: identical values keep the first; different values are a conflict (DUP_CONFLICT) and are excluded from the analysis table. None occur in this file.",
    "Devices are matched to coordinates only through the `locations` sheet. Coordinates are kept as the original text and as parsed numbers; none are inferred from device IDs or quadrant labels.",
    "Devices listed in `locations` without readings are kept in the inventory with status no_data_in_file and are not treated as active sensors.",
    "Daily mean: 24 intervals starting on a local calendar date; requires at least 18 valid hours. Monthly mean: mean of valid daily means; requires at least 75% of the eligible days (days of that month inside the file's date range).",
    "The hourly analysis table contains only valid readings (one row per device and interval); gaps are not filled.",
]
DEFAULT_QUALITY_FLAGS = {
    "VALID": "usable reading with a valid timestamp and known device",
    "BLANK_ROW": "worksheet row with no values (not a measurement)",
    "TIME_INVALID": "missing/invalid year, month, day or hour", "DEVICE_MISSING": "no device id", "PM_MISSING": "no PM2.5 value",
    "PM_NONNUMERIC": "PM2.5 present but not numeric", "UNKNOWN_DEVICE": "device not in the locations sheet",
    "DUP_CONFLICT": "same device and interval with different values",
    "flag_high_value": "PM2.5 above the high-value threshold (kept)", "flag_neighbor_outlier": "above 5x the median of other sensors the same interval (kept)",
}


class OutputExistsError(Exception):
    pass


def _rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def _guarded_write(path: Path, writer, allow_overwrite: bool) -> str:
    """Write via a temp file. Never replace an existing file with different content unless allowed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        writer(Path(tmp))
        if path.exists():
            if mf.hash_file(path, ("sha256",))["sha256"] == mf.hash_file(Path(tmp), ("sha256",))["sha256"]:
                os.unlink(tmp)
                return "unchanged"
            if not allow_overwrite:
                raise OutputExistsError(
                    f"{path} already exists with different content. Not overwritten. "
                    "Re-run with --allow-overwrite if this change is intended (for example new timestamp settings)."
                )
            os.replace(tmp, path)
            return "overwritten_with_approval"
        os.replace(tmp, path)
        return "written"
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_parquet(df: pd.DataFrame, path: Path, metadata: dict | None, allow_overwrite: bool) -> str:
    import pyarrow as pa

    def w(p: Path):
        table = pa.Table.from_pandas(df, preserve_index=False)
        md = dict(table.schema.metadata or {})
        if metadata is not None:
            md[b"airwatch.processing"] = json.dumps(metadata, sort_keys=True, default=str).encode("utf-8")
        pq.write_table(table.replace_schema_metadata(md), p, compression="snappy")

    return _guarded_write(path, w, allow_overwrite)


def write_csv(df: pd.DataFrame, path: Path, allow_overwrite: bool) -> str:
    return _guarded_write(path, lambda p: df.to_csv(p, index=False, lineterminator="\n", encoding="utf-8"), allow_overwrite)


def write_text(text: str, path: Path, allow_overwrite: bool) -> str:
    return _guarded_write(path, lambda p: p.write_text(text, encoding="utf-8", newline="\n"), allow_overwrite)


def write_json(obj, path: Path, allow_overwrite: bool) -> str:
    return write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n", path, allow_overwrite)


# ----------------------------------------------------------------------------- sensitivity
def convention_sensitivity(data: pd.DataFrame, inventory: pd.DataFrame, base: cc.ClarityConfig) -> list[dict]:
    variants = [
        ("default (hour_ending, Asia/Bishkek, shift 0)", base),
        ("hour label read as hour START (Asia/Bishkek)", cc.ClarityConfig(**{**_cfg_kwargs(base), "hour_convention": "hour_start"})),
        ("labels in UTC (hour_ending)", cc.ClarityConfig(**{**_cfg_kwargs(base), "source_timezone": "UTC"})),
        ("extra shift +1 h (sensitivity only)", cc.ClarityConfig(**{**_cfg_kwargs(base), "extra_shift_hours": 1})),
    ]
    out, ref = [], None
    for label, cfg in variants:
        a = cc.build_audit(data, inventory, cfg)
        h = cc.build_hourly(a)
        d = cc.build_daily(h, cfg)
        m = cc.build_monthly(d, h, cfg)
        date = h.set_index("source_excel_row")["ts_start_local"].dt.date
        if ref is None:
            ref = date
        changed = int((date.reindex(ref.index) != ref).sum())
        out.append({"label": label, "first_start_local": str(h["ts_start_local"].min()), "days_total": int(len(d)),
                    "days_meeting": int(d["meets_min_hours"].sum()), "months_meeting": f"{int(m['meets_threshold'].sum())} of {len(m)}",
                    "date_changed_vs_default": changed})
    return out


def _cfg_kwargs(cfg: cc.ClarityConfig) -> dict:
    return {k: getattr(cfg, k) for k in cfg.as_dict()}


def limitations_from(st: dict) -> list[str]:
    ta = st["timestamp_assumptions"]
    return [
        f"The hour convention and time zone are NOT documented by the source. Provisional setting: {ta['hour_convention']}, {ta['source_timezone']}, extra shift {ta['extra_shift_hours']} h. "
        "The apparent 1-hour offset against the Embassy series is unresolved and was not applied.",
        cr.CALIBRATION_STATEMENT,
        "One year (28 Feb to 31 Dec 2021). There is no January or February data, so the \"winter\" in the spatial analysis means Nov–Dec only.",
        f"{st['inventory']['n_no_data_in_file']} of {st['inventory']['n_listed']} listed devices have no readings in this file. "
        + (f"{sum(len(g['devices']) - len(g['devices_with_data']) for g in st['inventory']['shared_coordinate_groups'])} of them have exactly the same coordinates as a device that has data "
           "(possibly placeholder or shared positions; cause unknown). " if st["inventory"]["shared_coordinate_groups"] else "")
        + "They are kept in the inventory but not treated as active sensors.",
        "The 10,826 blank rows below the data table were treated as non-records; an earlier exploratory summary had counted them as records with a missing hour.",
        "This is historical 2021 coverage; nothing here describes present-day sensor coverage.",
        "The low end looks floored or left-censored at about 4 µg/m³ (no reading below 4.01, with an abrupt start), so clean-air statistics are biased high.",
        "The sensors are all in the built-up area; spatial statistics cannot be extrapolated to the rest of the airshed, and the representativeness estimates are descriptive for this dataset only.",
        "The Embassy location used for the comparison is approximate (OpenStreetMap building point, not the monitor).",
        "Redistribution terms: the Zenodo record is CC BY 4.0 (attribution required). The paper attributes the Clarity network to the Kyrgyz Hydrometeorological Agency; the record does not document the data provider's own terms, so provenance review is still required.",
    ]


def run(
    *,
    zenodo_manifest: Path = ZENODO_MANIFEST,
    interim_dir: Path | None = None,
    processed_dir: Path | None = None,
    manifest_path: Path = PROCESSING_MANIFEST,
    embassy_hourly: Path | None = EMBASSY_HOURLY,
    embassy_manifest: Path | None = EMBASSY_MANIFEST,
    skip_embassy: bool = False,
    cfg: cc.ClarityConfig | None = None,
    allow_overwrite: bool = False,
    out=print,
) -> int:
    cfg = cfg or cc.ClarityConfig()
    interim = interim_dir or REPO_ROOT / "data" / "interim" / "clarity"
    processed = processed_dir or REPO_ROOT / "data" / "processed" / "clarity"
    reports, cleaned = interim / "reports", interim / "cleaned"
    zm = mf.load_manifest(zenodo_manifest)
    if zm is None:
        out(f"ERROR: Zenodo manifest not found: {zenodo_manifest}")
        return 2
    entry = next((f for f in zm["files"] if f["filename"] == WORKBOOK_NAME), None)
    if entry is None or entry.get("download_status") not in ("downloaded_verified", "already_present_verified"):
        out(f"ERROR: {WORKBOOK_NAME} is not a verified download in {zenodo_manifest}.")
        return 2
    workbook = REPO_ROOT / entry["local_path"]
    previous = mf.load_manifest(manifest_path) or {}

    out(f"Verifying {workbook.name} against the Zenodo manifest ...")
    calc = cx.verify_workbook(workbook, entry["calculated_checksums"]["sha256"], entry["calculated_checksums"]["md5"])
    out("Reading workbook (read-only) ...")
    wb = cx.read_workbook(workbook)
    ext_records = cx.write_extracted(wb["frames"], interim / "extracted")
    prev_ext = {e["filename"]: e for e in previous.get("extraction", {}).get("files", [])}
    now = mf.utc_now()
    for e in ext_records:
        old = prev_ext.get(e["filename"])
        e["extracted_at_utc"] = old["extracted_at_utc"] if old and old.get("sha256") == e["sha256"] else now
        e["local_path"] = _rel(Path(e["local_path"]))
    out(f"Extracted {len(ext_records)} sheets to {_rel(interim / 'extracted')}")

    data, locations = wb["frames"][cx.DATA_SHEET], wb["frames"][cx.LOCATIONS_SHEET]
    inv0 = cc.build_inventory(locations, cfg)
    audit = cc.build_audit(data, inv0, cfg)
    audit = cc.add_neighbor_flags(audit, cfg)
    hourly = cc.build_hourly(audit)
    daily = cc.build_daily(hourly, cfg)
    monthly = cc.build_monthly(daily, hourly, cfg)
    inventory = cc.finish_inventory(inv0, audit, hourly)
    coverage = cc.device_coverage(audit, hourly, monthly, inventory, cfg)
    st = cr.compute_stats(audit, hourly, daily, monthly, coverage, inventory, wb, cfg)
    out(f"Records {st['rows']['records']:,}; valid {st['rows']['valid_readings']:,}; devices with data {st['inventory']['n_with_data']}")

    out("Spatial analysis ...")
    sp = cs.spatial_analysis(hourly, inventory)
    w = sp["periods"]["winter_nov_dec"]
    sp_ratio = sp["winter_vs_summer_sd_ratio"]
    if w.get("insufficient_data") or "estimate" not in sp_ratio:
        st["spatial_headline"] = "Not enough sensors with sufficient hours for spatial statistics."
    else:
        d25 = w["distance_km_at_25pct_of_sill"]
        st["spatial_headline"] = (
            f"Winter (Nov–Dec) between-sensor spread is about {sp_ratio['estimate']:.1f}× the summer spread; semivariance reaches 25% of the sill at about "
            f"{'n/a' if d25 is None else round(d25, 1)} km (winter, this dataset only)."
        )
    st["spatial"] = {k: v for k, v in sp.items() if k != "method"}

    emb = diurnal = None
    if not skip_embassy:
        out("Embassy comparison ...")
        e_df = cem.load_embassy_hourly(embassy_hourly, embassy_manifest)
        emb = cem.compare(hourly, inventory, e_df)
        diurnal = cem.diurnal_evidence(hourly, e_df)
        st["embassy_comparison"] = emb
        st["diurnal_evidence"] = diurnal
    out("Timestamp sensitivity ...")
    sens = convention_sensitivity(data, inv0, cfg)
    st["timestamp_sensitivity"] = sens

    limitations = limitations_from(st)
    other = ["clarity_timestamp_report.md", "clarity_device_coverage_report.md", "clarity_spatial_report.md", "clarity_embassy_comparison.md"]
    meta = {"processing_version": cc.PROCESSING_VERSION, "timestamp_assumptions": cfg.timestamp_assumptions(), "source_workbook": WORKBOOK_NAME}
    ow = allow_overwrite
    status: dict[str, str] = {}

    def rec(path: Path, fmt: str, role: str, rows, st_):
        status[_rel(path)] = st_
        return {"path": _rel(path), "format": fmt, "role": role, "rows": rows, "size_bytes": path.stat().st_size,
                "sha256": mf.hash_file(path, ("sha256",))["sha256"], "write_status": st_}

    outputs = []
    audit_out = audit.copy()
    outputs.append(rec(cleaned / "clarity_audit_rows.parquet", "parquet", "full audit dataset: every worksheet row, original fields (src_*), flags",
                       len(audit_out), write_parquet(audit_out, cleaned / "clarity_audit_rows.parquet", meta, ow)))
    tn_cols = ["excel_row", "row_kind", "device_id", "src_year", "src_month", "src_day", "src_hour", "hour_label", "time_issue", "flag_time_invalid",
               "ts_label_utc", "ts_start_utc", "ts_start_local", "ts_end_utc", "quality_flag"]
    outputs.append(rec(cleaned / "clarity_timestamp_normalized.parquet", "parquet", "timestamp-normalised dataset (original components + interpreted timestamps)",
                       len(audit), write_parquet(audit[tn_cols], cleaned / "clarity_timestamp_normalized.parquet", meta, ow)))
    outputs.append(rec(cleaned / "clarity_device_inventory.parquet", "parquet", "device inventory (all listed devices)", len(inventory),
                       write_parquet(inventory, cleaned / "clarity_device_inventory.parquet", meta, ow)))
    outputs.append(rec(cleaned / "clarity_device_coverage.parquet", "parquet", "device coverage statistics", len(coverage),
                       write_parquet(coverage, cleaned / "clarity_device_coverage.parquet", meta, ow)))
    outputs.append(rec(processed / "clarity_pm25_hourly.parquet", "parquet", "primary analysis table: valid readings per device and interval",
                       len(hourly), write_parquet(hourly, processed / "clarity_pm25_hourly.parquet", meta, ow)))
    outputs.append(rec(processed / "clarity_pm25_hourly.csv", "csv", "hourly table, CSV copy (timestamp settings are in the manifest)",
                       len(hourly), write_csv(hourly, processed / "clarity_pm25_hourly.csv", ow)))
    outputs.append(rec(processed / "clarity_daily.parquet", "parquet", "daily means per device (>= 18 valid hours)", len(daily),
                       write_parquet(daily, processed / "clarity_daily.parquet", meta, ow)))
    outputs.append(rec(processed / "clarity_monthly.parquet", "parquet", "monthly means per device (>= 75% of eligible days)", len(monthly),
                       write_parquet(monthly, processed / "clarity_monthly.parquet", meta, ow)))
    outputs.append(rec(processed / "clarity_device_inventory.csv", "csv", "device inventory incl. no_data_in_file status", len(inventory),
                       write_csv(inventory, processed / "clarity_device_inventory.csv", ow)))

    main_md = cr.render_main(st, limitations, CLEANING_RULES, other)
    reports_specs = [
        ("clarity_validation_report.md", "markdown", "validation report", main_md),
        ("clarity_timestamp_report.md", "markdown", "timestamp issue report", cr.render_timestamp(st, sens, diurnal)),
        ("clarity_device_coverage_report.md", "markdown", "device coverage report", cr.render_devices(coverage, inventory)),
        ("clarity_spatial_report.md", "markdown", "spatial analysis report",
         cr.render_spatial(sp, "Sensors cluster in the central built-up area; no inference is made for areas without sensors.")),
    ]
    if emb is not None:
        reports_specs.append(("clarity_embassy_comparison.md", "markdown", "Embassy comparison report", cr.render_embassy(emb, diurnal)))
    for name, fmt, role, text in reports_specs:
        p = reports / name
        outputs.append(rec(p, fmt, role, None, write_text(text, p, ow)))
    p = reports / "clarity_validation_stats.json"
    outputs.append(rec(p, "json", "validation statistics", None, write_json(st, p, ow)))
    p = reports / "clarity_sheet_inventory.json"
    outputs.append(rec(p, "json", "workbook sheet inventory", None,
                       write_json({k: v for k, v in wb.items() if k != "frames"}, p, ow)))
    p = reports / "clarity_device_coverage.csv"
    outputs.append(rec(p, "csv", "device coverage table", len(coverage), write_csv(coverage, p, ow)))

    manifest = {
        "schema_version": 1, "stage": "clarity_pm25_processing", "generated_at_utc": mf.utc_now(),
        "processing": {"script": SCRIPT, "version": cc.PROCESSING_VERSION, "python": platform.python_version(), "pandas": pd.__version__,
                       "pyarrow": pyarrow.__version__, "xlsx_reader": "pipeline/xlsx_reader.py (stdlib zipfile + XML; no extra dependency)"},
        "source": {
            "filename": WORKBOOK_NAME, "local_path": entry["local_path"], "zenodo_record_id": zm["record"]["record_id"], "doi": zm["record"]["doi"],
            "size_bytes": entry["size_bytes"], "zenodo_checksum": entry["zenodo_checksum"], "md5": calc["md5"], "sha256": calc["sha256"],
            "verified_against_zenodo_manifest": True, "zenodo_manifest": _rel(zenodo_manifest), "opened_read_only": True,
        },
        "licence": {
            "record_level_licence": zm["record"]["licence"], "attribution_required": True,
            "attribution": "Guttikunda, S. (2024). A Multi-Pollutant Emissions Inventory for Air Pollution Modeling and Supporting Information for Bishkek. Zenodo. doi:10.5281/zenodo.12720883 (CC BY 4.0).",
            "provenance_review_required": entry["provenance"]["review_required"],
            "status": "Record-level CC BY 4.0 only; the Clarity data provider's own terms are not documented in the record.",
        },
        "workbook": {"sheet_names": [s["sheet"] for s in wb["inventory"]], "sheets": wb["inventory"], "tables": wb["tables"],
                     "data_sheet_rows": st["rows"], "doc_properties": wb["doc_properties"]},
        "extraction": {"output_dir": _rel(interim / "extracted"), "files": ext_records},
        "timestamp_assumptions": cfg.timestamp_assumptions(),
        "config": cfg.as_dict(),
        "cleaning_rules": CLEANING_RULES,
        "quality_flags": DEFAULT_QUALITY_FLAGS,
        "device_inventory": inventory[["device_id", "latitude_src", "longitude_src", "status", "n_records_in_file", "n_valid_readings",
                                       "flag_shared_coordinates", "quadrant_rule"]].to_dict("records"),
        "dataset_summary": {k: st["rows"][k] for k in ("records", "blank_rows", "valid_readings", "invalid_or_excluded_records")}
        | {"devices_with_data": st["inventory"]["n_with_data"], "devices_listed": st["inventory"]["n_listed"], **st["time_range"]},
        "outputs": outputs,
        "known_limitations": limitations,
    }
    mf.write_manifest_atomic(manifest_path, manifest)
    out(f"Processing manifest written: {_rel(manifest_path)}")
    for o in outputs:
        out(f"  {o['size_bytes']:>11,}  {o['path']}  [{o['write_status']}]")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--hour-convention", choices=cc.HOUR_CONVENTIONS, default="hour_ending")
    p.add_argument("--source-timezone", choices=cc.SOURCE_TIMEZONES, default="Asia/Bishkek")
    p.add_argument("--extra-shift-hours", type=int, default=0, help="sensitivity only; 0 means never shift readings")
    p.add_argument("--skip-embassy", action="store_true", help="skip the Embassy comparison")
    p.add_argument("--allow-overwrite", action="store_true", help="allow replacing existing outputs whose content would change")
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    cfg = cc.ClarityConfig(hour_convention=a.hour_convention, source_timezone=a.source_timezone, extra_shift_hours=a.extra_shift_hours)
    try:
        return run(cfg=cfg, skip_embassy=a.skip_embassy, allow_overwrite=a.allow_overwrite)
    except (cx.ClarityExtractError, cc.ClarityCleanError, cem.EmbassyComparisonError, OutputExistsError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
