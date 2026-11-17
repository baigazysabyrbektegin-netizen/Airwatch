r"""Pack data/processed and data/manifest into one archive for a GitHub Release (used by the Docker build).

Run from the repository root after the pipeline:  backend\.venv\Scripts\python.exe scripts\pack_data.py
Prints the archive path and its SHA-256; put both into render.yaml (DATA_URL, DATA_SHA256).
"""

from __future__ import annotations

import argparse
import hashlib
import tarfile
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA = REPO_ROOT / "data"
PARTS = ("processed", "manifest")


def pack(out: Path) -> str:
    if not any(p.is_file() and p.name != ".gitkeep" for p in (DATA / "processed").rglob("*")):
        raise SystemExit("data/processed is empty: run the pipeline first (README, 'Clone and run').")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        for part in PARTS:
            tar.add(DATA / part, arcname=part)
    return hashlib.sha256(out.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "dist-data" / f"airwatch-data-{date.today():%Y%m%d}.tar.gz")
    args = ap.parse_args()
    sha = pack(args.out)
    print(f"archive: {args.out}\nsize:    {args.out.stat().st_size:,} bytes\nsha256:  {sha}")


if __name__ == "__main__":
    main()
