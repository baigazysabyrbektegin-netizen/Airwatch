import geopandas as gpd


def test_planner_overview_reports_realistic_coverage(client):
    response = client.get("/api/planner/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["existing_clarity_stations"] == 2
    assert 0 <= body["covered_cell_pct"] <= 100
    assert body["data_kind"] == "decision-support estimate"


def test_planner_candidates_are_ranked_and_separated(client):
    response = client.get("/api/planner/candidates", params={"n": 3, "min_separation_km": 0})
    assert response.status_code == 200
    candidates = response.json()["candidates"]
    assert len(candidates) == 3
    assert [c["rank"] for c in candidates] == [1, 2, 3]
    assert candidates[0]["priority_score"] >= candidates[-1]["priority_score"]
    assert candidates[0]["candidate_id"].startswith("cell-")


def test_planner_rejects_zero_total_weights(client):
    response = client.get("/api/planner/candidates", params={"exposure_weight": 0, "source_weight": 0})
    assert response.status_code == 422


def test_planner_recalculate_validates_and_returns_parameters(client):
    response = client.post("/api/planner/recalculate", json={
        "n": 2, "coverage_radius_km": 2, "min_separation_km": 0,
        "exposure_weight": 0.8, "source_weight": 0.2,
    })
    assert response.status_code == 200
    assert response.json()["parameters"]["n"] == 2


def test_planner_minimum_separation_can_reduce_candidates(store):
    from app.services import planner

    result = planner.candidates(store, planner.PlannerParameters(n=3, min_separation_km=100))
    assert len(result["candidates"]) == 1


def test_planner_reports_invalid_grid_as_data_unavailable(client, store):
    store.grid = lambda: gpd.GeoDataFrame({"frac_in_kgz_polygons": [1.0]})

    response = client.get("/api/planner/overview")

    assert response.status_code == 503
    assert response.json()["error"] == "data_unavailable"

