from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import get_store
from app.services import planner
from app.services.data_store import DataStore

router = APIRouter()


class RecalculateRequest(BaseModel):
    n: int = Field(default=5, ge=1, le=planner.MAX_CANDIDATES)
    coverage_radius_km: float = Field(default=planner.DEFAULT_RADIUS_KM, gt=0, le=100)
    min_separation_km: float = Field(default=planner.DEFAULT_MIN_SEPARATION_KM, ge=0, le=100)
    exposure_weight: float = Field(default=planner.DEFAULT_EXPOSURE_WEIGHT, ge=0)
    source_weight: float = Field(default=planner.DEFAULT_SOURCE_WEIGHT, ge=0)


def _params(
    n: int,
    coverage_radius_km: float,
    min_separation_km: float,
    exposure_weight: float,
    source_weight: float,
) -> planner.PlannerParameters:
    params = planner.PlannerParameters(n, coverage_radius_km, min_separation_km, exposure_weight, source_weight)
    params.validate()
    return params


@router.get("/planner/overview")
def planner_overview(
    coverage_radius_km: float = Query(planner.DEFAULT_RADIUS_KM, gt=0, le=100),
    store: DataStore = Depends(get_store),
):
    return planner.overview(store, _params(5, coverage_radius_km, planner.DEFAULT_MIN_SEPARATION_KM, planner.DEFAULT_EXPOSURE_WEIGHT, planner.DEFAULT_SOURCE_WEIGHT))


@router.get("/planner/candidates")
def planner_candidates(
    n: int = Query(5, ge=1, le=planner.MAX_CANDIDATES),
    coverage_radius_km: float = Query(planner.DEFAULT_RADIUS_KM, gt=0, le=100),
    min_separation_km: float = Query(planner.DEFAULT_MIN_SEPARATION_KM, ge=0, le=100),
    exposure_weight: float = Query(planner.DEFAULT_EXPOSURE_WEIGHT, ge=0),
    source_weight: float = Query(planner.DEFAULT_SOURCE_WEIGHT, ge=0),
    store: DataStore = Depends(get_store),
):
    params = _params(n, coverage_radius_km, min_separation_km, exposure_weight, source_weight)
    return planner.candidates(store, params)


@router.get("/planner/coverage")
def planner_coverage(
    coverage_radius_km: float = Query(planner.DEFAULT_RADIUS_KM, gt=0, le=100),
    store: DataStore = Depends(get_store),
):
    return planner.coverage(store, _params(5, coverage_radius_km, planner.DEFAULT_MIN_SEPARATION_KM, planner.DEFAULT_EXPOSURE_WEIGHT, planner.DEFAULT_SOURCE_WEIGHT))


@router.post("/planner/recalculate")
def planner_recalculate(request: RecalculateRequest, store: DataStore = Depends(get_store)):
    return planner.candidates(store, planner.PlannerParameters(**request.model_dump()))
