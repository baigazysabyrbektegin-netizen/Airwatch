"""Verify and extract the Zenodo GIS archives (reusing the safe Stage 2 extractor)."""

from __future__ import annotations

from pathlib import Path

from pipeline import embassy_extract as ex

ARCHIVES = [
    "gis_bishkek-grids-pop.rar",
    "gis_bishkek_roads.rar",
    "gis_bishkek_adm0.rar",
    "gis_bishkek_multiplelayers.rar",
    "bishkek_gridded_emissions_2018.rar",
]
CAMX_FILE = "bishkek_camx_pm25_monthlyavg.csv"


def role_of(filename: str) -> str:
    n = filename.lower()
    if n.endswith((".shp", ".dbf", ".shx", ".prj", ".cpg")):
        return "shapefile component"
    if n.endswith(".kml"):
        return "kml layer"
    if n.endswith(".bna"):
        return "bna boundary (duplicate of the shapefile)"
    if n.endswith((".png", ".gif")):
        return "figure (not processed)"
    if n.endswith(".emi"):
        return "area emissions grid"
    if n.endswith(".ptemi"):
        return "point-source emissions"
    if "profile" in n and n.endswith(".csv"):
        return "CAMx temporal profile (not processed)"
    if n.endswith(".csv"):
        return "table"
    return "other"


def manifest_entry(zen: dict, name: str) -> dict:
    e = next((f for f in zen["files"] if f["filename"] == name), None)
    if e is None or e.get("download_status") not in ("downloaded_verified", "already_present_verified"):
        raise ex.ExtractionError(f"{name} is not a verified download in the Zenodo manifest.")
    return e


def extract_archives(zen: dict, repo_root: Path, out_dir: Path) -> list[dict]:
    """Checksum-verify each archive against the Zenodo manifest, then extract it into its own folder."""
    records = []
    for name in ARCHIVES:
        e = manifest_entry(zen, name)
        archive = (repo_root / e["local_path"]) if not Path(e["local_path"]).is_absolute() else Path(e["local_path"])
        calc = ex.verify_archive(archive, e["calculated_checksums"]["sha256"], e["calculated_checksums"]["md5"])
        folder = out_dir / archive.stem
        files = ex.extract_archive(archive, folder)
        for f in files:
            f["role"] = role_of(f["filename"])
        records.append({"archive": name, "size_bytes": e["size_bytes"], "sha256": calc["sha256"], "md5": calc["md5"],
                        "zenodo_checksum": e["zenodo_checksum"], "extracted_to": folder.as_posix(), "files": files,
                        "provenance_review_required": e["provenance"]["review_required"]})
    return records
