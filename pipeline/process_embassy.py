"""Stage 2: extract, clean, validate and package the US Embassy Bishkek PM2.5 data.

Run from the repository root with the backend virtual environment:

    python -m pipeline.process_embassy

Reads the archive recorded in ``data/manifest/zenodo_12720883.json`` (checksum verified first),
extracts it to ``data/interim/embassy/extracted/``, writes the auditable row-level dataset and the
reports to ``data/interim/embassy/``, the hourly analysis tables to ``data/processed/embassy/``
and a processing manifest to ``data/manifest/embassy_processing.json``.
The original archive and the Zenodo manifest are never modified.
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

from pipeline import embassy_clean as ec
from pipeline import embassy_extract as ex
from pipeline import embassy_report as er
from pipeline import manifest as mf

REPO_ROOT = Path(__file__).resolve().parents[1]
ZENODO_MANIFEST = REPO_ROOT / "data" / "manifest" / "zenodo_12720883.json"
PROCESSING_MANIFEST = REPO_ROOT / "data" / "manifest" / "embassy_processing.json"
ARCHIVE_NAME = "bishkek_monitoring_us.embassy2019-2024.rar"
SCRIPT = "pipeline/process_embassy.py"

CLEANING_RULES = [
    "Source CSV fields are read as text and kept unchanged under src_* columns; no source file is modified.",
    "PM2.5 = `Raw Conc.` (µg/m³). `NowCast Conc.` is kept only as nowcast_ugm3 for reference and is never used as the measurement.",
    "`Date (LT)` is parsed with the strict format %Y-%m-%d %I:%M %p; the original string is kept in ts_original. "
    "Local time is localised to Asia/Bishkek (UTC+6, no daylight saving) and also stored as UTC. "
    "The source does not say whether the stamp is the start or end of the hour; it is used as given and never shifted.",
    "`QC Name` is kept as qc_name_original and standardised to VALID / INVALID / MISSING / SUSPECT (else UNKNOWN).",
    "-999 (and non-numeric values) become NaN, never 0 (flag_sentinel_999, flag_missing_value).",
    "Negative values are kept in pm25_raw_ugm3 and flagged flag_neg_noise; pm25_model_ugm3 is clipped at 0.",
    "pm25_model_ugm3 is filled only for hours whose primary row has QC VALID, a real value, the expected unit and a parsed timestamp; "
    "INVALID, SUSPECT and MISSING rows stay in the audit dataset with their flags but have no model value.",
    "Duplicate timestamps: if exactly one VALID row, it is the primary; identical VALID rows keep the first; VALID rows with different values "
    "are a conflict: all are flagged DUP_CONFLICT and excluded from the primary series (no guessing).",
    "High readings are flagged (flag_high_value at the configured threshold; flag_local_drop / flag_local_spike relative to the surrounding "
    "valid hours) and never deleted.",
    "The hourly table spans first to last timestamp with a row for every hour; hours without a source row are NO_ROW. Measurements are never shifted.",
    "Gap runs, irregular 23/25-row days and duplicates are reported, not repaired.",
    "Interpolation (linear, gaps of at most 3 h with a valid value on both sides) exists only in pm25_feature_interp_ugm3, flagged is_imputed; "
    "targets (pm25_model_ugm3) are never interpolated.",
]


def _rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def _atomic_write(path: Path, writer) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        writer(Path(tmp))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_parquet(df: pd.DataFrame, path: Path) -> None:
    _atomic_write(path, lambda p: df.to_parquet(p, engine="pyarrow", compression="snappy", index=False))


def write_csv(df: pd.DataFrame, path: Path) -> None:
    _atomic_write(path, lambda p: df.to_csv(p, index=False, lineterminator="\n", encoding="utf-8"))


def write_text(text: str, path: Path) -> None:
    _atomic_write(path, lambda p: p.write_text(text, encoding="utf-8", newline="\n"))


def _high_share_periods(ym: dict, threshold: float = 30.0) -> list[str]:
    """Contiguous runs of months whose share of valid readings below 5 ug/m3 is >= threshold."""
    months = sorted(ym)
    runs, cur = [], []
    for m in months:
        s = ym[m]["share_below_5"]
        if s is not None and s >= threshold:
            cur.append(m)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return [r[0] if len(r) == 1 else f"{r[0]}..{r[-1]}" for r in runs]


def limitations_from(st: dict) -> list[str]:
    ym = st["by_year_month"]
    winter = ("11", "12", "01", "02", "03")
    early_w = [v["share_below_5"] for k, v in ym.items() if k < "2023-11" and k[5:] in winter]
    late_w = [v["share_below_5"] for k, v in ym.items() if "2023-11" <= k <= "2024-03" and k[5:] in winter]
    items = [
        "Single monitoring site; hourly values only. No coordinates are in the files (the Embassy location is approximate elsewhere in the project).",
        "The timestamp convention (hour start or end) is not stated in the source; the 1-hour offset seen against the 2021 Clarity data (Stage 3) is not resolved here.",
        "Conflicting duplicate timestamps are excluded rather than resolved; the cause is not documented in the source "
        f"(US daylight-saving dates in the data: row counts {st['us_dst_dates_row_counts']}).",
        "QC `Valid` is the provider's flag and the measurement behaviour is not homogeneous over time. "
        f"Minimum valid raw value by year: {st['min_valid_raw_by_year']} (2019 never goes below 5 µg/m³; later years contain near-zero and "
        f"negative readings). Months where at least 30 % of valid readings are below 5 µg/m³: {_high_share_periods(ym)}. Low shares are "
        "partly natural in clean-air summers, so compare winter with winter: Nov–Mar months before 2023-11 range "
        f"{min(early_w) if early_w else 'n/a'}–{max(early_w) if early_w else 'n/a'} %, while 2023-11 to 2024-03 range "
        f"{min(late_w) if late_w else 'n/a'}–{max(late_w) if late_w else 'n/a'} %. In some hours near-zero values alternate with ~150 µg/m³ "
        "values (e.g. 2024-02-29 22:00). These rows are flagged where detectable (flag_local_drop) but NOT removed or reclassified. "
        "The cause is unverified (possible instrument or reporting change); the periods need review before further modelling.",
        "Local drop/spike flags only catch isolated values relative to neighbouring hours; they cannot detect an entire low-reading period.",
        "The archive holds yearly 'YTD' files; the 2020 and 2021 files carry an older modification date (Jul 2022) than the others (Jul 2024), "
        "so later quality-control revisions, if any, may not be reflected in every year.",
        "Gaps are reported but not imputed in the target; feature interpolation is limited to short gaps.",
        "Redistribution terms: the Zenodo record is CC BY 4.0, but the original provider's terms for the Embassy data are not documented in the record.",
    ]
    return items


def run(
    *,
    zenodo_manifest: Path = ZENODO_MANIFEST,
    interim_dir: Path | None = None,
    processed_dir: Path | None = None,
    manifest_path: Path = PROCESSING_MANIFEST,
    skip_extract: bool = False,
    cfg: ec.CleaningConfig | None = None,
    out=print,
) -> int:
    cfg = cfg or ec.CleaningConfig()
    interim = interim_dir or REPO_ROOT / "data" / "interim" / "embassy"
    processed = processed_dir or REPO_ROOT / "data" / "processed" / "embassy"
    zm = mf.load_manifest(zenodo_manifest)
    if zm is None:
        out(f"ERROR: Zenodo manifest not found: {zenodo_manifest}. Run `python -m pipeline.ingest_zenodo` first.")
        return 2
    entry = next((f for f in zm["files"] if f["filename"] == ARCHIVE_NAME), None)
    if entry is None or entry.get("download_status") not in ("downloaded_verified", "already_present_verified"):
        out(f"ERROR: {ARCHIVE_NAME} is not a verified download in {zenodo_manifest}.")
        return 2
    archive = REPO_ROOT / entry["local_path"]
    previous = mf.load_manifest(manifest_path) or {}

    # ---- 1. verify + extract
    out(f"Verifying archive {archive.name} against the Zenodo manifest ...")
    calc = ex.verify_archive(archive, entry["calculated_checksums"]["sha256"], entry["calculated_checksums"]["md5"])
    ext_dir = interim / "extracted"
    extracted = ex.extract_archive(archive, ext_dir) if not skip_extract else []
    if skip_extract:
        extracted = [
            {"filename": p.name, "size_bytes": p.stat().st_size, **mf.hash_file(p), "status": "existing_not_rechecked",
             "local_path": p.as_posix()} for p in sorted(ext_dir.glob("*.csv"))
        ]
    prev_ext = {e["filename"]: e for e in previous.get("extraction", {}).get("files", [])}
    now = mf.utc_now()
    for e in extracted:
        old = prev_ext.get(e["filename"])
        e["extracted_at_utc"] = old["extracted_at_utc"] if old and old.get("sha256") == e["sha256"] else now
        e["local_path"] = _rel(Path(e["local_path"]))
    out(f"Extracted/verified {len(extracted)} files into {_rel(ext_dir)}")

    # ---- 2. clean
    source = ec.read_source_csvs(ext_dir)
    rows_per_file = source.groupby("source_file").size().to_dict()
    for e in extracted:
        e["rows_read"] = int(rows_per_file.get(e["filename"], 0))
    rows = ec.clean_rows(source, cfg)
    hourly = ec.build_hourly(rows, cfg)
    out(f"Rows read: {len(rows):,}; hourly timeline: {len(hourly):,} hours")

    # ---- 3. report
    st = er.compute_stats(rows, hourly)
    limitations = limitations_from(st)
    md = er.render_markdown(st, cfg.as_dict(), limitations, CLEANING_RULES)
    reports = interim / "reports"
    write_text(md, reports / "embassy_validation_report.md")
    write_text(json.dumps(st, indent=2, ensure_ascii=False, default=str) + "\n", reports / "embassy_validation_stats.json")
    write_csv(ec.find_gaps(hourly), reports / "embassy_gaps.csv")
    write_csv(ec.find_gaps(hourly, only_missing_rows=True), reports / "embassy_missing_timestamp_runs.csv")
    irr = er.irregular_days(hourly)
    write_csv(irr.assign(missing_hours=irr["missing_hours"].astype(str), duplicated_hours=irr["duplicated_hours"].astype(str)),
              reports / "embassy_irregular_days.csv")
    write_csv(er.duplicate_table(rows), reports / "embassy_duplicates.csv")

    # ---- 4. data outputs
    cleaned_path = interim / "cleaned" / "embassy_pm25_cleaned_rows.parquet"
    hourly_parquet = processed / "embassy_pm25_hourly.parquet"
    hourly_csv = processed / "embassy_pm25_hourly.csv"
    write_parquet(rows, cleaned_path)
    write_parquet(hourly, hourly_parquet)
    write_csv(hourly, hourly_csv)

    outputs = []
    for p, fmt, n, role in [
        (cleaned_path, "parquet", len(rows), "auditable row-level dataset (all source rows, original fields, flags)"),
        (hourly_parquet, "parquet", len(hourly), "primary analytical hourly table"),
        (hourly_csv, "csv", len(hourly), "hourly table, CSV copy"),
        (reports / "embassy_validation_report.md", "markdown", None, "validation report"),
        (reports / "embassy_validation_stats.json", "json", None, "validation statistics"),
        (reports / "embassy_gaps.csv", "csv", None, "runs of hours without a valid value"),
        (reports / "embassy_missing_timestamp_runs.csv", "csv", None, "runs of hours without any source row"),
        (reports / "embassy_irregular_days.csv", "csv", None, "local dates with a row count other than 24"),
        (reports / "embassy_duplicates.csv", "csv", None, "rows sharing a timestamp"),
    ]:
        outputs.append({"path": _rel(p), "format": fmt, "role": role, "rows": n, "size_bytes": p.stat().st_size,
                        "sha256": mf.hash_file(p, ("sha256",))["sha256"]})

    # ---- 5. processing manifest (the Zenodo manifest is left untouched)
    manifest = {
        "schema_version": 1,
        "stage": "embassy_pm25_processing",
        "generated_at_utc": mf.utc_now(),
        "processing": {
            "script": SCRIPT, "version": ec.PROCESSING_VERSION, "python": platform.python_version(),
            "pandas": pd.__version__, "pyarrow": pyarrow.__version__, "extraction_tool": ex.tar_version(),
        },
        "source_archive": {
            "filename": ARCHIVE_NAME, "local_path": entry["local_path"], "zenodo_record_id": zm["record"]["record_id"],
            "doi": zm["record"]["doi"], "licence": zm["record"]["licence"], "size_bytes": entry["size_bytes"],
            "zenodo_checksum": entry["zenodo_checksum"], "md5": calc["md5"], "sha256": calc["sha256"],
            "verified_against_zenodo_manifest": True, "zenodo_manifest": _rel(zenodo_manifest),
            "provenance_review_required": entry["provenance"]["review_required"],
        },
        "extraction": {"output_dir": _rel(ext_dir), "files": extracted},
        "cleaning_config": cfg.as_dict(),
        "cleaning_rules": CLEANING_RULES,
        "outputs": outputs,
        "dataset": {
            "timezone": ec.TIMEZONE, "first_timestamp_local": st["date_range"]["first_local"],
            "last_timestamp_local": st["date_range"]["last_local"], "source_rows": st["rows_read"],
            "hours_in_timeline": st["hours_in_timeline"], "valid_hours": st["valid_hours"],
            "missing_timestamps": st["missing_timestamps"], "duplicate_conflict_hours": st["duplicate_conflicts"],
            "quality_flag_counts_hourly": st["hourly_quality_flag_counts"],
        },
        "known_limitations": limitations,
    }
    mf.write_manifest_atomic(manifest_path, manifest)
    out(f"Processing manifest written: {_rel(manifest_path)}")
    for o in outputs:
        out(f"  {o['size_bytes']:>10,}  {o['path']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--skip-extract", action="store_true", help="reuse files already in data/interim/embassy/extracted")
    a = p.parse_args(argv)
    try:
        return run(skip_extract=a.skip_extract)
    except (ex.ExtractionError, ec.CleaningError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
