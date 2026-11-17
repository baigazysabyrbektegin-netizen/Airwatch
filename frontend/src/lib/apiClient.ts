import type {
  ApiErrorBody,
  CompareResponse,
  ExplorerResponse,
  SummaryResponse,
  LayerFeatureCollection,
  LayersResponse,
  MeasurementsResponse,
  MetadataResponse,
  StationsResponse,
  PlannerCandidatesResponse,
  PlannerOverview,
} from './types'

/** An API failure with the server's own explanation (never a silent fallback to empty data). */
export class ApiError extends Error {
  status: number
  code: string
  hint?: string
  constructor(message: string, status: number, code = 'error', hint?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.hint = hint
  }
}

const BACKEND_HINT = 'From the repository root run: $env:PYTHONPATH="."; .\\backend\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --app-dir backend --port 8000'

type Params =Record<string, string | number | boolean | null | undefined>

export function buildUrl(path: string, params?: Params): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== '') q.set(k, String(v))
  }
  const s = q.toString()
  return s ? `${path}?${s}` : path
}

function explain(body: ApiErrorBody | null, status: number): { message: string; code: string; hint?: string } {
  if (body?.message) return { message: body.message, code: body.error ?? 'error', hint: body.hint }
  if (Array.isArray(body?.detail) && body.detail.length) {
    const d = body.detail[0] as { loc?: unknown[]; msg?: string }
    return { message: `Invalid request: ${(d.loc ?? []).slice(1).join('.')} ${d.msg ?? ''}`.trim(), code: 'invalid_query' }
  }
  if (status === 502 || status === 503 || status === 504) {
    // The dev/preview proxy answers like this when nothing is listening on the backend port.
    return { message: 'Cannot reach the AirWatch API. Is the backend running?', code: 'network_error', hint: BACKEND_HINT }
  }
  return { message: `The server answered HTTP ${status}.`, code: 'http_error' }
}

export async function getJson<T>(path: string, params?: Params, signal?: AbortSignal): Promise<T> {
  let res: Response
  try {
    res = await fetch(buildUrl(path, params), { signal, headers: { Accept: 'application/json' } })
  } catch (err) {
    if ((err as { name?: string }).name === 'AbortError') throw err
    throw new ApiError('Cannot reach the AirWatch API. Is the backend running?', 0, 'network_error', BACKEND_HINT)
  }

  if (!res.ok) {
    let body: ApiErrorBody | null = null
    try {
      body = (await res.json()) as ApiErrorBody
    } catch {
      body = null
    }
    const e = explain(body, res.status)
    throw new ApiError(e.message, res.status, e.code, e.hint)
  }
  return (await res.json()) as T
}

export async function postJson<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, { method: 'POST', signal, headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  } catch (err) {
    if ((err as { name?: string }).name === 'AbortError') throw err
    throw new ApiError('Cannot reach the AirWatch API. Is the backend running?', 0, 'network_error', BACKEND_HINT)
  }
  if (!res.ok) {
    let body: ApiErrorBody | null = null
    try { body = (await res.json()) as ApiErrorBody } catch { body = null }
    const e = explain(body, res.status)
    throw new ApiError(e.message, res.status, e.code, e.hint)
  }
  return (await res.json()) as T
}

export const api = {
  stations: (includeNoData = false, signal?: AbortSignal) => getJson<StationsResponse>('/api/stations', { include_no_data: includeNoData || undefined }, signal),
  layers: (signal?: AbortSignal) => getJson<LayersResponse>('/api/layers', undefined, signal),
  layerData: (id: string, params?: Params, signal?: AbortSignal) => getJson<LayerFeatureCollection>(`/api/layers/${id}`, params, signal),
  measurements: (station: string, interval: 'hourly' | 'daily' | 'monthly', params?: Params, signal?: AbortSignal) =>
    getJson<MeasurementsResponse>('/api/measurements', { station, interval, ...params }, signal),
  metadata: (signal?: AbortSignal) => getJson<MetadataResponse>('/api/metadata', undefined, signal),
  explorer: (params: Params, signal?: AbortSignal) => getJson<ExplorerResponse>('/api/explorer', params, signal),
  explorerSummary: (params: Params, signal?: AbortSignal) => getJson<SummaryResponse>('/api/explorer/summary', params, signal),
  compare: (params: Params, signal?: AbortSignal) => getJson<CompareResponse>('/api/explorer/compare', params, signal),
  plannerOverview: (params?: Params, signal?: AbortSignal) => getJson<PlannerOverview>('/api/planner/overview', params, signal),
  plannerCandidates: (params?: Params, signal?: AbortSignal) => getJson<PlannerCandidatesResponse>('/api/planner/candidates', params, signal),
  plannerCoverage: (params?: Params, signal?: AbortSignal) => getJson<LayerFeatureCollection>('/api/planner/coverage', params, signal),
  recalculatePlanner: (body: unknown, signal?: AbortSignal) => postJson<PlannerCandidatesResponse>('/api/planner/recalculate', body, signal),
}
