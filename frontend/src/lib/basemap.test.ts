import { describe, expect, it, vi } from 'vitest'
import { loadBasemapStyle } from './basemap'

describe('loadBasemapStyle', () => {
  it('returns the provider style when it loads', async () => {
    const f = vi.fn(() => Promise.resolve(new Response(JSON.stringify({ version: 8, sources: {}, layers: [] }))))
    const r = await loadBasemapStyle(f as unknown as typeof fetch)
    expect(r.provider).toBe('openfreemap')
  })

  it('falls back to a plain background on HTTP errors', async () => {
    const f = vi.fn(() => Promise.resolve(new Response('', { status: 503 })))
    const r = await loadBasemapStyle(f as unknown as typeof fetch)
    expect(r.provider).toBe('none')
    expect(r.error).toContain('503')
    expect(r.style.layers[0].id).toBe('plain-background')
  })

  it('falls back on network failure and on a malformed style', async () => {
    expect((await loadBasemapStyle((() => Promise.reject(new Error('offline'))) as unknown as typeof fetch)).provider).toBe('none')
    expect((await loadBasemapStyle((() => Promise.resolve(new Response('{}'))) as unknown as typeof fetch)).provider).toBe('none')
  })

  it('falls back when the request times out', async () => {
    const f = ((_u: string, init: RequestInit) =>
      new Promise((_res, rej) => init.signal?.addEventListener('abort', () => rej(new Error('aborted'))))) as unknown as typeof fetch
    const r = await loadBasemapStyle(f, 20)
    expect(r.provider).toBe('none')
  })
})
