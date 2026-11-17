import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../lib/apiClient'
import { downloadCsv } from '../lib/csv'
import { buildCandidateCsv, buildPeriodComparisonCsv, buildResearchSummary, buildStationComparisonCsv, deleteWorkspace, loadSavedWorkspaces, saveWorkspace, type SavedWorkspace, type WorkspaceSettings } from '../lib/workspace'
import { ComparePanel } from '../components/explorer/ComparePanel'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { CompareResponse, ExplorerResponse, PlannerCandidate, PlannerCandidatesResponse, PlannerOverview, Station } from '../lib/types'

type Load<T> = { status: 'loading' } | { status: 'error'; message: string; hint?: string } | { status: 'ok'; data: T }
const msg = (e: unknown) => e instanceof Error ? e.message : String(e)
const hint = (e: unknown) => (e as ApiError).hint
const defaultSettings = (stations: Station[]): WorkspaceSettings => {
  const embassy = stations.find((s) => s.network === 'us-embassy')
  const start = embassy?.data_period?.start?.slice(0, 10) ?? '2021-03-01'
  const end = embassy?.data_period?.end?.slice(0, 10) ?? '2021-03-07'
  return { title: 'Bishkek air-quality research', description: '', stationIds: stations.filter((s) => s.data_available).slice(0, 3).map((s) => s.id), start, end, periodAStart: start, periodAEnd: end, periodBStart: start, periodBEnd: end }
}
function ErrorBox({ error }: { error: { message: string; hint?: string } }) { return <div role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-900">{error.message}{error.hint && <div className="mt-1 text-xs">{error.hint}</div>}</div> }
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="block text-xs text-navy-700">{label}{children}</label> }
function fmt(value: number | null | undefined) { return value == null ? '—' : value.toFixed(2) }

