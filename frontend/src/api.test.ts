import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchHealth } from './api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('fetchHealth', () => {
  it('returns parsed JSON on success', async () => {
    const body = { status: 'ok', service: 's', version: '0', python: '3.13', time_utc: 't' }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => body }))
    await expect(fetchHealth()).resolves.toEqual(body)
  })

  it('throws on a non-2xx response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }))
    await expect(fetchHealth()).rejects.toThrow('HTTP 503')
  })
})
