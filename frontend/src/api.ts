export interface HealthResponse {
  status: string
  service: string
  version: string
  python: string
  time_utc: string
}

/** Calls the backend liveness endpoint. Throws on any non-2xx response. */
export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const res = await fetch('/api/health', { signal })
  if (!res.ok) {
    throw new Error(`Backend responded with HTTP ${res.status}`)
  }
  return (await res.json()) as HealthResponse
}
