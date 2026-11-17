import json

from fastapi.testclient import TestClient

from app.api.deps import get_store
from app.main import create_app
from app.services import data_store
from app.services.data_store import DataStore


# ------------------------------------------------------------------ /api/metadata
def test_metadata_is_assembled_from_the_manifests(client):
    b = client.get("/api/metadata").json()
    assert b["unavailable_manifests"] == [] and set(b["available_manifests"]) == {"zenodo", "embassy", "clarity", "gis", "weather"}
    assert b["source_record"]["doi"] == "10.5281/zenodo.12720883" and b["source_record"]["licence"] == {"id": "cc-by-4.0"}
    d = {x["id"]: x for x in b["datasets"]}
    assert {"embassy_pm25", "clarity_pm25", "camx_pm25", "emissions_2018", "population", "roads", "context_layers"} <= set(d)
    assert d["camx_pm25"]["data_kind"] == "MODELLED" and d["emissions_2018"]["vintage"] == "2018" and d["population"]["vintage"] == "2000-2019"
    assert d["embassy_pm25"]["data_kind"] == "measured" and d["embassy_pm25"]["vintage"].startswith("2019-02-06 to 2024-06-30")
    assert d["clarity_pm25"]["timestamp_assumptions"]["status"].startswith("ASSUMPTION") and d["clarity_pm25"]["provenance_review_required"] is True
    lim = b["limitations"]
    assert lim["embassy"] == ["Embassy limitation one", "QC Valid is not homogeneous over time"] and lim["clarity"] == ["Clarity limitation one"] and lim["gis"] == ["GIS limitation one"]
    assert b["licence"]["attribution_required"] is True and b["licence"]["attribution"] == "Guttikunda (2024)" and b["licence"]["notes"] == ["OSM ODbL note"]
    assert b["coordinate_reference"]["output_crs"] == "EPSG:4326"


def test_metadata_carries_the_mandatory_presentation_rules(client):
    rules = " ".join(client.get("/api/metadata").json()["presentation_rules"])
    for must in ("Embassy readings are measured historical", "low-cost sensor measurements", "CAMx is historical modelled", "2018 bottom-up inventory", "2019 modelled grid",
                 "1-hour offset", "potential measurement-regime issue", "approximate", "No current citywide sensor coverage"):
        assert must in rules, must
    bm = client.get("/api/metadata").json()["basemap"]
    assert bm["default_provider"] == "OpenFreeMap" and "No API key" in bm["usage"] and "OpenStreetMap" in bm["attribution"] and "fallback" in bm


def test_metadata_reports_missing_manifests_instead_of_failing(data_dir):
    (data_dir / "manifest" / "gis_processing.json").unlink()
    app = create_app()
    s = DataStore(data_dir)
    app.dependency_overrides[get_store] = lambda: s
    b = TestClient(app).get("/api/metadata").json()
    assert b["unavailable_manifests"] == ["data/manifest/gis_processing.json"] and "gis" not in b["available_manifests"]
    assert {x["id"] for x in b["datasets"]} == {"embassy_pm25", "clarity_pm25", "era5_weather"} and b["limitations"]["gis"] == []


# ------------------------------------------------------------------ missing and unreadable data
def test_missing_processed_files_give_informative_503s(empty_client):
    for url in ("/api/stations", "/api/measurements?station=C1", "/api/grid", "/api/layers/roads", "/api/layers/stations", "/api/layers/administrative_boundary"):
        r = empty_client.get(url)
        assert r.status_code in (503, 404), url
        if r.status_code == 503:
            b = r.json()
            assert b["error"] == "data_unavailable" and b["file"].startswith("data/processed/") and "pipeline" in b["hint"] and "not available" in b["message"]
            assert str(empty_client.app.dependency_overrides) is not None and "\\" not in b["file"]            # no absolute path leaks


