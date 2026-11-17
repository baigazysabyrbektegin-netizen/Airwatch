import pytest

from pipeline import ingest_zenodo as iz
from pipeline.tests.conftest import FILES, FakeZenodo, make_record, md5, read_json


def _file(name: str, data: bytes | None = None) -> iz.ZenodoFile:
    data = FILES[name] if data is None else data
    return iz.ZenodoFile(name, len(data), "md5", md5(data), f"https://zenodo.test/api/records/1/files/{name}/content")


# ---------- metadata ----------
def test_list_files_reads_official_urls_sizes_checksums():
    files = iz.list_files(make_record())
    assert {f.key for f in files} == set(FILES)
    f = next(f for f in files if f.key == "roads.rar")
    assert f.size == len(FILES["roads.rar"]) and f.algo == "md5" and f.digest == md5(FILES["roads.rar"])
    assert f.url == "https://zenodo.test/api/records/1/files/roads.rar/content"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(files=[]),
        lambda r: r["files"][0].pop("checksum"),
        lambda r: r["files"][0].update(key="../evil.txt"),
        lambda r: r["files"][0].update(checksum="nonsense"),
        lambda r: r["files"].append(dict(r["files"][0])),  # duplicate name
    ],
)
def test_list_files_rejects_bad_metadata(mutate):
    rec = make_record()
    mutate(rec)
    with pytest.raises(iz.MetadataError):
        iz.list_files(rec)


def test_fetch_record_retries_then_succeeds(make_client):
    fz = FakeZenodo()
    calls = {"n": 0}
    orig = fz.__call__

    def flaky(request):
        if request.url.path == "/api/records/1":
            calls["n"] += 1
            if calls["n"] < 3:
                import httpx

                return httpx.Response(503)
        return orig(request)

    rec = iz.fetch_record(make_client(flaky), "1", retries=4, sleep=lambda s: None)
    assert rec["id"] == 1 and calls["n"] == 3


def test_fetch_record_404_fails_clearly(make_client):
    fz = FakeZenodo()
    fz.metadata_status = 404
    with pytest.raises(iz.MetadataError, match="HTTP 404"):
        iz.fetch_record(make_client(fz), "1", retries=1, sleep=lambda s: None)


# ---------- downloads ----------
def test_download_streams_verifies_and_leaves_no_part(tmp_path, fake, make_client):
    res = iz.download_file(make_client(fake), _file("roads.rar"), tmp_path)
    assert res.status == "downloaded_verified"
    assert (tmp_path / "roads.rar").read_bytes() == FILES["roads.rar"]
    assert not (tmp_path / "roads.rar.part").exists()
    assert res.calculated["md5"] == md5(FILES["roads.rar"]) and len(res.calculated["sha256"]) == 64


def test_no_token_and_user_agent_sent(tmp_path, fake, make_client):
    iz.download_file(make_client(fake), _file("roads.rar"), tmp_path)
    for r in fake.requests:
        assert "authorization" not in {k.lower() for k in r.headers}
        assert "access_token" not in str(r.url)


def test_resume_uses_range_and_completes(tmp_path, fake, make_client):
    data = FILES["roads.rar"]
    (tmp_path / "roads.rar.part").write_bytes(data[:1234])
    res = iz.download_file(make_client(fake), _file("roads.rar"), tmp_path)
    assert res.resumed is True
    assert fake.file_requests()[0].headers["Range"] == "bytes=1234-"
    assert (tmp_path / "roads.rar").read_bytes() == data


def test_resume_falls_back_when_server_ignores_range(tmp_path, fake, make_client):
    fake.support_range = False
    (tmp_path / "roads.rar.part").write_bytes(b"garbage-prefix")
    res = iz.download_file(make_client(fake), _file("roads.rar"), tmp_path)
    assert res.resumed is False
    assert (tmp_path / "roads.rar").read_bytes() == FILES["roads.rar"]


def test_oversized_part_is_discarded(tmp_path, fake, make_client):
    (tmp_path / "roads.rar.part").write_bytes(b"x" * 99999)
    iz.download_file(make_client(fake), _file("roads.rar"), tmp_path)
    assert (tmp_path / "roads.rar").read_bytes() == FILES["roads.rar"]


def test_retries_transient_503(tmp_path, fake, make_client):
    fake.fail_first["roads.rar"] = 2
    res = iz.download_file(make_client(fake), _file("roads.rar"), tmp_path, retries=3, sleep=lambda s: None)
    assert res.attempts == 3 and res.status == "downloaded_verified"


def test_gives_up_after_retries_and_keeps_partial_message(tmp_path, fake, make_client):
    fake.fail_first["roads.rar"] = 99
    with pytest.raises(iz.DownloadError, match="attempts"):
        iz.download_file(make_client(fake), _file("roads.rar"), tmp_path, retries=1, sleep=lambda s: None)
    assert not (tmp_path / "roads.rar").exists()


def test_http_404_is_not_retried(tmp_path, fake, make_client):
    f = iz.ZenodoFile("missing.bin", 3, "md5", md5(b"abc"), "https://zenodo.test/api/records/1/files/missing.bin/content")
    with pytest.raises(iz.DownloadError, match="HTTP 404"):
        iz.download_file(make_client(fake), f, tmp_path)
    assert len(fake.file_requests()) == 1


def test_checksum_mismatch_fails_and_removes_partial(tmp_path, fake, make_client):
    fake.corrupt.add("roads.rar")
    with pytest.raises(iz.IntegrityError, match="Verification failed"):
        iz.download_file(make_client(fake), _file("roads.rar"), tmp_path)
    assert not (tmp_path / "roads.rar").exists()
    assert not (tmp_path / "roads.rar.part").exists()