export default function WorkspacePage() {
  const [stations, setStations] = useState<Load<Station[]>>({ status: 'loading' })
  const [settings, setSettings] = useState<WorkspaceSettings>({ title: '', description: '', stationIds: [], start: '', end: '', periodAStart: '', periodAEnd: '', periodBStart: '', periodBEnd: '' })
  const [saved, setSaved] = useState<SavedWorkspace[]>([])
  const [selectedSaved, setSelectedSaved] = useState('')
  const [series, setSeries] = useState<Load<Record<string, ExplorerResponse>>>({ status: 'loading' })
  const [period, setPeriod] = useState<Load<CompareResponse>>({ status: 'loading' })
  const [planner, setPlanner] = useState<Load<{ overview: PlannerOverview; data: PlannerCandidatesResponse }>>({ status: 'loading' })
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    // Load browser-local settings once; no raw datasets are persisted.
    // eslint-disable-next-line react/set-state-in-effect
    setSaved(loadSavedWorkspaces())
    api.stations().then((r) => { setStations({ status: 'ok', data: r.stations }); setSettings((s) => s.title ? s : defaultSettings(r.stations)) }).catch((e) => setStations({ status: 'error', message: msg(e), hint: hint(e) }))
  }, [])

  const stationList = stations.status === 'ok' ? stations.data : []
  const selectedStations = stationList.filter((s) => settings.stationIds.includes(s.id))
  const loadResearch = useCallback(() => {
    if (!settings.start || !settings.end || selectedStations.length === 0) return
    setBusy(true)
    setSeries({ status: 'loading' })
    const requests = selectedStations.map((station) => api.explorer({ source: station.network === 'clarity' ? 'clarity' : 'embassy', station_id: station.network === 'clarity' ? station.id : undefined, start_date: settings.start, end_date: settings.end, interval: 'daily' }).then((data) => [station.id, data] as const))
    Promise.all(requests).then((entries) => setSeries({ status: 'ok', data: Object.fromEntries(entries) })).catch((e) => setSeries({ status: 'error', message: msg(e), hint: hint(e) }))
    const stationId = selectedStations[0].id
    api.compare({ station_a: stationId, station_b: stationId, start_a: settings.periodAStart, end_a: settings.periodAEnd, start_b: settings.periodBStart, end_b: settings.periodBEnd, interval: 'daily' }).then((data) => setPeriod({ status: 'ok', data })).catch((e) => setPeriod({ status: 'error', message: msg(e), hint: hint(e) }))
    Promise.all([api.plannerOverview(), api.plannerCandidates({ n: 5 })]).then(([overview, data]) => setPlanner({ status: 'ok', data: { overview, data } })).catch((e) => setPlanner({ status: 'error', message: msg(e), hint: hint(e) })).finally(() => setBusy(false))
  }, [selectedStations, settings])

  const save = () => { const item = saveWorkspace(settings, saved.find((x) => x.id === selectedSaved)); setSaved(loadSavedWorkspaces()); setSelectedSaved(item.id) }
  const openSaved = (id: string) => { const item = saved.find((x) => x.id === id); if (item) { setSettings(item); setSelectedSaved(id) } }
  const removeSaved = () => { if (!selectedSaved) return; deleteWorkspace(selectedSaved); setSaved(loadSavedWorkspaces()); setSelectedSaved('') }
  const exportStations = () => {
    if (series.status !== 'ok') return
    const rows = selectedStations.flatMap((station) => (series.data[station.id]?.series ?? []).map((p) => ({ station, timestamp: p.timestamp, pm25: p.pm25, quality: p.quality_flag })))
    downloadCsv('airwatch_workspace_station_comparison.csv', buildStationComparisonCsv(rows, settings))
  }
  const exportCandidates = () => { if (planner.status === 'ok') downloadCsv('airwatch_workspace_sensor_candidates.csv', buildCandidateCsv(planner.data.data.candidates, settings.title)) }
  const exportPeriod = () => { if (period.status === 'ok') downloadCsv('airwatch_workspace_period_comparison.csv', buildPeriodComparisonCsv(period.data, settings.title)) }
  const exportSummary = () => {
    const findings: string[] = []
    if (period.status === 'ok' && period.data.difference.hourly_mean?.difference_b_minus_a != null) findings.push(`Period B minus Period A mean PM2.5: ${fmt(period.data.difference.hourly_mean.difference_b_minus_a)} µg/m³.`)
    if (planner.status === 'ok') findings.push(`${planner.data.overview.population_underserved_pct ?? 'Unavailable'}% of the 2019 gridded population is in cells outside the configured coverage radius.`)
    downloadCsv('airwatch_research_summary.txt', buildResearchSummary(settings, selectedStations, findings, ['Embassy data may have a 2023–2024 measurement-regime issue.', 'Clarity timestamps use an unresolved convention and are not force-aligned.', 'ERA5 is reanalysis, not a local weather-station record.', 'Planner candidates are approximate decision-support locations.']))
  }

  return (
    <main className="min-w-0 flex-1 overflow-y-auto bg-sand-50 p-4 md:p-6">
      <div className="mx-auto max-w-7xl space-y-4">
        <header><p className="text-xs font-semibold uppercase tracking-wide text-leaf-700">Reproducible research workspace</p><h1 className="text-2xl font-semibold text-navy-950">Research Workspace</h1><p className="mt-1 text-sm text-navy-600">Bring existing AirWatch evidence together without merging incompatible measurement sources or recalculating module results.</p></header>
        {stations.status === 'error' && <ErrorBox error={stations} />}
        <section className="rounded-lg border border-navy-200 bg-white p-4"><div className="flex flex-wrap items-end gap-3"><Field label="Research title"><input className="mt-1 block rounded border border-navy-200 p-2 text-sm" value={settings.title} onChange={(e) => setSettings({ ...settings, title: e.target.value })} /></Field><Field label="Description"><input className="mt-1 block rounded border border-navy-200 p-2 text-sm" value={settings.description} onChange={(e) => setSettings({ ...settings, description: e.target.value })} /></Field><button type="button" onClick={save} className="rounded bg-navy-900 px-3 py-2 text-sm text-white">Save comparison</button><select aria-label="Saved comparisons" value={selectedSaved} onChange={(e) => openSaved(e.target.value)} className="rounded border border-navy-200 p-2 text-sm"><option value="">Open saved comparison…</option>{saved.map((x) => <option key={x.id} value={x.id}>{x.title}</option>)}</select>{selectedSaved && <button type="button" onClick={removeSaved} className="rounded border border-red-300 px-3 py-2 text-sm text-red-800">Delete saved</button>}</div><p className="mt-2 text-xs text-navy-500">Saved settings are browser/device-specific local storage; raw datasets are not stored.</p></section>
        <section className="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]"><aside className="rounded-lg border border-navy-200 bg-white p-4"><h2 className="font-semibold text-navy-900">Research selections</h2><Field label="Stations"><div className="mt-1 max-h-48 space-y-1 overflow-auto">{stationList.filter((s) => s.data_available).map((s) => <label key={s.id} className="flex gap-2 text-xs"><input type="checkbox" checked={settings.stationIds.includes(s.id)} onChange={(e) => setSettings({ ...settings, stationIds: e.target.checked ? [...settings.stationIds, s.id] : settings.stationIds.filter((id) => id !== s.id) })} />{s.name} <span className="text-navy-500">({s.network})</span></label>)}</div></Field><Field label="Comparison period"><div className="mt-1 flex gap-1"><input aria-label="Research start" type="date" value={settings.start} onChange={(e) => setSettings({ ...settings, start: e.target.value })} /><input aria-label="Research end" type="date" value={settings.end} onChange={(e) => setSettings({ ...settings, end: e.target.value })} /></div></Field><div className="mt-3 space-y-1 text-xs"><b>Period A</b><input aria-label="Period A start" type="date" value={settings.periodAStart} onChange={(e) => setSettings({ ...settings, periodAStart: e.target.value })} /><input aria-label="Period A end" type="date" value={settings.periodAEnd} onChange={(e) => setSettings({ ...settings, periodAEnd: e.target.value })} /><b>Period B</b><input aria-label="Period B start" type="date" value={settings.periodBStart} onChange={(e) => setSettings({ ...settings, periodBStart: e.target.value })} /><input aria-label="Period B end" type="date" value={settings.periodBEnd} onChange={(e) => setSettings({ ...settings, periodBEnd: e.target.value })} /></div><button type="button" onClick={loadResearch} disabled={busy || selectedStations.length === 0} className="mt-3 w-full rounded bg-leaf-700 px-3 py-2 text-sm text-white disabled:opacity-50">{busy ? 'Loading research…' : 'Load research'}</button><div className="mt-3 space-y-1 text-xs text-navy-600"><button type="button" onClick={exportStations} className="mr-2 underline">Export station CSV</button><button type="button" onClick={exportCandidates} className="mr-2 underline">Export candidates</button><button type="button" onClick={exportSummary} className="underline">Export summary</button></div></aside>
          <div className="space-y-4"><section className="rounded-lg border border-navy-200 bg-white p-4"><h2 className="font-semibold text-navy-900">Research overview</h2><p className="mt-1 text-sm text-navy-600">{selectedStations.length} station(s) selected · {settings.start} to {settings.end}. Embassy and Clarity series remain separate.</p>{planner.status === 'ok' && <p className="mt-1 text-xs text-navy-500">Planner: {planner.data.overview.population_underserved_pct}% of 2019 population in cells outside the {planner.data.overview.coverage_radius_km} km coverage assumption.</p>}</section>
          {series.status === 'error' && <ErrorBox error={series} />}{series.status === 'ok' && <section className="rounded-lg border border-navy-200 bg-white p-4"><h2 className="font-semibold text-navy-900">Station comparison</h2><div className="h-56"><ResponsiveContainer width="100%" height="100%"><LineChart data={Array.from({ length: Math.max(...selectedStations.map((s) => series.data[s.id]?.series.length ?? 0), 0) }, (_, i) => ({ index: i, ...Object.fromEntries(selectedStations.map((s) => [`${s.id}`, series.data[s.id]?.series[i]?.pm25 ?? null])) }))}><CartesianGrid stroke="#e6ebf2" vertical={false} /><XAxis dataKey="index" /><YAxis /><Tooltip /><Line dataKey={selectedStations[0]?.id} name={selectedStations[0]?.name} stroke="#2a8670" dot={false} connectNulls={false} />{selectedStations.slice(1).map((s, i) => <Line key={s.id} dataKey={s.id} name={s.name} stroke={['#2f5fa8', '#d97706', '#8f6234', '#7c3aed'][i % 4]} dot={false} connectNulls={false} />)}</LineChart></ResponsiveContainer></div><div className="overflow-auto"><table className="w-full text-left text-xs"><thead><tr className="border-b"><th className="p-2">Station</th><th className="p-2">Network/data kind</th><th className="p-2">Valid observations</th><th className="p-2">Mean PM2.5</th><th className="p-2">Coverage notes</th></tr></thead><tbody>{selectedStations.map((s) => { const r = series.data[s.id]; const values = r?.series.map((x) => x.pm25).filter((x): x is number => x != null) ?? []; return <tr key={s.id} className="border-b"><td className="p-2">{s.name}</td><td className="p-2">{s.network}<br />{s.data_kind}</td><td className="p-2">{values.length}</td><td className="p-2">{values.length ? (values.reduce((a, b) => a + b, 0) / values.length).toFixed(1) : '—'} µg/m³</td><td className="p-2">{r?.warnings.map((w) => w.message).join(' ') || 'See source metadata.'}</td></tr> })}</tbody></table></div></section>}
          {period.status === 'ok' && <section><div className="mb-2 flex items-center justify-between"><h2 className="text-sm font-semibold text-navy-900">Period comparison</h2><button type="button" onClick={exportPeriod} className="text-xs underline">Export period CSV</button></div><ComparePanel data={period.data} /></section>}
          {planner.status === 'ok' && <section className="rounded-lg border border-navy-200 bg-white p-4"><h2 className="font-semibold text-navy-900">Sensor-planning analysis</h2><p className="text-xs text-navy-500">Existing Planner ranking, reused without recalculation. Candidates are approximate decision-support locations.</p><div className="mt-2 grid gap-2 md:grid-cols-2">{planner.data.data.candidates.map((c: PlannerCandidate) => <button type="button" key={c.candidate_id} className="rounded border border-navy-100 p-2 text-left text-xs hover:bg-sand-50"><b>#{c.rank} {c.candidate_id}</b> · score {c.priority_score.toFixed(3)}<br />{c.explanation}</button>)}</div></section>}</div></section>
        <section className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-xs text-amber-950"><h2 className="font-semibold">Methodology and provenance</h2><p className="mt-1">Measured Embassy and Clarity data are never combined. Clarity timestamp convention and the one-hour offset remain unresolved. ERA5 is reanalysis, CAMx/emissions are modelled, and population is a 2019 estimate. Embassy data has a potential 2023–2024 measurement-regime issue.</p></section>
      </div>
    </main>
  )
}
