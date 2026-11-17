"""End-to-end test of the Stage 2 orchestration on a tiny synthetic archive (no real data)."""

import json
import shutil
import subprocess
from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from pipeline import manifest as mf
from pipeline import process_embassy as pe
from pipeline.tests.emb_helpers import csv_text, hours, row

pytestmark = pytest.mark.skipif(shutil.which("tar") is None, reason="needs the system tar")


@pytest.fixture
def env(tmp_path):
    src = tmp_path / "files"
    src.mkdir()
    t = hours(datetime(2020, 1, 10, 0), 30)
    rows = [row(x, 20 + i) for i, x in enumerate(t) if i not in (7, 8)]
    rows += [row(t[3], 99)]                                        # conflicting duplicate
    rows.append(row(t[10], "-999.0", qc="Missing", now="-999.0"))  # duplicate hour with a missing value
    (src / "Bishkek_PM2.5_2020_YTD.csv").write_bytes(csv_text(rows).encode())
    archive = tmp_path / "raw" / pe.ARCHIVE_NAME
    archive.parent.mkdir()
    subprocess.run(["tar", "-cf", str(archive), "-C", str(src), "Bishkek_PM2.5_2020_YTD.csv"], check=True)
    h = mf.hash_file(archive, ("md5", "sha256"))
    zen = {
        "record": {"record_id": 1, "doi": "10.0/x", "licence": {"id": "cc-by-4.0"}},
        "files": [{
            "filename": pe.ARCHIVE_NAME, "local_path": str(archive), "download_status": "downloaded_verified",
            "size_bytes": archive.stat().st_size, "zenodo_checksum": {"algorithm": "md5", "value": h["md5"]},
            "calculated_checksums": h, "provenance": {"review_required": True},
        }],
    }
    zpath = tmp_path / "zenodo.json"
    zpath.write_text(json.dumps(zen), encoding="utf-8")
    return SimpleNamespace(archive=archive, zpath=zpath, tmp=tmp_path)


def run(env, **kw):
    msgs = []
    code = pe.run(
        zenodo_manifest=env.zpath, interim_dir=env.tmp / "interim", processed_dir=env.tmp / "processed",
        manifest_path=env.tmp / "manifest.json", out=msgs.append, **kw,
    )
    return code, msgs


def test_end_to_end_outputs_and_manifest(env):
    zen_before = env.zpath.read_bytes()
    arc_before = env.archive.read_bytes()
    code, _ = run(env)
    assert code == 0
    assert env.zpath.read_bytes() == zen_before and env.archive.read_bytes() == arc_before  # inputs untouched

    hourly = pd.read_parquet(env.tmp / "processed" / "embassy_pm25_hourly.parquet")
    assert len(hourly) == 30 and hourly["quality_flag"].value_counts().to_dict()["NO_ROW"] == 2
    assert (env.tmp / "processed" / "embassy_pm25_hourly.csv").exists()
    cleaned = pd.read_parquet(env.tmp / "interim" / "cleaned" / "embassy_pm25_cleaned_rows.parquet")
    assert len(cleaned) == 30   # 28 hourly rows + 1 conflicting duplicate + 1 duplicate with a missing value
    for f in ("embassy_validation_report.md", "embassy_validation_stats.json", "embassy_gaps.csv",
              "embassy_missing_timestamp_runs.csv", "embassy_irregular_days.csv", "embassy_duplicates.csv"):
        assert (env.tmp / "interim" / "reports" / f).exists(), f

    m = json.loads((env.tmp / "manifest.json").read_text(encoding="utf-8"))
    assert m["source_archive"]["filename"] == pe.ARCHIVE_NAME
    assert m["source_archive"]["sha256"] == mf.hash_file(env.archive, ("sha256",))["sha256"]
    assert m["source_archive"]["verified_against_zenodo_manifest"] is True
    assert m["processing"]["version"] and m["cleaning_rules"] and m["known_limitations"]
    ext = m["extraction"]["files"][0]
    assert ext["filename"] == "Bishkek_PM2.5_2020_YTD.csv" and ext["rows_read"] == 30 and ext["extracted_at_utc"]
    assert ext["sha256"] == mf.hash_file(env.tmp / "interim" / "extracted" / ext["filename"], ("sha256",))["sha256"]
    assert all(o["size_bytes"] > 0 and len(o["sha256"]) == 64 for o in m["outputs"])
    paths = {o["path"].rsplit("/", 1)[-1]: o for o in m["outputs"]}
    real = env.tmp / "processed" / "embassy_pm25_hourly.parquet"
    assert paths["embassy_pm25_hourly.parquet"]["sha256"] == mf.hash_file(real, ("sha256",))["sha256"]
    assert m["dataset"]["first_timestamp_local"].startswith("2020-01-10 00:00:00+06:00")
    assert m["dataset"]["valid_hours"] == hourly["quality_flag"].eq("VALID").sum()


def test_rerun_is_reproducible_and_keeps_extraction_time(env):
    run(env)
    first_m = json.loads((env.tmp / "manifest.json").read_text(encoding="utf-8"))
    hashes1 = {o["path"].rsplit("/", 1)[-1]: o["sha256"] for o in first_m["outputs"] if o["format"] != "markdown"}
    code, _ = run(env)
    assert code == 0
    second_m = json.loads((env.tmp / "manifest.json").read_text(encoding="utf-8"))
    hashes2 = {o["path"].rsplit("/", 1)[-1]: o["sha256"] for o in second_m["outputs"] if o["format"] != "markdown"}
    assert hashes1 == hashes2
    assert first_m["extraction"]["files"][0]["extracted_at_utc"] == second_m["extraction"]["files"][0]["extracted_at_utc"]
    assert second_m["extraction"]["files"][0]["status"] == "already_extracted_verified"


def test_refuses_archive_whose_checksum_changed(env):
    env.archive.write_bytes(env.archive.read_bytes() + b"tamper")
    with pytest.raises(Exception, match="differs from the Zenodo manifest"):
        run(env)
    assert not (env.tmp / "processed").exists()


def test_missing_zenodo_manifest_returns_error_code(env):
    env.zpath.unlink()
    code, msgs = run(env)
    assert code == 2 and any("Zenodo manifest not found" in m for m in msgs)


def test_unverified_archive_entry_is_refused(env):
    z = json.loads(env.zpath.read_text(encoding="utf-8"))
    z["files"][0]["download_status"] = "failed"
    env.zpath.write_text(json.dumps(z), encoding="utf-8")
    code, msgs = run(env)
    assert code == 2 and any("not a verified download" in m for m in msgs)
