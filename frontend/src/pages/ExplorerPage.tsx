import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../lib/apiClient'
import { buildCsv, csvFilename, downloadCsv } from '../lib/csv'
import type { CompareResponse, ExplorerResponse, Station, SummaryResponse } from '../lib/types'
import { ComparePanel } from '../components/explorer/ComparePanel'
import { PmChart, ScatterPanel, SeasonalCharts, WeatherCharts } from '../components/explorer/Charts'
import { FilterBar } from '../components/explorer/FilterBar'
import { validateFilters, type Filters } from '../lib/filters'
import { ProvenanceFooter, QualityWarnings } from '../components/explorer/Notices'
import { SummaryCards } from '../components/explorer/SummaryCards'

type Load<T> = { status: 'idle' } | { status: 'loading' } | { status: 'error'; message: string; hint?: string } | { status: 'ok'; data: T }
interface CompareInput {
  enabled: boolean
  stationB: string
  start: string
  end: string
}
interface Applied {
  filters: Filters
  compare: CompareInput | null
  nonce: number
}

const msg = (e: unknown) => (e instanceof Error ? e.message : String(e))
const hint = (e: unknown) => (e as { hint?: string }).hint
const day = (iso: string | null | undefined) => (iso ? iso.slice(0, 10) : '')

function ErrorBox({ title, message, hint: h }: { title: string; message: string; hint?: string }) {
  return (
    <div role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-900">
      <div className="font-medium">{title}</div>
      <div>{message}</div>
      {h && <div className="mt-1 text-xs">{h}</div>}
    </div>
  )
}

