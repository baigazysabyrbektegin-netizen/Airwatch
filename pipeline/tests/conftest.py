"""Shared fakes: an in-memory Zenodo server built on httpx.MockTransport (no network)."""

from __future__ import annotations

import hashlib
import json
import re

import httpx
import pytest

DESCRIPTION_HTML = (
    "<p>Bishkek data.</p><p>GIS roads: File: roads.rar (shapefiles extracted from openstreetmaps)</p>"
    "<p>Monitoring: File: embassy.csv (US Embassy hourly PM2.5)</p>"
)
FILES = {
    "roads.rar": b"R" * 5000,
    "embassy.csv": b"site,pm25\n" + b"1,12.5\n" * 400,
    "unlisted.txt": b"nothing describes me",
}


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def make_record(files: dict[str, bytes] | None = None, *, license_id: str | None = "cc-by-4.0") -> dict:
    files = FILES if files is None else files
    meta = {
        "title": "Test record",
        "publication_date": "2024-01-02",
        "description": DESCRIPTION_HTML,
        "creators": [{"name": "Doe, Jane"}],
        "resource_type": {"type": "dataset"},
        "access_right": "open",
    }
    if license_id:
        meta["license"] = {"id": license_id}
    return {
        "id": 1,
        "doi": "10.5281/zenodo.1",
        "conceptdoi": "10.5281/zenodo.0",
        "doi_url": "https://doi.org/10.5281/zenodo.1",
        "created": "2024-01-02T00:00:00+00:00",
        "updated": "2024-02-02T00:00:00+00:00",
        "metadata": meta,
        "files": [
            {
                "key": k,
                "size": len(v),
                "checksum": f"md5:{md5(v)}",
                "links": {"self": f"https://zenodo.test/api/records/1/files/{k}/content"},
            }
            for k, v in files.items()
        ],
    }


class FakeZenodo:
    """Callable httpx handler. Records every request; supports Range and injected failures."""

    def __init__(self, record: dict | None = None, contents: dict[str, bytes] | None = None):
        self.contents = dict(FILES if contents is None else contents)
        self.record = record or make_record(self.contents)
        self.requests: list[httpx.Request] = []
        self.support_range = True
        self.fail_first: dict[str, int] = {}  # filename -> number of 503s to return first
        self.corrupt: set[str] = set()
        self.metadata_status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/api/records/1":
            if self.metadata_status != 200:
                return httpx.Response(self.metadata_status)
            return httpx.Response(200, json=self.record)
        m = re.match(r"/api/records/1/files/(.+)/content", path)
        if not m or m.group(1) not in self.contents:
            return httpx.Response(404)
        name = m.group(1)
        if self.fail_first.get(name, 0) > 0:
            self.fail_first[name] -= 1
            return httpx.Response(503)
        data = self.contents[name]
        if name in self.corrupt:
            data = b"X" * len(data)
        rng = request.headers.get("Range")
        if rng and self.support_range:
            start = int(re.match(r"bytes=(\d+)-", rng).group(1))
            return httpx.Response(206, content=data[start:], headers={"Content-Range": f"bytes {start}-{len(data)-1}/{len(data)}"})
        return httpx.Response(200, content=data)

    def file_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if "/files/" in r.url.path]


@pytest.fixture
def fake() -> FakeZenodo:
    return FakeZenodo()


@pytest.fixture
def make_client():
    clients: list[httpx.Client] = []

    def _make(handler):
        c = httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": "test-agent/1"})
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.close()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # Retry back-off must never slow the tests down.
    monkeypatch.setattr("time.sleep", lambda s: None)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))
