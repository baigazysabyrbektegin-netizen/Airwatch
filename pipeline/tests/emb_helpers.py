"""Synthetic Embassy-style CSV fixtures. Tests never touch the real dataset."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from pathlib import Path

from pipeline import embassy_clean as ec

HEADER = ec.EXPECTED_COLUMNS


def row(ts: datetime, raw, qc: str = "Valid", now="10.0", unit: str = "UG/M3", ts_text: str | None = None) -> list[str]:
    """One CSV row as the source writes it (12-hour clock text, zero-padded fields)."""
    return [
        "Bishkek", "PM2.5 - Principal", ts_text or ts.strftime("%Y-%m-%d %I:%M %p"), f"{ts.year:04d}", f"{ts.month:02d}",
        f"{ts.day:02d}", f"{ts.hour:02d}", str(now), "50", "Moderate", str(raw), unit, "1 Hr", qc,
    ]


def hours(start: datetime, n: int) -> list[datetime]:
    return [start + timedelta(hours=i) for i in range(n)]


def csv_text(rows: list[list[str]]) -> str:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(HEADER)
    w.writerows(rows)
    return buf.getvalue()


def write_csv(directory: Path, name: str, rows: list[list[str]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_bytes(csv_text(rows).encode("utf-8"))
    return p


def clean(tmp_path: Path, rows: list[list[str]], cfg: ec.CleaningConfig | None = None, name: str = "Bishkek_PM2.5_2020_YTD.csv"):
    """Write rows to a CSV, read it back and run the full row + hourly cleaning."""
    write_csv(tmp_path / "src", name, rows)
    source = ec.read_source_csvs(tmp_path / "src")
    audit = ec.clean_rows(source, cfg)
    return source, audit, ec.build_hourly(audit, cfg)


T0 = datetime(2020, 1, 10, 0, 0)
