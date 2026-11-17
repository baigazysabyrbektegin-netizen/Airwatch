// Basemap handling.
//
// Provider: OpenFreeMap (https://openfreemap.org). Verified policy (openfreemap.org/tos): no API key, no
// registration, no stated request limits, commercial use allowed, attribution required, NO SLA (the service may
// change or stop without notice). Because of the missing SLA the map always works without it: if the style
// cannot be fetched in time, the project's own data layers are drawn on a plain background instead.

import type { StyleSpecification } from 'maplibre-gl'

export const OPENFREEMAP_STYLE_URL = 'https://tiles.openfreemap.org/styles/positron'
export const BASEMAP_SOURCE_ID = 'openmaptiles'
export const BASEMAP_ATTRIBUTION =
  '<a href="https://openfreemap.org" target="_blank" rel="noopener">OpenFreeMap</a> © <a href="https://www.openmaptiles.org/" target="_blank" rel="noopener">OpenMapTiles</a> ' +
  'Data from <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>'
export const DATA_ATTRIBUTION =
  'Study data: Guttikunda (2024), Zenodo <a href="https://doi.org/10.5281/zenodo.12720883" target="_blank" rel="noopener">10.5281/zenodo.12720883</a> (CC BY 4.0); roads © OpenStreetMap contributors (ODbL)'

export const PLAIN_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: 'plain-background', type: 'background', paint: { 'background-color': '#e9efee' } }],
}

export interface BasemapResult {
  style: StyleSpecification
  provider: 'openfreemap' | 'none'
  error?: string
}

/** Fetch the OpenFreeMap style with a time limit; fall back to the plain style on any failure. */
export async function loadBasemapStyle(fetchImpl: typeof fetch = fetch, timeoutMs = 6000, url = OPENFREEMAP_STYLE_URL): Promise<BasemapResult> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    const res = await fetchImpl(url, { signal: ctrl.signal })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const style = (await res.json()) as StyleSpecification
    if (!style || !Array.isArray(style.layers)) throw new Error('unexpected style document')
    return { style, provider: 'openfreemap' }
  } catch (err) {
    return { style: structuredClone(PLAIN_STYLE), provider: 'none', error: err instanceof Error ? err.message : String(err) }
  } finally {
    clearTimeout(timer)
  }
}