export default function ExplorerPage() {
  const [stations, setStations] = useState<Load<Station[]>>({ status: 'loading' })
  const [filters, setFilters] = useState<Filters | null>(null)
  const [compare, setCompare] = useState<CompareInput>({ enabled: false, stationB: '', start: '', end: '' })
  const [applied, setApplied] = useState<Applied | null>(null)
  const [explorer, setExplorer] = useState<Load<ExplorerResponse>>({ status: 'idle' })
  const [summary, setSummary] = useState<Load<SummaryResponse>>({ status: 'idle' })
  const [compared, setCompared] = useState<Load<CompareResponse>>({ status: 'idle' })

  const loadStations = useCallback(() => {
    setStations({ status: 'loading' })
    api
      .stations()
      .then((r) => setStations({ status: 'ok', data: r.stations }))
      .catch((e: unknown) => setStations({ status: 'error', message: msg(e), hint: hint(e) }))
  }, [])
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadStations()
  }, [loadStations])

  const list = useMemo(() => (stations.status === 'ok' ? stations.data : []), [stations])
  const embassy = list.find((s) => s.network === 'us-embassy')
  const clarity = useMemo(() => list.filter((s) => s.network === 'clarity' && s.data_available), [list])
  const stationFor = useCallback((f: Pick<Filters, 'source' | 'stationId'>) => (f.source === 'embassy' ? embassy : list.find((s) => s.id === f.stationId)), [embassy, list])
  const stationIdOf = (f: Filters) => (f.source === 'embassy' ? embassy?.id ?? 'us-embassy-bishkek' : f.stationId)

  // First load: Embassy, whole record, daily — then run it once so the page is not empty.
  useEffect(() => {
    if (stations.status !== 'ok' || filters || !embassy) return
    const f: Filters = { source: 'embassy', stationId: embassy.id, start: day(embassy.data_period?.start), end: day(embassy.data_period?.end), interval: 'daily' }
    setFilters(f)
    setApplied({ filters: f, compare: null, nonce: 1 })
  }, [stations, filters, embassy])

  const changeFilters = (next: Filters) => {
    if (!filters) return
    let f = next
    if (next.source !== filters.source || next.stationId !== filters.stationId) {
      // A different source/station has a different data period: reset the dates to it (the user can then narrow them).
      const sourceChanged = next.source !== filters.source
      const sid = sourceChanged ? (next.source === 'clarity' ? clarity[0]?.id ?? '' : embassy?.id ?? next.stationId) : next.stationId
      const st = stationFor({ source: next.source, stationId: sid })
      f = { ...next, stationId: sid, start: day(st?.data_period?.start) || next.start, end: day(st?.data_period?.end) || next.end }
    }
    setFilters(f)
  }

  const apply = () => {
    if (!filters || validateFilters(filters)) return
    const c = compare.enabled && compare.start && compare.end ? compare : null
    setApplied((a) => ({ filters: { ...filters }, compare: c, nonce: (a?.nonce ?? 0) + 1 }))
  }

  useEffect(() => {
    if (!applied) return
    const ctrl = new AbortController()
    const f = applied.filters
    const base = { source: f.source, station_id: f.source === 'clarity' ? f.stationId : undefined, start_date: f.start, end_date: f.end }
    setExplorer({ status: 'loading' })
    setSummary({ status: 'loading' })
    setCompared(applied.compare ? { status: 'loading' } : { status: 'idle' })
    api
      .explorer({ ...base, interval: f.interval }, ctrl.signal)
      .then((data) => setExplorer({ status: 'ok', data }))
      .catch((e: unknown) => !ctrl.signal.aborted && setExplorer({ status: 'error', message: msg(e), hint: hint(e) }))
    api
      .explorerSummary(base, ctrl.signal)
      .then((data) => setSummary({ status: 'ok', data }))
      .catch((e: unknown) => !ctrl.signal.aborted && setSummary({ status: 'error', message: msg(e), hint: hint(e) }))
    if (applied.compare) {
      const c = applied.compare
      api
        .compare(
          { station_a: stationIdOf(f), start_a: f.start, end_a: f.end, station_b: c.stationB || undefined, start_b: c.start, end_b: c.end, interval: f.interval === 'monthly' ? 'monthly' : 'daily' },
          ctrl.signal,
        )
        .then((data) => setCompared({ status: 'ok', data }))
        .catch((e: unknown) => !ctrl.signal.aborted && setCompared({ status: 'error', message: msg(e), hint: hint(e) }))
    }
    return () => ctrl.abort()
    // stationIdOf only depends on the station list, which is stable once loaded
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applied])

  const busy = explorer.status === 'loading' || summary.status === 'loading'
  const selected = filters ? stationFor(filters) : undefined
  const limits = selected?.data_period ? { min: day(selected.data_period.start), max: day(selected.data_period.end) } : null

  const exportCsv = () => {
    if (explorer.status !== 'ok') return
    downloadCsv(csvFilename(explorer.data), buildCsv(explorer.data))
  }

  return (
    <main className="min-h-0 flex-1 overflow-y-auto p-4">
      <div className="mx-auto max-w-6xl space-y-3">
        <header>
          <h1 className="text-xl font-semibold text-navy-900">Data &amp; Weather Explorer</h1>
          <p className="text-sm text-navy-700">
            Explore historical PM2.5 measurements, seasonal patterns and their association with ERA5 reanalysis weather. All data are historical (Embassy 2019–2024, Clarity 2021); nothing here is current or a forecast.
          </p>
        </header>

        {stations.status === 'loading' && (
          <p role="status" className="text-sm text-navy-700">
            Loading stations…
          </p>
        )}
        {stations.status === 'error' && (
          <div className="space-y-2">
            <ErrorBox title="Could not load stations" message={stations.message} hint={stations.hint} />
            <button type="button" onClick={loadStations} className="rounded border border-navy-200 bg-white px-3 py-1 text-sm">
              Retry
            </button>
          </div>
        )}

        {filters && (
          <>
            <FilterBar filters={filters} clarityStations={clarity} limits={limits} busy={busy} onChange={changeFilters} onApply={apply} />

            <div className="rounded-lg border border-navy-200 bg-white p-3">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={compare.enabled}
                  onChange={(e) => setCompare({ ...compare, enabled: e.target.checked })}
                  className="accent-leaf-600"
                />
                Compare with a second period or station
              </label>
              {compare.enabled && (
                <div className="mt-2 flex flex-wrap items-end gap-3 text-xs text-navy-700">
                  <label>
                    <span className="mb-1 block font-medium">Station for period B</span>
                    <select aria-label="Station for period B" className="rounded border border-navy-200 bg-white px-2 py-1.5 text-sm" value={compare.stationB} onChange={(e) => setCompare({ ...compare, stationB: e.target.value })}>
                      <option value="">Same as period A</option>
                      {embassy && <option value={embassy.id}>US Embassy</option>}
                      {clarity.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span className="mb-1 block font-medium">Period B from</span>
                    <input aria-label="Period B start" type="date" className="rounded border border-navy-200 bg-white px-2 py-1.5 text-sm" value={compare.start} onChange={(e) => setCompare({ ...compare, start: e.target.value })} />
                  </label>
                  <label>
                    <span className="mb-1 block font-medium">Period B to</span>
                    <input aria-label="Period B end" type="date" className="rounded border border-navy-200 bg-white px-2 py-1.5 text-sm" value={compare.end} onChange={(e) => setCompare({ ...compare, end: e.target.value })} />
                  </label>
                  <p className="basis-full text-[11px] text-navy-500">Period A is the range chosen above. Press “Apply filters” to run the comparison. Different networks are compared statistically only.</p>
                </div>
              )}
            </div>
          </>
        )}

        {busy && (
          <p role="status" className="rounded border border-navy-100 bg-white p-3 text-sm text-navy-700">
            Loading historical observations…
          </p>
        )}
        {explorer.status === 'error' && <ErrorBox title="Could not load the observations" message={explorer.message} hint={explorer.hint} />}
        {summary.status === 'error' && <ErrorBox title="Could not load the summary" message={summary.message} hint={summary.hint} />}

        {explorer.status === 'ok' && (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs text-navy-700">
                <strong>{explorer.data.station.name}</strong> · {explorer.data.station.data_kind} · {explorer.data.query.effective_start} → {explorer.data.query.effective_end} · {explorer.data.interval}
              </p>
              <button type="button" onClick={exportCsv} disabled={explorer.data.series.length === 0} className="rounded border border-navy-200 bg-white px-3 py-1 text-sm disabled:opacity-50">
                Export CSV
              </button>
            </div>
            <QualityWarnings warnings={explorer.data.warnings} />
            {explorer.data.empty ? (
              <div role="status" className="rounded-lg border border-navy-200 bg-white p-6 text-center text-sm text-navy-700">
                <div className="font-medium text-navy-900">No observations to show</div>
                <div className="mt-1">{explorer.data.empty_reason ?? 'No valid PM2.5 observations in the selected range.'}</div>
                <div className="mt-1 text-xs text-navy-500">Try a wider date range or another station.</div>
              </div>
            ) : (
              <>
                {summary.status === 'ok' && !summary.data.empty && <SummaryCards summary={summary.data} />}
                <PmChart data={explorer.data} />
                <WeatherCharts data={explorer.data} />
                {summary.status === 'ok' && !summary.data.empty && <SeasonalCharts summary={summary.data} />}
                <ScatterPanel data={explorer.data} />
              </>
            )}
          </>
        )}

        {compared.status === 'loading' && (
          <p role="status" className="text-sm text-navy-700">
            Loading comparison…
          </p>
        )}
        {compared.status === 'error' && <ErrorBox title="Could not compare the periods" message={compared.message} hint={compared.hint} />}
        {compared.status === 'ok' && <ComparePanel data={compared.data} />}

        {explorer.status === 'ok' && <ProvenanceFooter data={explorer.data} />}
      </div>
    </main>
  )
}
