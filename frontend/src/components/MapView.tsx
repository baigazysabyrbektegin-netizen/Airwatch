import * as maplibregl from 'maplibre-gl'
// MapLibre 6 runs tile work in a module worker; let Vite bundle it so the URL is valid in dev and in the production build.
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import type { GeoJSONSource, Map as MlMap, MapGeoJSONFeature } from 'maplibre-gl'
import { useEffect, useRef, useState } from 'react'
import type { FeatureCollection } from 'geojson'
import { BASEMAP_SOURCE_ID, DATA_ATTRIBUTION, loadBasemapStyle } from '../lib/basemap'
import { fillColorExpression, GRID_LAYERS, LAYER_STYLES, ROAD_COLORS, type GridLayerId, type ToggleId } from '../lib/layers'
import type { LayerFeatureCollection, PlannerCandidate, Station } from '../lib/types'
import { BISHKEK_CENTER, EMPTY_FC, stationsToGeoJSON } from '../lib/mapData'

const EMPTY = EMPTY_FC

maplibregl.setWorkerUrl(workerUrl)

interface Props {
  stations: Station[]
  layerData: Partial<Record<ToggleId, LayerFeatureCollection>>
  visible: Record<ToggleId, boolean>
  basemapOn: boolean
  selectedId: string | null
  onSelect: (id: string | null) => void
  plannerCandidates?: PlannerCandidate[]
  onCandidateSelect?: (id: string | null) => void
  coverageMode?: boolean
  onBasemap?: (provider: 'openfreemap' | 'none', error?: string) => void
}

