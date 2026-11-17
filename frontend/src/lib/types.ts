// Types mirror the FastAPI responses (backend/app/schemas.py and the GeoJSON services).

import type { FeatureCollection, Geometry } from 'geojson'

export type DataPeriod = { start: string | null; end: string | null }

export interface Station {
  id: string
  name: string
  network: 'clarity' | 'us-embassy' | string
  operator_reported?: string | null
  sensor_type: string
  grade: string
  data_kind: string
  latitude: number | null
  longitude: number | null
  location_status: string
  location_note?: string | null
  data_available: boolean
  status: string
  vintage: string
  data_period: DataPeriod | null
  n_valid_readings: number
  coverage_pct: number | null
  quality: Record<string, unknown> | null
  shares_coordinates_with: string[]
  notes: string[]
  timestamp_convention: string
}

export interface StationsResponse {
  count: number
  stations: Station[]
  notes: string[]
}

export interface MeasurementRecord {
  timestamp: string
  interval_end?: string | null
  pm25_ugm3: number | null
  pm25_valid_ugm3: number | null
  quality_flag: string
  flags: string[]
  detail?: Record<string, unknown> | null
}

export interface MeasurementsResponse {
  station: { id: string; name: string; network: string; data_kind: string; location_status: string }
  interval: 'hourly' | 'daily' | 'monthly'
  unit: string
  timestamp_basis: string
  alignment_note: string
  total: number
  count: number
  truncated: boolean
  records: MeasurementRecord[]
  notes: string[]
}

export interface LayerInfo {
  id: string
  title: string
  description: string
  group: string
  geometry_type: string
  data_kind: string
  vintage: string
  unit: string | null
  value_field: string | null
  source: string
  endpoint: string
  query_defaults: Record<string, string>
  limitations: string[]
  available: boolean
  unavailable_reason: string | null
  feature_count: number | null
  default_visible: boolean
  time_status: string
}

export interface LayersResponse {
  count: number
  layers: LayerInfo[]
  notes: string[]
}

export interface FieldStats {
  min?: number
  p05?: number
  p25?: number
  median?: number
  p75?: number
  p95?: number
  max?: number
  n_null: number
}

export interface GridMetadata {
  total: number
  count: number
  camx_month: string
  fields: Record<string, { definition?: string; unit?: string; data_kind?: string; vintage?: string; note?: string; selection?: string }>
  stats: Record<string, FieldStats>
  notes: string[]
}

export type LayerFeatureCollection = FeatureCollection<Geometry | null, Record<string, unknown>> & {
  metadata?: Partial<GridMetadata> & { notes?: string[]; count?: number }
}

export interface MetadataResponse {
  available_manifests: string[]
  unavailable_manifests: string[]
  source_record: { id?: number; doi?: string; title?: string; licence?: { id?: string }; creators?: { name: string }[]; url?: string }
  datasets: {
    id: string
    title: string
    data_kind: string | null
    unit: string | null
    vintage: string | null
    resolution: string | null
    provenance_review_required: boolean
    limitations: string[]
  }[]
  presentation_rules: string[]
  limitations: Record<string, string[]>
  licence: { attribution: string; notes: string[]; other: string }
  basemap: { default_provider: string; usage: string; attribution: string; policy_url: string; fallback: string }
}

export interface ApiErrorBody {
  error?: string
  message?: string
  hint?: string
  detail?: unknown
}

export interface PlannerParameters {
  n: number
  coverage_radius_km: number
  min_separation_km: number
  exposure_weight: number
  source_weight: number
}

export interface PlannerOverview {
  existing_clarity_stations: number
  eligible_grid_cells: number
  population_2019_total: number
  population_2019_underserved: number
  population_underserved_pct: number | null
  covered_cell_pct: number
  coverage_radius_km: number
  data_kind: string
  limitations: string[]
}

export interface PlannerCandidate {
  candidate_id: string
  rank: number
  latitude: number
  longitude: number
  priority_score: number
  score_components: Record<string, number | null>
  population_2019: number | null
  camx_pm25_annual_mean_ugm3: number | null
  emissions_pm25_t_per_km2_yr: number | null
  nearest_station_id: string
  nearest_station_distance_km: number
  data_confidence: string
  explanation: string
  limitations: string[]
}

export interface PlannerCandidatesResponse {
  parameters: PlannerParameters
  count: number
  candidates: PlannerCandidate[]
  data_kind: string
  notes: string[]
}

// ---------------------------------------------------------------- Data & Weather Explorer (Phase 2)
export type Interval = 'hourly' | 'daily' | 'monthly'
export type WeatherVar =
  | 'temperature_2m'
  | 'relative_humidity_2m'
  | 'wind_speed_10m'
  | 'wind_direction_10m'
  | 'surface_pressure'
  | 'precipitation'
  | 'boundary_layer_height'