def test_existing_verified_file_is_not_downloaded_again(tmp_path, fake, make_client):
    (tmp_path / "roads.rar").write_bytes(FILES["roads.rar"])
    res = iz.download_file(make_client(fake), _file("roads.rar"), tmp_path)
    assert res.status == "already_present_verified"
    assert fake.file_requests() == []


def test_existing_mismatched_file_is_never_overwritten(tmp_path, fake, make_client):
    target = tmp_path / "roads.rar"
    target.write_bytes(b"someone edited me")
    with pytest.raises(iz.IntegrityError, match="Refusing to overwrite"):
        iz.download_file(make_client(fake), _file("roads.rar"), tmp_path)
    assert target.read_bytes() == b"someone edited me"
    assert fake.file_requests() == []


def test_force_replaces_existing_file(tmp_path, fake, make_client):
    target = tmp_path / "roads.rar"
    target.write_bytes(b"old")
    iz.download_file(make_client(fake), _file("roads.rar"), tmp_path, force=True)
    assert target.read_bytes() == FILES["roads.rar"]


# ---------- end to end ----------
def _run(tmp_path, client, **kw):
    out: list[str] = []
    args = dict(
        record_id="1",
        dest_dir=tmp_path / "raw",
        manifest_path=tmp_path / "manifest" / "m.json",
        dry_run=False,
        force=False,
        retries=1,
        timeout=5,
        client=client,
        sleep=lambda s: None,
        out=out.append,
    )
    args.update(kw)
    return iz.run(**args), out


def test_dry_run_lists_but_writes_nothing(tmp_path, fake, make_client):
    code, out = _run(tmp_path, make_client(fake), dry_run=True)
    assert code == 0
    assert not (tmp_path / "raw").exists() and not (tmp_path / "manifest").exists()
    assert fake.file_requests() == []
    text = "\n".join(out)
    assert "roads.rar" in text and f"{sum(len(v) for v in FILES.values()):,} bytes" in text


def test_full_run_writes_complete_manifest(tmp_path, fake, make_client):
    code, _ = _run(tmp_path, make_client(fake))
    assert code == 0
    m = read_json(tmp_path / "manifest" / "m.json")
    rec = m["record"]
    assert rec["record_id"] == 1 and rec["doi"] == "10.5281/zenodo.1"
    assert rec["licence"] == {"id": "cc-by-4.0"} and rec["version"] is None
    assert rec["retrieved_at_utc"] and rec["creators"][0]["name"] == "Doe, Jane"
    assert m["files_count"] == 3 and m["files_failed"] == 0
    by = {e["filename"]: e for e in m["files"]}
    e = by["roads.rar"]
    assert e["download_status"] == "downloaded_verified" and e["checksum_verified"] is True
    assert e["extension"] == "rar" and e["size_bytes"] == len(FILES["roads.rar"])
    assert e["zenodo_checksum"] == {"algorithm": "md5", "value": md5(FILES["roads.rar"])}
    assert len(e["calculated_checksums"]["sha256"]) == 64 and e["downloaded_at_utc"]
    assert e["download_url"].endswith("/files/roads.rar/content")
    assert e["provenance"]["third_party_origin_indicated"] is True  # description says openstreetmaps
    assert by["unlisted.txt"]["provenance"]["record_text_near_file"] is None
    assert by["unlisted.txt"]["provenance"]["review_required"] is True
    assert e["licence"]["file_specific_licence"] is None
    # raw files untouched and not extracted
    assert sorted(p.name for p in (tmp_path / "raw").iterdir()) == sorted(FILES)


def test_rerun_is_idempotent_and_preserves_download_time(tmp_path, fake, make_client):
    _run(tmp_path, make_client(fake))
    first = {e["filename"]: e["downloaded_at_utc"] for e in read_json(tmp_path / "manifest" / "m.json")["files"]}
    fake.requests.clear()
    code, _ = _run(tmp_path, make_client(fake))
    assert code == 0 and fake.file_requests() == []
    again = read_json(tmp_path / "manifest" / "m.json")["files"]
    assert all(e["download_status"] == "already_present_verified" for e in again)
    assert {e["filename"]: e["downloaded_at_utc"] for e in again} == first


def test_failed_file_gives_exit_1_and_recorded_error(tmp_path, fake, make_client):
    fake.corrupt.add("roads.rar")
    code, out = _run(tmp_path, make_client(fake))
    assert code == 1
    m = read_json(tmp_path / "manifest" / "m.json")
    assert m["files_failed"] == 1
    bad = next(e for e in m["files"] if e["filename"] == "roads.rar")
    assert bad["download_status"] == "failed" and "Verification failed" in bad["error"]
    assert bad["checksum_verified"] is False
    good = next(e for e in m["files"] if e["filename"] == "embassy.csv")
    assert good["download_status"] == "downloaded_verified"
    assert any("FAILED" in line for line in out)


def test_metadata_failure_exit_2_and_no_manifest(tmp_path, fake, make_client):
    fake.metadata_status = 500
    code, out = _run(tmp_path, make_client(fake))
    assert code == 2
    assert not (tmp_path / "manifest").exists()
    assert any("ERROR" in line for line in out)


def test_record_without_licence_is_recorded_as_unknown(tmp_path, make_client):
    fz = FakeZenodo(record=make_record(license_id=None))
    _run(tmp_path, make_client(fz))
    m = read_json(tmp_path / "manifest" / "m.json")
    assert m["record"]["licence"] is None
    assert m["files"][0]["licence"]["record_level_licence_id"] is None
    assert m["files"][0]["licence"]["attribution_required"] is None
