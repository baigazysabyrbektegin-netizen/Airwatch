import math


# ------------------------------------------------------------------ /api/grid
def test_grid_returns_geojson_with_all_cells_and_field_metadata(client):
    r = client.get("/api/grid")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/geo+json")
    b = r.json()
    assert b["type"] == "FeatureCollection" and len(b["features"]) == 6 and b["metadata"]["total"] == 6 and b["metadata"]["grid_cells_in_dataset"] == 6
    f = b["features"][0]
    assert f["geometry"]["type"] == "Polygon" and f["id"] == 1 and f["properties"]["cell_id"] == 1 and f["properties"]["pop_2019"] == 1000.0
    info = b["metadata"]["fields"]
    assert info["pop_2019"]["unit"] == "persons/cell" and "ESTIMATE" in info["pop_2019"]["data_kind"] and info["pop_2019"]["vintage"] == "2019"      # from the manifest
    assert info["emis_pm25_total_t_yr"]["vintage"] == "2018"
    assert any("related model products" in n for n in b["metadata"]["notes"])


def test_missing_values_are_null_never_zero(client):
    b = client.get("/api/grid", params={"fields": "cell_id,pop_2019"}).json()
    cell4 = next(f for f in b["features"] if f["properties"]["cell_id"] == 4)
    assert cell4["properties"]["pop_2019"] is None
    assert b["metadata"]["stats"]["pop_2019"]["n_null"] == 1 and b["metadata"]["stats"]["pop_2019"]["min"] == 1000.0
    flt = client.get("/api/grid", params={"fields": "cell_id,pop_2019", "min_population": 0}).json()
    assert flt["metadata"]["total"] == 5 and 4 not in [f["id"] for f in flt["features"]]       # a missing value does not match a filter (it is not read as 0)


def test_camx_month_selects_the_modelled_value(client):
    jan = client.get("/api/grid", params={"camx_month": "jan", "fields": "cell_id,camx_pm25_ugm3"}).json()
    dec = client.get("/api/grid", params={"camx_month": "dec", "fields": "cell_id,camx_pm25_ugm3"}).json()
    ann = client.get("/api/grid", params={"fields": "cell_id,camx_pm25_ugm3"}).json()
    val = lambda b, i: next(f["properties"]["camx_pm25_ugm3"] for f in b["features"] if f["id"] == i)  # noqa: E731
    assert (val(jan, 2), val(dec, 2), val(ann, 2)) == (20.0, 31.0, 12.0)
    meta = dec["metadata"]["fields"]["camx_pm25_ugm3"]
    assert "MODELLED" in meta["data_kind"] and "not current" in meta["data_kind"] and meta["selection"] == "dec" and "not independent" in meta["note"]
    assert client.get("/api/grid", params={"camx_month": "june"}).status_code == 422


def test_grid_filters_population_emissions_road_density_camx_and_bbox(client):
    ids = lambda **p: sorted(f["id"] for f in client.get("/api/grid", params=p).json()["features"])  # noqa: E731
    assert ids(min_population=3000) == [3, 5, 6] and ids(min_population=2000, max_population=3000) == [2, 3]
    assert ids(min_emissions=8) == [4, 5, 6] and ids(min_road_density=7.5) == [5, 6]
    assert ids(camx_month="jan", min_camx_pm25=40) == [4, 5, 6] and ids(camx_month="jan", max_camx_pm25=20) == [1, 2]
    assert ids(bbox="74.35,42.70,74.359,42.709") == [1]
    assert ids(min_population=100000) == []                                                      # empty, not an error


def test_grid_fields_geometry_and_paging(client):
    b = client.get("/api/grid", params={"fields": "cell_id,emis_pm25_total_t_yr", "geometry": "false", "limit": 2, "offset": 1}).json()
    assert [f["id"] for f in b["features"]] == [2, 3] and all(f["geometry"] is None for f in b["features"])
    assert set(b["features"][0]["properties"]) == {"cell_id", "emis_pm25_total_t_yr"} and b["metadata"]["total"] == 6 and b["metadata"]["count"] == 2
    st = b["metadata"]["stats"]["emis_pm25_total_t_yr"]
    assert st["min"] == 2.0 and st["max"] == 12.0 and math.isclose(st["median"], 7.0)


def test_grid_invalid_parameters(client):
    for params in ({"fields": "cell_id,not_a_column"}, {"bbox": "1,2,3"}, {"bbox": "5,5,1,1"}, {"bbox": "a,b,c,d"}, {"min_population": -1}, {"limit": 0}, {"limit": 5000}):
        assert client.get("/api/grid", params=params).status_code == 422, params
    r = client.get("/api/grid", params={"fields": "bogus"})
    assert r.json()["error"] == "invalid_query" and "camx_pm25_ugm3" in r.json()["allowed"] and "pop_2019" in r.json()["allowed"]


