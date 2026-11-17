import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts'
import { formatNumber } from '../../lib/layers'
import type { ExplorerResponse, SummaryResponse, WeatherVar } from '../../lib/types'

const GRID = '#e6ebf2'
const PM = '#2a8670'
const TICK = { fontSize: 10 }

function Panel({ title, subtitle, testId, children, height = 'h-64' }: { title: string; subtitle: string; testId: string; children: React.ReactNode; height?: string }) {
  return (
    <section aria-label={title} className="rounded-lg border border-navy-200 bg-white p-3">
      <h2 className="text-sm font-semibold text-navy-900">{title}</h2>
      <p className="text-[11px] text-navy-500">{subtitle}</p>
      <div className={`mt-2 ${height}`} data-testid={testId}>
        {children}
      </div>
    </section>
  )
}

const shortTime = (t: string) => (t.length > 10 ? t.slice(5, 16).replace('T', ' ') : t)
const pmLabel = (d: ExplorerResponse) => `${d.source === 'embassy' ? 'US Embassy reference monitor' : 'Clarity low-cost sensor'} · ${d.station.name} · measured PM2.5, ${d.interval}`

// ------------------------------------------------------------------------------------------------ Chart 1
export function PmChart({ data }: { data: ExplorerResponse }) {
  const rows = data.series.map((p) => ({ t: p.timestamp, pm25: p.pm25, flag: p.quality_flag }))
  return (
    <Panel title="Historical PM2.5" subtitle={`${pmLabel(data)}. Gaps are real missing data and are not filled or drawn as zero.`} testId="chart-pm25">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="t" tick={TICK} tickFormatter={shortTime} minTickGap={40} />
          <YAxis tick={TICK} label={{ value: 'PM2.5 (µg/m³)', angle: -90, position: 'insideLeft', style: { fontSize: 10 } }} width={52} />
          <Tooltip formatter={(v) => [formatNumber(v as number | null), 'PM2.5 µg/m³']} labelFormatter={(l) => String(l)} />
          <Line type="monotone" dataKey="pm25" stroke={PM} strokeWidth={1.6} dot={rows.length < 60 ? { r: 2 } : false} connectNulls={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </Panel>
  )
}

// ------------------------------------------------------------------------------------------------ Chart 2
const WEATHER_PANELS: { v: WeatherVar; title: string; color: string; bar?: boolean }[] = [
  { v: 'temperature_2m', title: 'Temperature at 2 m', color: '#8f6234' },
  { v: 'wind_speed_10m', title: 'Wind speed at 10 m', color: '#10264a' },
  { v: 'relative_humidity_2m', title: 'Relative humidity at 2 m', color: '#2f5fa8' },
  { v: 'precipitation', title: 'Precipitation', color: '#5b8ccb', bar: true },
]

export function WeatherCharts({ data }: { data: ExplorerResponse }) {
  if (!data.weather?.included) {
    return (
      <section aria-label="Weather conditions" className="rounded-lg border border-navy-200 bg-white p-3 text-sm text-navy-700">
        <h2 className="font-semibold text-navy-900">Weather conditions</h2>
        <p className="mt-1" role="status">
          ERA5 weather is not available{data.weather?.unavailable ? ': the weather file has not been created yet (python -m pipeline.process_weather)' : ' for this view'}.
        </p>
      </section>
    )
  }
  const rows = data.series.map((p) => ({ t: p.timestamp, ...p.weather }))
  const agg = data.interval === 'hourly' ? 'hourly values' : data.interval === 'daily' ? 'daily means (precipitation: daily sum)' : 'monthly means (precipitation: monthly total)'
  return (
    <section aria-label="Weather conditions" className="space-y-2">
      <div>
        <h2 className="text-sm font-semibold text-navy-900">Weather conditions</h2>
        <p className="text-[11px] text-navy-500">ERA5 reanalysis via Open-Meteo, {agg}. Modelled historical weather on a ~25 km grid — not a forecast and not a local station measurement.</p>
      </div>
      <div className="grid gap-2 md:grid-cols-2">
        {WEATHER_PANELS.map((p) => {
          const meta = data.weather?.variables?.[p.v]
          const unit = meta?.unit ?? ''
          return (
            <div key={p.v} className="rounded-lg border border-navy-200 bg-white p-3" data-testid={`chart-weather-${p.v}`}>
              <h3 className="text-xs font-semibold text-navy-900">
                {p.title} <span className="font-normal text-navy-500">({unit})</span>
              </h3>
              <div className="mt-1 h-40">
                <ResponsiveContainer width="100%" height="100%">
                  {p.bar ? (
                    <BarChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: -10 }}>
                      <CartesianGrid stroke={GRID} vertical={false} />
                      <XAxis dataKey="t" tick={TICK} tickFormatter={shortTime} minTickGap={40} />
                      <YAxis tick={TICK} />
                      <Tooltip formatter={(v) => [formatNumber(v as number | null), `${p.title} ${unit}`]} />
                      <Bar dataKey={p.v} fill={p.color} isAnimationActive={false} />
                    </BarChart>
                  ) : (
                    <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: -10 }}>
                      <CartesianGrid stroke={GRID} vertical={false} />
                      <XAxis dataKey="t" tick={TICK} tickFormatter={shortTime} minTickGap={40} />
                      <YAxis tick={TICK} />
                      <Tooltip formatter={(v) => [formatNumber(v as number | null), `${p.title} ${unit}`]} />
                      <Line type="monotone" dataKey={p.v} stroke={p.color} strokeWidth={1.4} dot={false} connectNulls={false} isAnimationActive={false} />
                    </LineChart>
                  )}
                </ResponsiveContainer>
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

// ------------------------------------------------------------------------------------------------ Chart 3
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export function SeasonalCharts({ summary }: { summary: SummaryResponse }) {
  const monthly = (summary.monthly ?? []).map((m) => ({ t: m.year_month, pm25: m.mean }))
  const clim = (summary.climatology ?? []).map((c) => ({ t: MONTHS[c.month - 1], pm25: c.mean, n: c.n_months }))
  const seasons = (summary.seasonal ?? []).map((s) => ({ t: s.season, pm25: s.mean_of_daily_means, n: s.n_valid_days, label: s.label }))
  const byYear = (summary.seasonal_by_year ?? []).filter((s) => s.mean_of_daily_means !== null || s.n_valid_days > 0)
  return (
    <section aria-label="Seasonal patterns" className="space-y-2">
      <div>
        <h2 className="text-sm font-semibold text-navy-900">Seasonal patterns</h2>
        <p className="text-[11px] text-navy-500">
          Measured PM2.5 from the selected station. Months need ≥75% of days valid and days ≥18 valid hours; otherwise they are left blank. Winter = Dec–Feb.
        </p>
      </div>
      <div className="grid gap-2 lg:grid-cols-3">
        <div className="rounded-lg border border-navy-200 bg-white p-3" data-testid="chart-monthly-trend">
          <h3 className="text-xs font-semibold text-navy-900">Monthly average (trend)</h3>
          <div className="mt-1 h-44">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={monthly} margin={{ top: 4, right: 8, bottom: 0, left: -10 }}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="t" tick={TICK} minTickGap={30} />
                <YAxis tick={TICK} />
                <Tooltip formatter={(v) => [formatNumber(v as number | null), 'PM2.5 µg/m³']} />
                <Line type="monotone" dataKey="pm25" stroke={PM} strokeWidth={1.6} dot={{ r: 2 }} connectNulls={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="rounded-lg border border-navy-200 bg-white p-3" data-testid="chart-climatology">
          <h3 className="text-xs font-semibold text-navy-900">Average by calendar month</h3>
          <div className="mt-1 h-44">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={clim} margin={{ top: 4, right: 8, bottom: 0, left: -10 }}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="t" tick={TICK} />
                <YAxis tick={TICK} />
                <Tooltip formatter={(v, _n, item) => [`${formatNumber(v as number | null)} (${(item.payload as { n: number }).n} month(s) averaged)`, 'PM2.5 µg/m³']} />
                <Bar dataKey="pm25" fill={PM} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="rounded-lg border border-navy-200 bg-white p-3" data-testid="chart-seasons">
          <h3 className="text-xs font-semibold text-navy-900">Seasonal comparison</h3>
          <div className="mt-1 h-44">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={seasons} margin={{ top: 4, right: 8, bottom: 0, left: -10 }}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="t" tick={TICK} />
                <YAxis tick={TICK} />
                <Tooltip formatter={(v, _n, item) => [`${formatNumber(v as number | null)} (${(item.payload as { n: number }).n} valid days)`, 'mean of daily means µg/m³']} />
                <Bar dataKey="pm25" fill="#2f5fa8" isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
      {byYear.length > 0 && (
        <details className="rounded border border-navy-100 bg-white p-3 text-xs text-navy-700">
          <summary className="cursor-pointer font-medium">Season by year ({byYear.length})</summary>
          <table className="mt-2 w-full text-left">
            <thead className="text-navy-500">
              <tr>
                <th className="pr-3">Season</th>
                <th className="pr-3">Mean of daily means (µg/m³)</th>
                <th>Valid days</th>
              </tr>
            </thead>
            <tbody>
              {byYear.map((s) => (
                <tr key={`${s.season_year}-${s.season}`}>
                  <td className="pr-3">{s.label}</td>
                  <td className="pr-3">{s.mean_of_daily_means === null ? 'not enough valid days' : formatNumber(s.mean_of_daily_means)}</td>
                  <td>
                    {s.n_valid_days} / {s.eligible_days}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </section>
  )
}

// ------------------------------------------------------------------------------------------------ Chart 4
const SCATTER: { v: WeatherVar; label: string }[] = [
  { v: 'temperature_2m', label: 'Temperature at 2 m' },
  { v: 'wind_speed_10m', label: 'Wind speed at 10 m' },
  { v: 'relative_humidity_2m', label: 'Relative humidity at 2 m' },
  { v: 'precipitation', label: 'Precipitation' },
]

export function ScatterPanel({ data }: { data: ExplorerResponse }) {
  const [v, setV] = useState<WeatherVar>('temperature_2m')
  if (!data.weather?.included) return null
  const unit = data.weather.variables?.[v]?.unit ?? ''
  const pts = data.series.filter((p) => p.pm25 !== null && p.weather[v] !== null && p.weather[v] !== undefined).map((p) => ({ x: p.weather[v] as number, y: p.pm25 as number }))
  const rel = data.relationships.variables?.[v]
  return (
    <section aria-label="Pollution and weather relationship" className="rounded-lg border border-navy-200 bg-white p-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-navy-900">Pollution and weather relationship</h2>
          <p className="text-[11px] text-navy-500">
            Measured PM2.5 vs ERA5 reanalysis weather, {data.interval} pairs. {data.alignment.statement}
          </p>
        </div>
        <label className="text-xs text-navy-700">
          <span className="mr-2 font-medium">Weather variable</span>
          <select aria-label="Weather variable" className="rounded border border-navy-200 bg-white px-2 py-1 text-sm" value={v} onChange={(e) => setV(e.target.value as WeatherVar)}>
            {SCATTER.map((s) => (
              <option key={s.v} value={s.v}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="mt-2 h-64" data-testid="chart-scatter">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 4, right: 12, bottom: 16, left: 0 }}>
            <CartesianGrid stroke={GRID} />
            <XAxis type="number" dataKey="x" name={SCATTER.find((s) => s.v === v)?.label} tick={TICK} label={{ value: `${SCATTER.find((s) => s.v === v)?.label} (${unit})`, position: 'insideBottom', offset: -8, style: { fontSize: 10 } }} />
            <YAxis type="number" dataKey="y" name="PM2.5" tick={TICK} width={52} label={{ value: 'PM2.5 (µg/m³)', angle: -90, position: 'insideLeft', style: { fontSize: 10 } }} />
            <Tooltip cursor={{ strokeDasharray: '3 3' }} />
            <Scatter data={pts} fill={PM} fillOpacity={0.5} isAnimationActive={false} />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-2 text-xs text-navy-700" role="status" data-testid="scatter-stats">
        <strong>n = {rel?.n_pairs ?? pts.length}</strong> valid pairs.{' '}
        {rel?.enough_pairs ? (
          <>
            Pearson r = {rel.pearson_r === null ? 'n/a' : rel.pearson_r.toFixed(2)}; Spearman ρ = {rel.spearman_rho === null ? 'n/a' : rel.spearman_rho.toFixed(2)}.
          </>
        ) : (
          <span>{rel?.reason ?? 'Not enough valid pairs for a correlation.'}</span>
        )}
      </div>
      <p className="mt-1 text-[11px] text-navy-500">{data.relationships.note}</p>
    </section>
  )
}
