from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def dist(tmp_path) -> Path:
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (d / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (d / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside dist", encoding="utf-8")
    return d


def test_serves_index_and_spa_fallback(dist):
    client = TestClient(create_app(static_dir=dist))
    for path in ("/", "/workspace", "/planner/deep/link"):
        r = client.get(path)
        assert r.status_code == 200
        assert "id=root" in r.text


def test_serves_built_files(dist):
    client = TestClient(create_app(static_dir=dist))
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/favicon.svg").text == "<svg/>"


def test_api_still_wins_and_unknown_api_is_json_404(dist):
    client = TestClient(create_app(static_dir=dist))
    assert client.get("/api/health").json()["status"] == "ok"
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_does_not_escape_dist(dist):
    client = TestClient(create_app(static_dir=dist))
    r = client.get("/..%2Fsecret.txt")
    assert "outside dist" not in r.text


def test_no_build_means_no_frontend_routes(tmp_path):
    client = TestClient(create_app(static_dir=tmp_path / "missing"))
    assert client.get("/").status_code == 404
    assert client.get("/api/health").status_code == 200
