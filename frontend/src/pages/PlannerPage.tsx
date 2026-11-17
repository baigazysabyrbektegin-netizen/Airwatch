import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../lib/apiClient'
import { MapView } from '../components/MapView'
import type { LayerFeatureCollection, PlannerCandidatesResponse, PlannerOverview, Station } from '../lib/types'
import type { ToggleId } from '../lib/layers'

type Load<T> = { status: 'loading' } | { status: 'error'; message: string; hint?: string } | { status: 'ok'; data: T }
const visible: Record<ToggleId, boolean> = { stations: true, population_grid: true, roads: false, camx_pm25: false, emissions_context: false, context_features: false, administrative_boundary: false }
const errorMessage = (e: unknown) => e instanceof Error ? e.message : String(e)

function ErrorBox({ error }: { error: { message: string; hint?: string } }) {
  return <div role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-900">{error.message}{error.hint && <div className="mt-1 text-xs">{error.hint}</div>}</div>
}

function number(value: number | null | undefined) {
  return value == null ? '—' : value.toLocaleString(undefined, { maximumFractionDigits: 1 })
}

export default function PlannerPage() {
  const [stations, setStations] = useState<Load<Station[]>>({ status: 'loading' })
  const [overview, setOverview] = useState<Load<PlannerOverview>>({ status: 'loading' })
  const [candidates, setCandidates] = useState<Load<PlannerCandidatesResponse>>({ status: 'loading' })
  const [coverage, setCoverage] = useState<Load<LayerFeatureCollection>>({ status: 'loading' })
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [n, setN] = useState(5)
  const [radius, setRadius] = useState(3)
  const [separation, setSeparation] = useState(2)
  const [exposure, setExposure] = useState(70)
  const [source, setSource] = useState(30)
  const [busy, setBusy] = useState(false)

  const load = useCallback((signal?: AbortSignal) => {
    setStations({ status: 'loading' })
    setOverview({ status: 'loading' })
    setCandidates({ status: 'loading' })
    setCoverage({ status: 'loading' })
    api.stations(false, signal).then((r) => setStations({ status: 'ok', data: r.stations })).catch((e) => !signal?.aborted && setStations({ status: 'error', message: errorMessage(e), hint: (e as ApiError).hint }))
    const params = { coverage_radius_km: radius }
    api.plannerOverview(params, signal).then((r) => setOverview({ status: 'ok', data: r })).catch((e) => !signal?.aborted && setOverview({ status: 'error', message: errorMessage(e), hint: (e as ApiError).hint }))
    api.plannerCandidates({ n, coverage_radius_km: radius, min_separation_km: separation, exposure_weight: exposure / 100, source_weight: source / 100 }, signal).then((r) => setCandidates({ status: 'ok', data: r })).catch((e) => !signal?.aborted && setCandidates({ status: 'error', message: errorMessage(e), hint: (e as ApiError).hint }))
    api.plannerCoverage(params, signal).then((r) => setCoverage({ status: 'ok', data: r })).catch((e) => !signal?.aborted && setCoverage({ status: 'error', message: errorMessage(e), hint: (e as ApiError).hint }))
  }, [exposure, n, radius, separation, source])

  useEffect(() => {
    const ctrl = new AbortController()
    // Initial load is intentionally tied to the current planning controls.
    // eslint-disable-next-line react/set-state-in-effect
    load(ctrl.signal)
    return () => ctrl.abort()
  }, [load])

  const recalculate = () => {
    setBusy(true)
    api.recalculatePlanner({ n, coverage_radius_km: radius, min_separation_km: separation, exposure_weight: exposure / 100, source_weight: source / 100 })
      .then((r) => setCandidates({ status: 'ok', data: r }))
      .then(() => api.plannerOverview({ coverage_radius_km: radius }).then((r) => setOverview({ status: 'ok', data: r })))
      .then(() => api.plannerCoverage({ coverage_radius_km: radius }).then((r) => setCoverage({ status: 'ok', data: r })))
      .catch((e) => setCandidates({ status: 'error', message: errorMessage(e), hint: (e as ApiError).hint }))
      .finally(() => setBusy(false))
  }

  const candidateList = candidates.status === 'ok' ? candidates.data.candidates : []
  const selected = candidateList.find((c) => c.candidate_id === selectedId) ?? null
  const mapData = coverage.status === 'ok' ? coverage.data : undefined
  const stationList = stations.status === 'ok' ? stations.data : []

  return (
    <main className="min-w-0 flex-1 overflow-y-auto bg-sand-50 p-4 md:p-6">
      <div className="mx-auto max-w-7xl space-y-4">
        <header><p className="text-xs font-semibold uppercase tracking-wide text-leaf-700">Research decision support</p><h1 className="text-2xl font-semibold text-navy-950">Sensor Planner</h1><p className="mt-1 text-sm text-navy-600">Rank approximate grid-cell locations for further investigation. These are not confirmed installation sites or exposure probabilities.</p></header>
        {overview.status === 'error' && <ErrorBox error={overview} />}
        {candidates.status === 'error' && <ErrorBox error={candidates} />}
        {overview.status === 'ok' && <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><div className="rounded-lg border border-navy-200 bg-white p-3"><p className="text-xs text-navy-500">Active Clarity stations</p><p className="text-2xl font-semibold text-navy-950">{overview.data.existing_clarity_stations}</p></div><div className="rounded-lg border border-navy-200 bg-white p-3"><p className="text-xs text-navy-500">Population in underserved cells</p><p className="text-2xl font-semibold text-navy-950">{number(overview.data.population_2019_underserved)}</p></div><div className="rounded-lg border border-navy-200 bg-white p-3"><p className="text-xs text-navy-500">Covered grid cells</p><p className="text-2xl font-semibold text-navy-950">{number(overview.data.covered_cell_pct)}%</p></div><div className="rounded-lg border border-navy-200 bg-white p-3"><p className="text-xs text-navy-500">Recommended candidates</p><p className="text-2xl font-semibold text-navy-950">{candidateList.length}</p></div></section>}
        <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <div className="relative min-h-[32rem] overflow-hidden rounded-lg border border-navy-200 bg-white">
            {coverage.status === 'loading' && <div role="status" className="absolute left-1/2 top-3 z-10 -translate-x-1/2 rounded bg-white px-3 py-1 text-sm shadow">Loading coverage…</div>}
            <MapView stations={stationList} layerData={mapData ? { population_grid: mapData } : {}} visible={visible} basemapOn selectedId={selectedId} onSelect={() => undefined} plannerCandidates={candidateList} onCandidateSelect={setSelectedId} coverageMode />
            <div className="absolute bottom-3 left-3 rounded border border-navy-200 bg-white/95 p-2 text-xs shadow"><div><span className="mr-1 inline-block h-3 w-3 bg-[#86b9cf]" />Covered under {radius} km assumption</div><div><span className="mr-1 inline-block h-3 w-3 bg-[#d97706]" />Underserved</div><div><span className="mr-1 inline-block h-3 w-3 rounded-full bg-[#d97706]" />Candidate</div></div>
          </div>
          <aside className="space-y-3 rounded-lg border border-navy-200 bg-white p-4">
            <h2 className="font-semibold text-navy-900">Planning controls</h2>
            <label className="block text-xs text-navy-700">Additional sensors: <b>{n}</b><input aria-label="Additional sensors" type="range" min="1" max="10" value={n} onChange={(e) => setN(Number(e.target.value))} className="w-full" /></label>
            <label className="block text-xs text-navy-700">Coverage assumption: <b>{radius} km</b><input aria-label="Coverage radius" type="range" min="1" max="10" step="0.5" value={radius} onChange={(e) => setRadius(Number(e.target.value))} className="w-full" /></label>
            <label className="block text-xs text-navy-700">Minimum separation: <b>{separation} km</b><input aria-label="Minimum separation" type="range" min="0" max="10" step="0.5" value={separation} onChange={(e) => setSeparation(Number(e.target.value))} className="w-full" /></label>
            <label className="block text-xs text-navy-700">Exposure weight: <b>{exposure}%</b><input aria-label="Exposure weight" type="range" min="0" max="100" value={exposure} onChange={(e) => { setExposure(Number(e.target.value)); setSource(100 - Number(e.target.value)) }} className="w-full" /></label>
            <p className="text-xs text-navy-500">Source/context weight: {source}%</p>
            <button type="button" onClick={recalculate} disabled={busy} className="w-full rounded bg-navy-900 px-3 py-2 text-sm text-white disabled:opacity-50">{busy ? 'Recalculating…' : 'Recalculate rankings'}</button>
            <p className="text-[11px] text-navy-500">Weights combine monitoring deficit with 2019 population/CAMx exposure context and emissions/road/environmental source context. They are planning assumptions.</p>
          </aside>
        </section>
        <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <section className="rounded-lg border border-navy-200 bg-white p-4"><h2 className="font-semibold text-navy-900">Candidate ranking</h2><div className="mt-2 overflow-auto"><table className="w-full text-left text-xs"><thead><tr className="border-b"><th className="p-2">Rank</th><th className="p-2">Cell</th><th className="p-2">Score</th><th className="p-2">Population</th><th className="p-2">Nearest station</th><th className="p-2">Reason</th></tr></thead><tbody>{candidateList.map((c) => <tr key={c.candidate_id} className={`cursor-pointer border-b hover:bg-sand-50 ${selectedId === c.candidate_id ? 'bg-sand-50' : ''}`} onClick={() => setSelectedId(c.candidate_id)}><td className="p-2 font-semibold">{c.rank}</td><td className="p-2">{c.candidate_id}<br /><span className="text-navy-500">{c.latitude.toFixed(3)}, {c.longitude.toFixed(3)}</span></td><td className="p-2">{c.priority_score.toFixed(3)}</td><td className="p-2">{number(c.population_2019)}</td><td className="p-2">{c.nearest_station_distance_km.toFixed(1)} km</td><td className="p-2">{c.explanation}</td></tr>)}</tbody></table></div></section>
          <section className="rounded-lg border border-navy-200 bg-white p-4"><h2 className="font-semibold text-navy-900">Candidate details</h2>{selected ? <><p className="mt-2 text-sm font-medium">{selected.candidate_id} · rank {selected.rank}</p><p className="mt-1 text-sm text-navy-700">{selected.explanation}</p><dl className="mt-3 space-y-1 text-xs">{Object.entries(selected.score_components).map(([key, value]) => <div key={key} className="flex justify-between gap-2"><dt className="text-navy-500">{key.replaceAll('_', ' ')}</dt><dd>{value == null ? 'unavailable' : value.toFixed(3)}</dd></div>)}</dl><p className="mt-3 text-xs text-navy-500">Confidence: {selected.data_confidence}. CAMx is modelled historical data; population is a 2019 estimate. Verify siting, access, power, security and local representativeness before installation.</p></> : <p className="mt-2 text-sm text-navy-600">Select a candidate on the map or in the table.</p>}</section>
        </section>
        <section className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950"><h2 className="font-semibold">Methodology and limitations</h2><p className="mt-1">Coverage is distance from active Clarity coordinates using a configurable planning radius. The US Embassy point is approximate and is shown separately, not treated as an equivalent verified station. Candidates are grid centroids and are selected greedily with a separation constraint; the algorithm is not globally optimal. No verified Bishkek boundary is available, so positive overlap with the available Kyrgyzstan polygons defines eligibility.</p></section>
      </div>
    </main>
  )
}