export interface QualityWarning {
  code: string
  severity: 'info' | 'warning'
  message: string
}

export interface WeatherProvenance {
  provider: string
  model: string
  data_kind: string
  is_forecast: boolean
  is_local_measurement: boolean
  spatial_resolution: string
  grid: { requested_latitude: number | null; requested_longitude: number | null; returned_latitude: number | null; returned_longitude: number | null; elevation_m: number | null }
  retrieved_at_utc: string | null
  period: { first_local: string | null; last_local: string | null }
  attribution: string[]
  licence: string
  limitations: string[]
}

export interface ExplorerPoint {
  timestamp: string
  pm25: number | null
  pm25_raw: number | null
  quality_flag: string
  flags: string[]
  detail?: Record<string, unknown> | null
  weather: Partial<Record<WeatherVar, number | null>>
}

export interface Relationship {
  n_pairs: number
  enough_pairs: boolean
  min_pairs_required: number
  pearson_r: number | null
  spearman_rho: number | null
  reason: string | null
}

export interface ExplorerResponse {
  station: { id: string; name: string; network: string; data_kind: string; location_status: string; timestamp_convention: string }
  source: 'embassy' | 'clarity'
  interval: Interval
  unit: string
  query: { start_date: string | null; end_date: string | null; effective_start: string; effective_end: string; station_data_period: { first: string; last: string } }
  alignment: { weather_included: boolean; pairing_basis: string; weather_offset_hours: number; offset_applies: boolean; statement: string; weather_timestamps: string }
  empty: boolean
  empty_reason: string | null
  count: number
  n_valid?: number
  series: ExplorerPoint[]
  weather: { included: boolean; unavailable?: boolean; variables?: Record<string, { label: string; unit: string }>; provenance?: WeatherProvenance } | null
  relationships: { variables?: Partial<Record<WeatherVar, Relationship>>; basis?: string; note?: string }
  warnings: QualityWarning[]
  notes: string[]
}

export interface Stats {
  mean: number | null
  median: number | null
  min: number | null
  max: number | null
  std: number | null
  p05: number | null
  p25: number | null
  p75: number | null
  p95: number | null
  p99: number | null
}

export interface SummaryResponse {
  station: { id: string; name: string; network: string; data_kind?: string }
  source: 'embassy' | 'clarity'
  unit?: string
  query: ExplorerResponse['query']
  empty: boolean
  empty_reason: string | null
  stats?: Stats
  stats_basis?: string
  n_valid?: number
  expected_hours?: number
  coverage_pct?: number | null
  coverage_definition?: string
  observations?: {
    expected_hours: number
    valid: number
    excluded: Record<string, number>
    suspect: { excluded_suspect_hours: number; flagged_but_included: Record<string, number> }
    imputed: { hours_with_feature_interpolation: number; used_in_statistics: boolean; note: string }
    negative_clipped_to_zero: number
  }
  daily?: { n_valid_days: number; n_days: number; mean_of_daily_means: number | null }
  monthly?: { year_month: string; mean: number | null; n_valid_days: number; eligible_days: number; meets_75pct_rule: boolean }[]
  climatology?: { month: number; n_months: number; mean: number | null; min: number | null; max: number | null }[]
  seasonal?: { season: string; label: string; eligible_days: number; n_valid_days: number; mean_of_daily_means: number | null; median_of_daily_means: number | null; p95_of_daily_means: number | null }[]
  seasonal_by_year?: { season_year: number; season: string; label: string; eligible_days: number; n_valid_days: number; meets_75pct_rule: boolean; mean_of_daily_means: number | null }[]
  warnings: QualityWarning[]
  notes?: string[]
}

export interface DiffEntry {
  a: number | null
  b: number | null
  difference_b_minus_a: number | null
  percent_change: number | null
}

export interface ComparePeriod {
  label: string
  station: { id: string; name: string; network: string }
  source: 'embassy' | 'clarity'
  query: ExplorerResponse['query']
  empty: boolean
  empty_reason: string | null
  summary?: SummaryResponse
  series: { index: number; timestamp: string; pm25: number | null }[]
  weather?: Record<string, { mean_of_daily_values: number | null; n_days: number; unit: string | null }>
}

export interface CompareResponse {
  interval: 'daily' | 'monthly'
  unit: string
  period_a: ComparePeriod
  period_b: ComparePeriod
  difference: { available: boolean; reason: string | null; hourly_mean?: DiffEntry; median?: DiffEntry; mean_of_daily_means?: DiffEntry; p95?: DiffEntry; max?: DiffEntry; weather?: Record<string, DiffEntry> }
  warnings: QualityWarning[]
  alignment: { statement: string; x_axis: string }
  notes: string[]
}