# ------------------------------------------------------------------ /api/layers
def test_layer_catalogue_states_kind_vintage_and_limits(client):
    b = client.get("/api/layers").json()
    L = {x["id"]: x for x in b["layers"]}
    assert set(L) == {"stations", "population_grid", "camx_pm25", "emissions_context", "roads", "context_features", "administrative_boundary", "spatial_grid"}
    assert all(x["time_status"] == "historical" and x["data_kind"] and x["vintage"] and x["limitations"] and x["endpoint"].startswith("/api/") for x in b["layers"])
    assert "MODELLED" in L["camx_pm25"]["data_kind"] and "not measured" in L["camx_pm25"]["data_kind"] and L["camx_pm25"]["unit"] == "µg/m³"
    assert "2018" in L["emissions_context"]["vintage"] and "2019" in L["population_grid"]["vintage"] and L["population_grid"]["data_kind"].startswith("estimated")
    assert any("NOT Bishkek" in s for s in L["administrative_boundary"]["limitations"]) and any("traffic" in s for s in L["roads"]["limitations"])
    assert any("APPROXIMATE" in s for s in L["stations"]["limitations"]) and any("No current citywide" in s for s in L["stations"]["limitations"])
    assert [x["id"] for x in b["layers"] if x["default_visible"]] == ["stations"]                  # not everything is visible by default
    assert all(x["available"] for x in b["layers"]) and L["stations"]["feature_count"] == 3 and L["roads"]["feature_count"] == 5 and L["spatial_grid"]["feature_count"] == 6


def test_station_layer_geojson(client):
    b = client.get("/api/layers/stations").json()
    assert [f["id"] for f in b["features"]] == ["us-embassy-bishkek", "C1", "C2"]
    emb = b["features"][0]
    assert emb["geometry"] == {"type": "Point", "coordinates": [74.5824625, 42.8272774]} and emb["properties"]["approximate_location"] is True
    c1 = b["features"][1]["properties"]
    assert c1["network"] == "clarity" and c1["approximate_location"] is False and c1["data_available"] is True and c1["period_start"].startswith("2021-03-01")
    assert len(client.get("/api/layers/stations", params={"include_no_data": "true"}).json()["features"]) == 5


def test_thematic_layers_reuse_the_grid(client):
    pop = client.get("/api/layers/population_grid").json()
    assert len(pop["features"]) == 6 and set(pop["features"][0]["properties"]) == {"cell_id", "pop_2019"}
    camx = client.get("/api/layers/camx_pm25", params={"camx_month": "feb"}).json()
    assert camx["metadata"]["camx_month"] == "feb" and camx["features"][0]["properties"]["camx_pm25_ugm3"] == 11.0
    em = client.get("/api/layers/emissions_context").json()
    assert "emis_pm25_total_t_per_km2_yr" in em["features"][0]["properties"] and "n_point_sources" in em["features"][0]["properties"]
    assert len(client.get("/api/layers/spatial_grid").json()["features"]) == 6


def test_roads_layer_groups_and_notes(client):
    d = client.get("/api/layers/roads").json()
    assert sorted({f["properties"]["road_group"] for f in d["features"]}) == ["primary", "secondary", "tertiary"] and d["metadata"]["groups"] == ["primary", "secondary", "tertiary"]
    assert "name" not in d["features"][0]["properties"] and any("names are not available" in n for n in d["metadata"]["notes"])
    assert len(client.get("/api/layers/roads", params={"groups": "all"}).json()["features"]) == 5
    assert [f["properties"]["road_group"] for f in client.get("/api/layers/roads", params={"groups": "local_streets"}).json()["features"]] == ["local_streets"]
    assert client.get("/api/layers/roads", params={"groups": "motorway"}).status_code == 422
    assert client.get("/api/layers/roads", params={"bbox": "0,0,1"}).status_code == 422


def test_context_and_admin_layers(client):
    c = client.get("/api/layers/context_features").json()
    assert len(c["features"]) == 4 and c["metadata"]["available_layers"] == ["brick_kilns", "bte_boilers", "dh_districts"]
    named = [f for f in c["features"] if f["properties"]["layer"] == "bte_boilers"]
    assert [f["properties"]["feature_name"] for f in named] == ["Boiler A", "Boiler B"]
    kiln = next(f for f in c["features"] if f["properties"]["layer"] == "brick_kilns")
    assert kiln["properties"]["feature_name"] is None and kiln["properties"]["name_is_placeholder"] is True            # no invented names
    assert next(f for f in c["features"] if f["properties"]["layer"] == "dh_districts")["properties"]["layer_meaning_status"] == "unverified"
    assert len(client.get("/api/layers/context_features", params={"layers": "bte_boilers"}).json()["features"]) == 2
    assert client.get("/api/layers/context_features", params={"layers": "nope"}).status_code == 422
    a = client.get("/api/layers/administrative_boundary").json()
    assert len(a["features"]) == 1 and any("NOT Bishkek" in n for n in a["metadata"]["notes"])


def test_unknown_layer(client):
    r = client.get("/api/layers/nope")
    assert r.status_code == 404 and "stations" in r.json()["available"]
