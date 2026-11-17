"""Response models. Extra fields are allowed so the API can add provenance details without breaking clients."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow")


class DataPeriod(_Model):
    start: Optional[str] = None
    end: Optional[str] = None


class Station(_Model):
    id: str
    name: str
    network: str
    operator_reported: Optional[str] = None
    sensor_type: str
    grade: str
    data_kind: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_status: str
    location_note: Optional[str] = None
    data_available: bool
    status: str
    vintage: str
    data_period: Optional[DataPeriod] = None
    n_valid_readings: int = 0
    coverage_pct: Optional[float] = None
    quality: Optional[dict[str, Any]] = None
    shares_coordinates_with: list[str] = []
    notes: list[str] = []
    timestamp_convention: str


class StationsResponse(_Model):
    count: int
    stations: list[Station]
    notes: list[str]


class MeasurementRecord(_Model):
    timestamp: str
    interval_end: Optional[str] = None
    pm25_ugm3: Optional[float] = None
    pm25_valid_ugm3: Optional[float] = None
    quality_flag: str
    flags: list[str] = []
    detail: Optional[dict[str, Any]] = None


class MeasurementsResponse(_Model):
    station: dict[str, Any]
    interval: str
    unit: str
    timestamp_basis: str
    alignment_note: str
    query: dict[str, Any]
    total: int
    count: int
    truncated: bool
    records: list[MeasurementRecord]
    notes: list[str]


class LayerInfo(_Model):
    id: str
    title: str
    description: str
    group: str
    geometry_type: str
    data_kind: str
    vintage: str
    unit: Optional[str] = None
    value_field: Optional[str] = None
    source: str
    endpoint: str
    query_defaults: dict[str, Any] = {}
    limitations: list[str] = []
    available: bool
    unavailable_reason: Optional[str] = None
    feature_count: Optional[int] = None
    default_visible: bool
    time_status: str


class LayersResponse(_Model):
    count: int
    layers: list[LayerInfo]
    notes: list[str]
