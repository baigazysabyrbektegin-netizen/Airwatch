import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/apiClient'
import { GRID_LAYERS, type CamxMonth, type ToggleId } from '../lib/layers'
import type { LayerFeatureCollection, LayerInfo, MetadataResponse, Station } from '../lib/types'
import { LayerControls } from '../components/LayerControls'
import { Legend } from '../components/Legend'
import { MapView } from '../components/MapView'
import { MetadataDialog, ProvenanceNotice } from '../components/ProvenanceNotice'
import { StationPanel } from '../components/StationPanel'

const DEFAULT_VISIBLE: Record<ToggleId, boolean> = {
  stations: true,
  population_grid: false,
  roads: false,
  camx_pm25: false,
  emissions_context: false,
  context_features: false,
  administrative_boundary: false,
}

type Load<T> = { status: 'loading' } | { status: 'error'; message: string; hint?: string } | { status: 'ok'; data: T }

const msg = (e: unknown) => (e instanceof Error ? e.message : String(e))

export function AirQualityMap() {
  const [stations, setStations] = useState<Load<Station[]>>({ status: 'loading' })
  const [layers, setLayers] = useState<LayerInfo[]>([])
  const [layersError, setLayersError] = useState<string | null>(null)
  const [metadata, setMetadata] = useState<MetadataResponse | null>(null)
  const [metaError, setMetaError] = useState<string | null>(null)
  const [visible, setVisible] = useState(DEFAULT_VISIBLE)
  const [layerData, setLayerData] = useState<Partial<Record<ToggleId, LayerFeatureCollection>>>({})
  const [loading, setLoading] = useState<Partial<Record<ToggleId, boolean>>>({})
  const [errors, setErrors] = useState<Partial<Record<ToggleId, string>>>({})
  const [camxMonth, setCamxMonth] = useState<CamxMonth>('annual')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [basemapOn, setBasemapOn] = useState(true)
  const [basemapAvailable, setBasemapAvailable] = useState(true)
  const [sourcesOpen, setSourcesOpen] = useState(false)
  const [controlsOpen, setControlsOpen] = useState(false)
  const loaded = useRef<Partial<Record<ToggleId, string>>>({})

  const fetchStations = useCallback(() => {
    api
      .stations()
      .then((r) => setStations({ status: 'ok', data: r.stations }))
      .catch((e: unknown) => setStations({ status: 'error', message: msg(e), hint: (e as { hint?: string }).hint }))
  }, [])

  const retryStations = () => {
    setStations({ status: 'loading' })
    fetchStations()
  }

  useEffect(() => {
    fetchStations()
    api.layers().then((r) => setLayers(r.layers)).catch((e: unknown) => setLayersError(msg(e)))
    api.metadata().then(setMetadata).catch((e: unknown) => setMetaError(msg(e)))
  }, [fetchStations])

  // Fetch a layer the first time it is shown (and CAMx again when the month changes).
  useEffect(() => {
    const ctrl = new AbortController()
    for (const id of Object.keys(visible) as ToggleId[]) {
      if (id === 'stations' || !visible[id]) continue
      const key = id === 'camx_pm25' ? `${id}:${camxMonth}` : id
      if (loaded.current[id] === key) continue
      loaded.current[id] = key
      setLoading((s) => ({ ...s, [id]: true }))
      setErrors((s) => ({ ...s, [id]: undefined }))
      api
        .layerData(id, id === 'camx_pm25' ? { camx_month: camxMonth } : undefined, ctrl.signal)
        .then((fc) => setLayerData((s) => ({ ...s, [id]: fc })))
        .catch((e: unknown) => {
          if (ctrl.signal.aborted) return
          loaded.current[id] = undefined
          setErrors((s) => ({ ...s, [id]: msg(e) }))
        })
        .finally(() => !ctrl.signal.aborted && setLoading((s) => ({ ...s, [id]: false })))
    }
    return () => ctrl.abort()
  }, [visible, camxMonth])

  const toggle = (id: ToggleId) =>
    setVisible((v) => {
      const next = { ...v, [id]: !v[id] }
      // One choropleth at a time.
      if (next[id] && (GRID_LAYERS as string[]).includes(id)) for (const g of GRID_LAYERS) if (g !== id) next[g] = false
      return next
    })

  const selected = useMemo(() => (stations.status === 'ok' ? stations.data.find((s) => s.id === selectedId) ?? null : null), [stations, selectedId])
  const stationList = stations.status === 'ok' ? stations.data : []

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ProvenanceNotice metadata={metadata} metaError={metaError} onOpenSources={() => setSourcesOpen(true)} />
      <div className="relative flex min-h-0 flex-1 flex-col md:flex-row">
        <button
          type="button"
          onClick={() => setControlsOpen((o) => !o)}
          aria-expanded={controlsOpen}
          className="m-2 rounded border border-navy-200 bg-white px-3 py-1.5 text-sm md:hidden"
        >
          {controlsOpen ? 'Hide layers' : 'Show layers'}
        </button>
        <div className={`${controlsOpen ? 'block' : 'hidden'} max-h-[45vh] overflow-y-auto overflow-x-hidden border-navy-200 bg-navy-50 p-3 md:block md:max-h-none md:w-80 md:shrink-0 md:border-r`}>
          {layersError && <p role="alert" className="mb-2 rounded border border-red-300 bg-red-50 p-2 text-xs text-red-800">Layer catalogue unavailable: {layersError}</p>}
          <LayerControls
            layers={layers}
            visible={visible}
            loading={loading}
            errors={errors}
            camxMonth={camxMonth}
            basemapOn={basemapOn}
            basemapAvailable={basemapAvailable}
            onToggle={toggle}
            onCamxMonth={setCamxMonth}
            onBasemap={setBasemapOn}
          />
        </div>

        <div className="relative min-h-[22rem] flex-1">
          <MapView
            stations={stationList}
            layerData={layerData}
            visible={visible}
            basemapOn={basemapOn}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onBasemap={(provider) => setBasemapAvailable(provider === 'openfreemap')}
          />
          {stations.status === 'loading' && (
            <div role="status" className="absolute left-1/2 top-4 -translate-x-1/2 rounded border border-navy-200 bg-white px-3 py-1.5 text-sm shadow">Loading stations…</div>
          )}
          {stations.status === 'error' && (
            <div role="alert" className="absolute left-1/2 top-4 max-w-md -translate-x-1/2 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-900 shadow">
              <div className="font-medium">Could not load stations</div>
              <div>{stations.message}</div>
              {stations.hint && <div className="mt-1 text-xs">{stations.hint}</div>}
              <button type="button" onClick={retryStations} className="mt-2 rounded border border-red-400 px-2 py-0.5 text-xs">Retry</button>
            </div>
          )}
          {stations.status === 'ok' && stations.data.length === 0 && (
            <div role="status" className="absolute left-1/2 top-4 rounded border border-navy-200 bg-white px-3 py-1.5 text-sm shadow">No stations with data were returned.</div>
          )}
          <div className="pointer-events-none absolute bottom-14 left-3 hidden sm:block">
            <div className="pointer-events-auto">
              <Legend visible={visible} layerData={layerData} layers={layers} camxMonth={camxMonth} />
            </div>
          </div>
          {selected && (
            <div className="absolute inset-x-2 bottom-2 top-auto flex max-h-[60%] md:inset-x-auto md:bottom-auto md:right-14 md:top-3 md:max-h-[calc(100%-1.5rem)] md:w-96">
              <StationPanel key={selected.id} station={selected} onClose={() => setSelectedId(null)} />
            </div>
          )}
        </div>
      </div>
      {sourcesOpen && <MetadataDialog metadata={metadata} onClose={() => setSourcesOpen(false)} />}
    </div>
  )
}
