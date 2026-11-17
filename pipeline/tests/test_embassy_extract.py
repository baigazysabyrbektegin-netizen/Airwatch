import shutil
import subprocess
from types import SimpleNamespace

import pytest

from pipeline import embassy_extract as ex
from pipeline import manifest as mf

pytestmark = pytest.mark.skipif(shutil.which("tar") is None, reason="needs the system tar")


def make_archive(tmp_path, files: dict[str, bytes], name="a.tar"):
    src = tmp_path / "srcfiles"
    src.mkdir(exist_ok=True)
    for k, v in files.items():
        (src / k).write_bytes(v)
    out = tmp_path / name
    subprocess.run(["tar", "-cf", str(out), "-C", str(src), *files], check=True)
    return out


def test_list_members_names_and_sizes(tmp_path):
    arc = make_archive(tmp_path, {"one.csv": b"abc", "two.csv": b"x" * 1000})
    assert ex.list_members(arc) == [ex.Member("one.csv", 3), ex.Member("two.csv", 1000)]


@pytest.mark.parametrize("bad", ["../evil.csv", "/abs/evil.csv", "sub/inner.csv", "C:evil.csv"])
def test_unsafe_member_names_are_rejected(monkeypatch, tmp_path, bad):
    listing = f"-rw-r--r--  0 0      0      10 Jul 05  2024 {bad}\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=listing, stderr=""))
    with pytest.raises(ex.ExtractionError, match="Unsafe"):
        ex.list_members(tmp_path / "x.rar")


def test_list_members_fails_clearly_on_bad_archive(tmp_path):
    p = tmp_path / "broken.rar"
    p.write_bytes(b"not an archive")
    with pytest.raises(ex.ExtractionError):
        ex.list_members(p)


def test_extract_writes_files_with_checksums_and_leaves_archive_untouched(tmp_path):
    arc = make_archive(tmp_path, {"one.csv": b"abc", "two.csv": b"def"})
    before = arc.read_bytes()
    out = tmp_path / "out"
    res = ex.extract_archive(arc, out)
    assert {r["filename"]: r["status"] for r in res} == {"one.csv": "extracted", "two.csv": "extracted"}
    assert (out / "one.csv").read_bytes() == b"abc"
    one = next(r for r in res if r["filename"] == "one.csv")
    assert one["sha256"] == mf.hash_file(out / "one.csv", ("sha256",))["sha256"] and one["size_bytes"] == 3
    assert arc.read_bytes() == before


def test_extract_is_idempotent(tmp_path):
    arc = make_archive(tmp_path, {"one.csv": b"abc"})
    out = tmp_path / "out"
    ex.extract_archive(arc, out)
    again = ex.extract_archive(arc, out)
    assert again[0]["status"] == "already_extracted_verified"


def test_extract_never_overwrites_different_existing_file(tmp_path):
    arc = make_archive(tmp_path, {"one.csv": b"abc"})
    out = tmp_path / "out"
    out.mkdir()
    (out / "one.csv").write_bytes(b"locally edited")
    with pytest.raises(ex.ExtractionError, match="refusing to overwrite"):
        ex.extract_archive(arc, out)
    assert (out / "one.csv").read_bytes() == b"locally edited"


def test_verify_archive_detects_checksum_mismatch(tmp_path):
    arc = make_archive(tmp_path, {"one.csv": b"abc"})
    good = mf.hash_file(arc, ("md5", "sha256"))
    assert ex.verify_archive(arc, good["sha256"], good["md5"]) == good
    with pytest.raises(ex.ExtractionError, match="SHA-256"):
        ex.verify_archive(arc, "0" * 64, None)
    with pytest.raises(ex.ExtractionError, match="MD5"):
        ex.verify_archive(arc, None, "0" * 32)
