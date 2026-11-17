from fastapi import APIRouter, Depends

from app.api.deps import get_store
from app.services import metadata as svc
from app.services.data_store import DataStore

router = APIRouter(tags=["metadata"])


@router.get("/metadata")
def metadata(store: DataStore = Depends(get_store)):
    """Sources, vintages, data kinds, units, provenance, limitations and licence notes, taken from the processing manifests."""
    return svc.build(store)
