from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["python"].startswith("3.")


def test_health_returns_no_measurements():
    # The foundation must never serve placeholder air-quality values.
    body = client.get("/api/health").json()
    assert not any("pm" in k.lower() for k in body)


def test_cors_allows_vite_dev_origin():
    r = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
