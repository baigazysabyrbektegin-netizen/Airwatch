import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// jsdom has no WebGL: replace maplibre-gl with a recording fake.
const mapState = vi.hoisted(() => ({
  handlers: {} as Record<string, (e: unknown) => void>,
  sources: {} as Record<string, { setData: ReturnType<typeof vi.fn> }>,
}))
vi.mock('maplibre-gl', () => {
  class FakeMap {
    constructor() {
      setTimeout(() => mapState.handlers.load?.({}), 0)
    }
    on(type: string, a: unknown, b?: unknown) {
      mapState.handlers[typeof a === 'string' ? `${type}:${a}` : type] = (typeof a === 'string' ? b : a) as (e: unknown) => void
    }
    addControl() {}
    addSource(id: string) {
      mapState.sources[id] = { setData: vi.fn() }
    }
    addLayer() {}
    getSource(id: string) {
      return mapState.sources[id]
    }
    getLayer() {
      return {}
    }
    setLayoutProperty() {}
    setPaintProperty() {}
    setFilter() {}
    getStyle() {
      return { layers: [] }
    }
    getCanvas() {
      return { style: {} }
    }
    queryRenderedFeatures() {
      return []
    }
    remove() {}
  }
  return { Map: FakeMap, NavigationControl: class {}, ScaleControl: class {}, setWorkerUrl: vi.fn() }
})

import { AirQualityMap } from './AirQualityMap'

const station = (over: Record<string, unknown>) => ({
  id: 'c1', name: 'Clarity A1', network: 'clarity', sensor_type: 'low-cost optical', grade: 'low-cost sensor', data_kind: 'measured (low-cost)',
  latitude: 42.87, longitude: 74.6, location_status: 'confirmed', data_available: true, status: 'historical', vintage: '2022-2023',
  data_period: { start: '2022-01-01T00:00:00+06:00', end: '2023-01-01T00:00:00+06:00' }, n_valid_readings: 100, coverage_pct: 80.5,
  quality: { completeness: 0.8 }, shares_coordinates_with: [], notes: [], timestamp_convention: 'hour-ending (assumed)', ...over,
})
const STATIONS = {
  count: 2,
  notes: [],
  stations: [
    station({ id: 'us-embassy-bishkek', name: 'US Embassy Bishkek', network: 'us-embassy', data_kind: 'measured (reference)', location_status: 'approximate', location_note: 'taken from public listing' }),
    station({}),
  ],
}
const layerInfo = (id: string, title: string, kind: string) => ({
  id, title, description: `${title} description`, group: 'g', geometry_type: 'Polygon', data_kind: kind, vintage: '2018', unit: null, value_field: null,
  source: 's', endpoint: `/api/layers/${id}`, query_defaults: {}, limitations: [], available: true, unavailable_reason: null, feature_count: 10,
  default_visible: id === 'stations', time_status: 'historical',
})
const LAYERS = {
  count: 7,
  notes: [],
  layers: [
    layerInfo('stations', 'Stations', 'measured'), layerInfo('population_grid', 'Population', 'estimated'), layerInfo('camx_pm25', 'CAMx', 'modelled'),
    layerInfo('emissions_context', 'Emissions', 'modelled'), layerInfo('roads', 'Roads', 'static'), layerInfo('context_features', 'Context', 'static'),
    layerInfo('administrative_boundary', 'Boundary', 'static'),
  ],
}
const q = { min: 0, p05: 1, p25: 2, median: 3, p75: 4, p95: 5, max: 9, n_null: 0 }
const GRID = { type: 'FeatureCollection', features: [], metadata: { stats: { pop_2019: q, camx_pm25_ugm3: q } } }
const META = {
  available_manifests: [], unavailable_manifests: [], source_record: {},
  datasets: [{ id: 'd', title: 'Dataset D', data_kind: 'modelled', unit: null, vintage: '2018', resolution: null, provenance_review_required: false, limitations: ['Limit X'] }],
  presentation_rules: ['Rule one: CAMx is historical'], limitations: {}, licence: { attribution: 'CC BY', notes: [], other: 'ODbL' },
  basemap: { default_provider: 'OpenFreeMap', usage: 'u', attribution: 'a', policy_url: 'https://openfreemap.org/tos/', fallback: 'f' },
}
const MONTHLY = {
  station: {}, interval: 'monthly', unit: 'ug/m3', timestamp_basis: 'Timestamp basis text', alignment_note: '', total: 1, count: 1, truncated: false, notes: [],
  records: [{ timestamp: '2022-01-01', pm25_ugm3: 10, pm25_valid_ugm3: 10, quality_flag: 'ok', flags: [] }],
}

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status }))
let calls: string[] = []
let overrides: Record<string, () => Promise<Response>> = {}
let basemapOk = true

