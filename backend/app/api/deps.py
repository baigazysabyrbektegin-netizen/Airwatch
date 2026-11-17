from functools import lru_cache

from app.core.config import settings
from app.services.data_store import DataStore


@lru_cache(maxsize=1)
def get_store() -> DataStore:
    """One cached store per process. Tests replace this dependency with a store over synthetic data."""
    return DataStore(settings.data_dir)
