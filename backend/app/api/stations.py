from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_store
from app.schemas import Station, StationsResponse
from app.services import stations as svc
from app.services.data_store import DataStore

router = APIRouter(tags=["stations"])


@router.get("/stations", response_model=StationsResponse)
def list_stations(
    network: Optional[Literal["clarity", "us-embassy"]] = Query(None, description="Only this network."),
    include_no_data: bool = Query(False, description="Also list the Clarity devices that are in the workbook but have no readings."),
    store: DataStore = Depends(get_store),
):
    """Monitoring stations with their real coordinates, period, coverage and quality information.

    The US Embassy position is approximate. Clarity devices are listed as in the 2021 workbook."""
    rows = svc.list_stations(store, network, include_no_data)
    return {"count": len(rows), "stations": rows, "notes": [
        "All stations are historical (Clarity 2021; US Embassy 2019-2024). No current citywide coverage dataset exists.", svc.ALIGNMENT_NOTE,
        "The Embassy location is APPROXIMATE (OpenStreetMap building point), not a verified monitor coordinate."]}


@router.get("/stations/{station_id}", response_model=Station)
def get_station(station_id: str, store: DataStore = Depends(get_store)):
    return svc.get_station(store, station_id)