beforeEach(() => {
  calls = []
  overrides = {}
  basemapOk = true
  mapState.handlers = {}
  mapState.sources = {}
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      calls.push(url)
      if (url.includes('openfreemap')) return basemapOk ? json({ version: 8, sources: {}, layers: [] }) : Promise.reject(new Error('offline'))
      const key = Object.keys(overrides).find((k) => url.startsWith(k))
      if (key) return overrides[key]()
      if (url.startsWith('/api/stations')) return json(STATIONS)
      if (url === '/api/layers') return json(LAYERS)
      if (url.startsWith('/api/layers/')) return json(GRID)
      if (url.startsWith('/api/measurements')) return json(MONTHLY)
      if (url.startsWith('/api/metadata')) return json(META)
      return json({}, 404)
    }),
  )
})
afterEach(() => vi.unstubAllGlobals())

describe('AirQualityMap page', () => {
  it('shows a loading state, then the stations on the map', async () => {
    render(<AirQualityMap />)
    expect(screen.getByText('Loading stations…')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('Loading stations…')).not.toBeInTheDocument())
    await waitFor(() => expect(mapState.sources['aw-stations']?.setData).toHaveBeenCalled())
    const fc = mapState.sources['aw-stations'].setData.mock.calls.at(-1)?.[0] as { features: unknown[] }
    expect(fc.features).toHaveLength(2)
  })

  it('states up front that the data are historical and what each layer is', async () => {
    render(<AirQualityMap />)
    expect(screen.getByText(/Historical research data/)).toBeInTheDocument()
    expect(await screen.findByText('CAMx description')).toBeInTheDocument()
    expect(screen.getAllByText('Modelled').length).toBeGreaterThan(0)
  })

  it('only shows stations by default', async () => {
    render(<AirQualityMap />)
    await screen.findByText('CAMx description')
    expect(screen.getByLabelText('Monitoring stations')).toBeChecked()
    for (const name of [/Population grid/, /^Roads$/, /CAMx modelled PM2.5/, /PM2.5 emissions/]) expect(screen.getByLabelText(name)).not.toBeChecked()
    expect(calls.some((c) => c.startsWith('/api/layers/'))).toBe(false)
  })

  it('fetches a layer when toggled on and shows its legend, one choropleth at a time', async () => {
    const user = userEvent.setup()
    render(<AirQualityMap />)
    await screen.findByText('CAMx description')
    await user.click(screen.getByLabelText(/Population grid/))
    await waitFor(() => expect(calls).toContain('/api/layers/population_grid'))
    const legend = await screen.findByRole('complementary', { name: 'Legend' })
    await waitFor(() => expect(within(legend).getByText(/persons per cell/)).toBeInTheDocument())
    await user.click(screen.getByLabelText(/CAMx modelled PM2.5/))
    expect(screen.getByLabelText(/Population grid/)).not.toBeChecked()
    await waitFor(() => expect(calls).toContain('/api/layers/camx_pm25?camx_month=annual'))
    await user.selectOptions(screen.getByLabelText('Modelled period'), 'jan')
    await waitFor(() => expect(calls).toContain('/api/layers/camx_pm25?camx_month=jan'))
  })

  it('shows an error when a layer fails and keeps the map usable', async () => {
    overrides['/api/layers/roads'] = () => json({ error: 'data_unavailable', message: 'Roads file missing' }, 503)
    const user = userEvent.setup()
    render(<AirQualityMap />)
    await screen.findByText('CAMx description')
    await user.click(screen.getByLabelText(/^Roads$/))
    expect(await screen.findByText('Roads file missing')).toBeInTheDocument()
  })

  it('shows an actionable error with retry when stations cannot be loaded', async () => {
    overrides['/api/stations'] = () => json({ error: 'data_unavailable', message: 'Clarity inventory missing', hint: 'Run the pipeline' }, 503)
    const user = userEvent.setup()
    render(<AirQualityMap />)
    expect(await screen.findByText('Clarity inventory missing')).toBeInTheDocument()
    expect(screen.getByText('Run the pipeline')).toBeInTheDocument()
    delete overrides['/api/stations']
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.queryByText('Clarity inventory missing')).not.toBeInTheDocument())
  })

  it('opens the station panel on map click, flags approximate coordinates and loads the series', async () => {
    render(<AirQualityMap />)
    await waitFor(() => expect(mapState.handlers['click:aw-stations']).toBeDefined())
    await screen.findByText('CAMx description')
    mapState.handlers['click:aw-stations']({ features: [{ properties: { id: 'us-embassy-bishkek' } }] })
    const panel = await screen.findByRole('region', { name: 'Selected station' })
    expect(within(panel).getByText('US Embassy Bishkek')).toBeInTheDocument()
    expect(within(panel).getByText(/Coordinates are approximate/)).toBeInTheDocument()
    await waitFor(() => expect(calls).toContain('/api/measurements?station=us-embassy-bishkek&interval=monthly'))
    expect(await within(panel).findByText('Timestamp basis text')).toBeInTheDocument()
    await userEvent.click(within(panel).getByRole('button', { name: 'Close station panel' }))
    expect(screen.queryByRole('region', { name: 'Selected station' })).not.toBeInTheDocument()
  })

  it('opens the Embassy panel when its approximate-location ring is clicked', async () => {
    render(<AirQualityMap />)
    await waitFor(() => expect(mapState.handlers['click:aw-stations-ring']).toBeDefined())
    await screen.findByText('CAMx description')
    mapState.handlers['click:aw-stations-ring']({ features: [{ properties: { id: 'us-embassy-bishkek' } }] })
    expect(await screen.findByRole('region', { name: 'Selected station' })).toBeInTheDocument()
    expect(await screen.findByText('US Embassy Bishkek')).toBeInTheDocument()
  })

  it('reports a measurement-loading error inside the panel', async () => {
    overrides['/api/measurements'] = () => json({ error: 'data_unavailable', message: 'No hourly file' }, 503)
    render(<AirQualityMap />)
    await waitFor(() => expect(mapState.handlers['click:aw-stations']).toBeDefined())
    await screen.findByText('CAMx description')
    mapState.handlers['click:aw-stations']({ features: [{ properties: { id: 'c1' } }] })
    expect(await screen.findByText(/No hourly file/)).toBeInTheDocument()
  })

  it('opens the sources dialog with the presentation rules', async () => {
    const user = userEvent.setup()
    render(<AirQualityMap />)
    await user.click(screen.getByRole('button', { name: /Sources & limitations/ }))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('Rule one: CAMx is historical')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Datasets' }))
    expect(within(dialog).getByText('Limit X')).toBeInTheDocument()
  })

  it('falls back to a plain background when the basemap service is unreachable', async () => {
    basemapOk = false
    render(<AirQualityMap />)
    expect(await screen.findByText(/Basemap service not reachable/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Street basemap/)).toBeDisabled()
  })
})