def test_catalogue_marks_layers_unavailable_without_failing(empty_client):
    r = empty_client.get("/api/layers")
    assert r.status_code == 200
    layers = r.json()["layers"]
    assert all(x["available"] is False and x["feature_count"] is None and x["unavailable_reason"].startswith("missing:") for x in layers)


def test_one_missing_file_only_affects_its_endpoints(data_dir):
    (data_dir / "processed/gis/roads.parquet").unlink()
    app = create_app()
    s = DataStore(data_dir)
    app.dependency_overrides[get_store] = lambda: s
    c = TestClient(app)
    assert c.get("/api/layers/roads").status_code == 503 and c.get("/api/grid").status_code == 200 and c.get("/api/stations").status_code == 200
    cat = {x["id"]: x for x in c.get("/api/layers").json()["layers"]}
    assert cat["roads"]["available"] is False and cat["roads"]["feature_count"] is None and cat["camx_pm25"]["available"] is True


def test_unreadable_file_is_reported_not_crashed(data_dir):
    (data_dir / "processed/gis/airwatch_spatial_grid.parquet").write_bytes(b"not a parquet file")
    app = create_app()
    s = DataStore(data_dir)
    app.dependency_overrides[get_store] = lambda: s
    r = TestClient(app).get("/api/grid")
    assert r.status_code == 503 and "could not be read" in r.json()["message"]


def test_missing_values_never_become_zero_in_any_endpoint(client):
    # Cell 4 has no population; the Clarity day 2021-03-03 has too few hours; the Embassy gap hour has no value.
    cells = client.get("/api/grid", params={"fields": "cell_id,pop_2019"}).json()["features"]
    assert next(f for f in cells if f["id"] == 4)["properties"]["pop_2019"] is None
    day = client.get("/api/measurements", params={"station": "C1", "interval": "daily"}).json()["records"][2]
    assert day["pm25_ugm3"] is None
    gap = next(r for r in client.get("/api/measurements", params={"station": "us-embassy-bishkek", "include_gaps": "true"}).json()["records"] if r["quality_flag"] == "NO_ROW")
    assert gap["pm25_ugm3"] is None and gap["pm25_valid_ugm3"] is None


# ------------------------------------------------------------------ data architecture rules
def test_only_processed_data_and_manifests_are_ever_read(data_dir):
    allowed = ("processed/", "manifest/")
    assert all(p.startswith(allowed) for p in list(data_store.FILES.values()) + list(data_store.MANIFESTS.values()))
    s = DataStore(data_dir)
    for key in data_store.FILES:
        assert str(s.path(key).relative_to(data_dir)).replace("\\", "/").startswith("processed/")


def test_store_caches_files_and_does_not_modify_them(data_dir):
    s = DataStore(data_dir)
    before = {p: p.stat().st_mtime_ns for p in data_dir.rglob("*") if p.is_file()}
    a, b = s.grid(), s.grid()
    assert a is b                                                                            # read once, then served from memory
    s.clarity_hourly(); s.embassy_hourly(); s.roads(); s.context(); s.admin(); s.manifest("gis")
    assert {p: p.stat().st_mtime_ns for p in data_dir.rglob("*") if p.is_file()} == before   # nothing was written or touched
    s.clear()
    assert s.grid() is not a


def test_responses_are_valid_json_without_nan(client):
    for url in ("/api/grid", "/api/layers/roads", "/api/stations", "/api/measurements?station=C1&interval=daily", "/api/layers/population_grid"):
        text = client.get(url).text
        assert "NaN" not in text and "Infinity" not in text, url
        json.loads(text)


def test_weather_manifest_is_optional_for_metadata(data_dir):
    (data_dir / "manifest" / "weather_processing.json").unlink()
    app = create_app()
    s = DataStore(data_dir)
    app.dependency_overrides[get_store] = lambda: s
    b = TestClient(app).get("/api/metadata").json()
    assert b["unavailable_manifests"] == [] and "era5_weather" not in {x["id"] for x in b["datasets"]}
