import { useEffect, useState } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../lib/apiClient'
import { formatNumber } from '../lib/layers'
import type { MeasurementsResponse, Station } from '../lib/types'
import { HistoricalBadge, KindBadge } from './Badge'

type Series = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ok'; data: MeasurementsResponse }

function day(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 10) : '—'
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <>
      <dt className="text-navy-500">{k}</dt>
      <dd className="min-w-0 break-words">{v}</dd>
    </>
  )
}

export function StationPanel({ station, onClose }: { station: Station; onClose: () => void }) {
  const [series, setSeries] = useState<Series>({ status: 'loading' })

  useEffect(() => {
    if (!station.data_available) return
    const ctrl = new AbortController()
    api
      .measurements(station.id, 'monthly', undefined, ctrl.signal)
      .then((data) => setSeries({ status: 'ok', data }))
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return
        setSeries({ status: 'error', message: err instanceof Error ? err.message : String(err) })
      })
    return () => ctrl.abort()
  }, [station.id, station.data_available])

  const isEmbassy = station.network !== 'clarity'
  const chart =
    series.status === 'ok'
      ? series.data.records.map((r) => ({ month: r.timestamp.slice(0, 7), pm25: r.pm25_valid_ugm3 }))
      : []

  return (
    <section aria-label="Selected station" className="flex min-h-0 w-full flex-col overflow-y-auto rounded-lg border border-navy-200 bg-white p-4 shadow">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold leading-tight">{station.name}</h2>
          <div className="mt-1 flex flex-wrap items-center gap-1">
            <KindBadge dataKind={station.data_kind} />
            <HistoricalBadge />
            <span className="text-[11px] text-navy-500">{station.grade}</span>
          </div>
        </div>
        <button type="button" onClick={onClose} aria-label="Close station panel" className="rounded px-2 py-1 text-navy-500 hover:bg-navy-100">✕</button>
      </div>

      {isEmbassy && (
        <p className="mt-3 rounded border border-sand-700/30 bg-sand-100 p-2 text-xs text-sand-700">
          Coordinates are approximate{station.location_note ? ` — ${station.location_note}` : ''}. Treat the position as indicative only.
        </p>
      )}

      <dl className="mt-3 grid grid-cols-[7.5rem_1fr] gap-x-3 gap-y-1 text-xs">
        <Row k="Network" v={station.network} />
        <Row k="Sensor type" v={station.sensor_type} />
        <Row k="Location" v={station.latitude !== null ? `${station.latitude?.toFixed(4)}, ${station.longitude?.toFixed(4)} (${station.location_status})` : 'unknown'} />
        <Row k="Data period" v={station.data_available ? `${day(station.data_period?.start)} → ${day(station.data_period?.end)}` : 'no data in the processed dataset'} />
        <Row k="Valid readings" v={formatNumber(station.n_valid_readings, 0)} />
        <Row k="Coverage" v={station.coverage_pct === null ? 'not computed' : `${formatNumber(station.coverage_pct)}% of the period`} />
        <Row k="Timestamps" v={station.timestamp_convention} />
        <Row k="Vintage" v={station.vintage} />
      </dl>

      {station.quality && (
        <div className="mt-3 text-xs">
          <h3 className="font-semibold text-navy-700">Quality information</h3>
          <dl className="mt-1 grid grid-cols-[9rem_1fr] gap-x-3 gap-y-0.5">
            {Object.entries(station.quality).map(([k, v]) => (
              <Row key={k} k={k.replace(/_/g, ' ')} v={typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v)} />
            ))}
          </dl>
        </div>
      )}

      {station.notes.length > 0 && (
        <ul className="mt-3 list-disc space-y-1 pl-4 text-xs text-navy-700">
          {station.notes.map((n, i) => <li key={i}>{n}</li>)}
        </ul>
      )}

      <div className="mt-4">
        <h3 className="text-xs font-semibold text-navy-700">Monthly mean PM2.5 (µg/m³)</h3>
        {!station.data_available && <p className="mt-1 text-xs text-navy-500">No measurements available for this device.</p>}
        {station.data_available && series.status === 'loading' && <p role="status" className="mt-2 text-xs text-navy-500">Loading measurements…</p>}
        {series.status === 'error' && station.data_available && <p role="alert" className="mt-2 text-xs text-red-700">Could not load measurements: {series.message}</p>}
        {series.status === 'ok' && (
          <>
            {chart.length === 0 ? (
              <p className="mt-2 text-xs text-navy-500">No months met the completeness rule (≥75% of eligible days).</p>
            ) : (
              <div className="mt-2 h-40" data-testid="station-chart">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chart} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
                    <CartesianGrid stroke="#e6ebf2" vertical={false} />
                    <XAxis dataKey="month" tick={{ fontSize: 10 }} minTickGap={24} />
                    <YAxis tick={{ fontSize: 10 }} />
                    <Tooltip formatter={(v) => [formatNumber(v as number | null), 'PM2.5 µg/m³']} />
                    <Line type="monotone" dataKey="pm25" stroke="#2a8670" strokeWidth={2} dot={{ r: 2 }} connectNulls={false} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
            <p className="mt-1 text-[11px] text-navy-500">{series.data.timestamp_basis}</p>
            {series.data.notes.map((n, i) => <p key={i} className="mt-1 text-[11px] text-navy-500">{n}</p>)}
          </>
        )}
      </div>
    </section>
  )
}