export function MapView({ stations, layerData, visible, basemapOn, selectedId, onSelect, onBasemap, plannerCandidates = [], onCandidateSelect, coverageMode = false }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MlMap | null>(null)
  const [ready, setReady] = useState(false)
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect
  const onCandidateSelectRef = useRef(onCandidateSelect)
  onCandidateSelectRef.current = onCandidateSelect

  // Create the map once.
  useEffect(() => {
    let cancelled = false
    let map: MlMap | null = null
    void loadBasemapStyle().then((base) => {
      if (cancelled || !container.current) return
      onBasemap?.(base.provider, base.error)
      const m = new maplibregl.Map({
        container: container.current,
        style: base.style,
        center: BISHKEK_CENTER,
        zoom: 10.6,
        minZoom: 7,
        maxZoom: 17,
        attributionControl: { compact: true, customAttribution: [DATA_ATTRIBUTION] },
      })
      map = m
      m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
      m.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left')
      m.on('load', () => {
        addDataLayers(m)
        mapRef.current = m
        setReady(true)
      })
      m.on('click', 'aw-stations', (e) => {
        const f = e.features?.[0] as MapGeoJSONFeature | undefined
        if (f) onSelectRef.current(String(f.properties?.id))
      })
      m.on('click', 'aw-stations-ring', (e) => {
        const f = e.features?.[0] as MapGeoJSONFeature | undefined
        if (f) onSelectRef.current(String(f.properties?.id))
      })
      m.on('click', (e) => {
        const hit = m.queryRenderedFeatures(e.point, { layers: ['aw-stations'] }) 
        if (!hit || hit.length === 0) onSelectRef.current(null)
      })
      m.on('mouseenter', 'aw-stations', () => (m.getCanvas().style.cursor = 'pointer'))
      m.on('mouseleave', 'aw-stations', () => (m.getCanvas().style.cursor = ''))
      m.on('click', 'aw-candidates', (e) => {
        const f = e.features?.[0] as MapGeoJSONFeature | undefined
        if (f) onCandidateSelectRef.current?.(String(f.properties?.candidate_id))
      })
      m.on('mouseenter', 'aw-candidates', () => (m.getCanvas().style.cursor = 'pointer'))
      m.on('mouseleave', 'aw-candidates', () => (m.getCanvas().style.cursor = ''))
    })
    return () => {
      cancelled = true
      mapRef.current = null
      map?.remove()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Push data and visibility into the map.
  useEffect(() => {
    const map = mapRef.current
    if (!ready || !map) return
    setData(map, 'aw-stations', stationsToGeoJSON(stations))
    const grid = GRID_LAYERS.find((g) => visible[g]) as GridLayerId | undefined
    const gridData = grid ? layerData[grid] : undefined
    setData(map, 'aw-grid', gridData ?? EMPTY)
    if (grid && gridData) {
      const style = LAYER_STYLES[grid]
      const field = style.valueField as string
      const paint = coverageMode
        ? ['match', ['get', 'coverage_status'], 'underserved', '#d97706', '#86b9cf'] as never
        : fillColorExpression(field, gridData.metadata?.stats?.[field], style.ramp as string[]) as never
      map.setPaintProperty('aw-grid-fill', 'fill-color', paint)
    }
    setVis(map, 'aw-grid-fill', Boolean(grid && gridData))
    setVis(map, 'aw-grid-line', Boolean(grid && gridData))
    setData(map, 'aw-roads', layerData.roads ?? EMPTY)
    setVis(map, 'aw-roads', visible.roads && Boolean(layerData.roads))
    setData(map, 'aw-context', layerData.context_features ?? EMPTY)
    for (const id of ['aw-context-fill', 'aw-context-line', 'aw-context-point']) setVis(map, id, visible.context_features && Boolean(layerData.context_features))
    setData(map, 'aw-boundary', layerData.administrative_boundary ?? EMPTY)
    setVis(map, 'aw-boundary', visible.administrative_boundary && Boolean(layerData.administrative_boundary))
    setVis(map, 'aw-stations', visible.stations)
    setVis(map, 'aw-stations-ring', visible.stations)
    // Basemap on/off: hide every layer that belongs to the OpenFreeMap sources.
    for (const l of map.getStyle().layers ?? []) {
      const src = (l as { source?: string }).source
      if (src === BASEMAP_SOURCE_ID || src === 'ne2_shaded') setVis(map, l.id, basemapOn)
    }
    map.setFilter('aw-stations-selected', ['==', ['get', 'id'], selectedId ?? ''])
    setData(map, 'aw-candidates', {
      type: 'FeatureCollection',
      features: plannerCandidates.map((candidate) => ({
        type: 'Feature',
        id: candidate.candidate_id,
        geometry: { type: 'Point', coordinates: [candidate.longitude, candidate.latitude] },
        properties: { candidate_id: candidate.candidate_id, rank: candidate.rank, score: candidate.priority_score },
      })),
    })
    setVis(map, 'aw-candidates', plannerCandidates.length > 0)
    map.setFilter('aw-candidates-selected', ['==', ['get', 'candidate_id'], selectedId ?? ''])
  }, [ready, stations, layerData, visible, basemapOn, selectedId, plannerCandidates, coverageMode])

  return <div ref={container} data-testid="map-container" className="h-full w-full" />
}

function setData(map: MlMap, id: string, data: unknown) {
  ;(map.getSource(id) as GeoJSONSource | undefined)?.setData(data as FeatureCollection)
}

function setVis(map: MlMap, id: string, on: boolean) {
  if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none')
}

function addDataLayers(map: MlMap) {
  for (const id of ['aw-grid', 'aw-roads', 'aw-context', 'aw-boundary', 'aw-stations', 'aw-candidates']) map.addSource(id, { type: 'geojson', data: EMPTY })

  map.addLayer({ id: 'aw-grid-fill', type: 'fill', source: 'aw-grid', layout: { visibility: 'none' }, paint: { 'fill-color': 'rgba(0,0,0,0)', 'fill-opacity': 0.72 } })
  map.addLayer({ id: 'aw-grid-line', type: 'line', source: 'aw-grid', layout: { visibility: 'none' }, paint: { 'line-color': '#ffffff', 'line-opacity': 0.25, 'line-width': 0.4 } })
  map.addLayer({ id: 'aw-boundary', type: 'line', source: 'aw-boundary', layout: { visibility: 'none' }, paint: { 'line-color': '#10264a', 'line-width': 1.4, 'line-dasharray': [3, 2] } })
  map.addLayer({
    id: 'aw-roads',
    type: 'line',
    source: 'aw-roads',
    layout: { visibility: 'none' },
    paint: {
      'line-color': ['match', ['get', 'road_group'], 'primary', ROAD_COLORS.primary, 'secondary', ROAD_COLORS.secondary, ROAD_COLORS.tertiary],
      'line-width': ['match', ['get', 'road_group'], 'primary', 2.2, 'secondary', 1.5, 0.9],
      'line-opacity': 0.8,
    },
  })
  map.addLayer({ id: 'aw-context-fill', type: 'fill', source: 'aw-context', filter: ['==', ['geometry-type'], 'Polygon'], layout: { visibility: 'none' }, paint: { 'fill-color': '#8f6234', 'fill-opacity': 0.3 } })
  map.addLayer({ id: 'aw-context-line', type: 'line', source: 'aw-context', filter: ['==', ['geometry-type'], 'LineString'], layout: { visibility: 'none' }, paint: { 'line-color': '#8f6234', 'line-width': 1.4 } })
  map.addLayer({ id: 'aw-context-point', type: 'circle', source: 'aw-context', filter: ['==', ['geometry-type'], 'Point'], layout: { visibility: 'none' }, paint: { 'circle-radius': 4, 'circle-color': '#8f6234', 'circle-stroke-color': '#fff', 'circle-stroke-width': 1 } })

  // Clarity sensors: filled green circles. Embassy (approximate coordinates): hollow ring so it is not read as a precise point.
  map.addLayer({
    id: 'aw-stations',
    type: 'circle',
    source: 'aw-stations',
    paint: {
      'circle-radius': 7,
      'circle-color': ['case', ['get', 'approximate'], '#ffffff', '#2a8670'],
      'circle-stroke-color': ['case', ['get', 'approximate'], '#10264a', '#ffffff'],
      'circle-stroke-width': ['case', ['get', 'approximate'], 2.5, 1.5],
    },
  })
  map.addLayer({ id: 'aw-stations-ring', type: 'circle', source: 'aw-stations', filter: ['==', ['get', 'approximate'], true], paint: { 'circle-radius': 14, 'circle-color': 'rgba(16,38,74,0.08)', 'circle-stroke-color': '#10264a', 'circle-stroke-width': 1, 'circle-stroke-opacity': 0.5 } })
  map.addLayer({ id: 'aw-stations-selected', type: 'circle', source: 'aw-stations', filter: ['==', ['get', 'id'], ''], paint: { 'circle-radius': 11, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#10264a', 'circle-stroke-width': 2.5 } })
  map.addLayer({ id: 'aw-candidates', type: 'circle', source: 'aw-candidates', paint: { 'circle-radius': 8, 'circle-color': '#d97706', 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2 } })
  map.addLayer({ id: 'aw-candidates-selected', type: 'circle', source: 'aw-candidates', filter: ['==', ['get', 'candidate_id'], ''], paint: { 'circle-radius': 12, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#d97706', 'circle-stroke-width': 3 } })
}
