import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, buildUrl, getJson } from './apiClient'

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))
}

afterEach(() => vi.unstubAllGlobals())

describe('buildUrl', () => {
  it('drops empty parameters and encodes the rest', () => {
    expect(buildUrl('/api/x', { a: 1, b: undefined, c: null, d: '', e: 'a b' })).toBe('/api/x?a=1&e=a+b')
    expect(buildUrl('/api/x')).toBe('/api/x')
  })
})

describe('getJson', () => {
  it('returns parsed JSON on success', async () => {
    vi.stubGlobal('fetch', vi.fn(() => respond({ ok: true })))
    await expect(getJson('/api/health')).resolves.toEqual({ ok: true })
  })

  it('surfaces the server error message and hint', async () => {
    vi.stubGlobal('fetch', vi.fn(() => respond({ error: 'data_unavailable', message: 'File missing', hint: 'Run the pipeline' }, 503)))
    const err = await getJson('/api/stations').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 503, code: 'data_unavailable', message: 'File missing', hint: 'Run the pipeline' })
  })

  it('explains validation (422) errors', async () => {
    vi.stubGlobal('fetch', vi.fn(() => respond({ detail: [{ loc: ['query', 'interval'], msg: 'bad value' }] }, 422)))
    await expect(getJson('/api/measurements')).rejects.toMatchObject({ status: 422, message: expect.stringContaining('interval') })
  })

  it('reports an unreachable backend clearly', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('failed'))))
    await expect(getJson('/api/stations')).rejects.toMatchObject({ code: 'network_error', status: 0 })
  })

  it('treats a proxy 502 with no body as "backend not running"', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('', { status: 502 }))))
    await expect(getJson('/api/stations')).rejects.toMatchObject({ code: 'network_error', hint: expect.stringContaining('uvicorn') })
  })

  it('handles a non-JSON error body', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('boom', { status: 500 }))))
    await expect(getJson('/api/stations')).rejects.toMatchObject({ status: 500, message: expect.stringContaining('500') })
  })
})

describe('api helpers', () => {
  it('requests the expected endpoints', async () => {
    const f = vi.fn((_u: string) => respond({}))
    vi.stubGlobal('fetch', f)
    await api.measurements('abc', 'monthly')
    await api.layerData('camx_pm25', { camx_month: 'jan' })
    await api.stations()
    await api.explorer({ source: 'clarity', station_id: 'C1', start_date: '2021-03-01', interval: 'daily' })
    await api.explorerSummary({ source: 'embassy' })
    await api.compare({ station_a: 'x', start_a: '2022-01-01' })
    expect(f.mock.calls.map((c) => c[0])).toEqual([
      '/api/measurements?station=abc&interval=monthly',
      '/api/layers/camx_pm25?camx_month=jan',
      '/api/stations',
      '/api/explorer?source=clarity&station_id=C1&start_date=2021-03-01&interval=daily',
      '/api/explorer/summary?source=embassy',
      '/api/explorer/compare?station_a=x&start_a=2022-01-01',
    ])
  })
})
