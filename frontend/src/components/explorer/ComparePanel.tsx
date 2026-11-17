import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { formatNumber } from '../../lib/layers'
import type { CompareResponse, DiffEntry } from '../../lib/types'
import { QualityWarnings } from './Notices'

function label(p: CompareResponse['period_a']): string {
  return `${p.label}: ${p.station.name} (${p.query.effective_start} → ${p.query.effective_end})`
}

function Row({ name, d, unit, digits }: { name: string; d: DiffEntry | undefined; unit: string; digits?: number }) {
  if (!d) return null
  const diff = d.difference_b_minus_a
  const dg = digits ?? 1
  const shown = diff === null ? null : Number(Math.abs(diff).toFixed(dg))
  const sign = diff === null || shown === 0 ? '' : diff > 0 ? '+' : '−'
  const pct = d.percent_change === null ? '' : ` (${Math.round(d.percent_change) > 0 ? '+' : Math.round(d.percent_change) < 0 ? '−' : ''}${Math.abs(Math.round(d.percent_change))}%)`
  return (
    <tr className="border-t border-navy-100">
      <th scope="row" className="py-1 pr-3 text-left font-medium">
        {name} <span className="font-normal text-navy-500">{unit}</span>
      </th>
      <td className="pr-3">{formatNumber(d.a, digits ?? 1)}</td>
      <td className="pr-3">{formatNumber(d.b, digits ?? 1)}</td>
      <td>{shown === null ? 'n/a' : `${sign}${formatNumber(shown, dg)}${pct}`}</td>
    </tr>
  )
}

export function ComparePanel({ data }: { data: CompareResponse }) {
  const n = Math.max(data.period_a.series.length, data.period_b.series.length)
  const rows = Array.from({ length: n }, (_, i) => ({ index: i, a: data.period_a.series[i]?.pm25 ?? null, b: data.period_b.series[i]?.pm25 ?? null }))
  const d = data.difference
  const sa = data.period_a.summary
  const sb = data.period_b.summary
  return (
    <section aria-label="Period comparison" className="space-y-2 rounded-lg border border-navy-200 bg-white p-3">
      <div>
        <h2 className="text-sm font-semibold text-navy-900">Comparison of two periods</h2>
        <p className="text-[11px] text-navy-500">Measured PM2.5. {data.alignment.statement}</p>
      </div>
      <QualityWarnings warnings={data.warnings} />
      {d.available ? (
        <table className="w-full text-left text-xs text-navy-700" aria-label="Comparison statistics">
          <thead className="text-navy-500">
            <tr>
              <th className="pr-3">Statistic</th>
              <th className="pr-3">{label(data.period_a)}</th>
              <th className="pr-3">{label(data.period_b)}</th>
              <th>B − A</th>
            </tr>
          </thead>
          <tbody>
            <Row name="Mean (valid hourly values)" d={d.hourly_mean} unit="µg/m³" />
            <Row name="Median" d={d.median} unit="µg/m³" />
            <Row name="Mean of daily means" d={d.mean_of_daily_means} unit="µg/m³" />
            <Row name="95th percentile" d={d.p95} unit="µg/m³" />
            <Row name="Maximum" d={d.max} unit="µg/m³" />
            {Object.entries(d.weather ?? {}).map(([k, v]) => (
              <Row key={k} name={`ERA5 ${k.replace(/_/g, ' ')} (daily mean)`} d={v} unit="" />
            ))}
            <tr className="border-t border-navy-100">
              <th scope="row" className="py-1 pr-3 text-left font-medium">Valid days / coverage</th>
              <td className="pr-3">{sa?.daily?.n_valid_days} days · {formatNumber(sa?.coverage_pct ?? null)}%</td>
              <td className="pr-3">{sb?.daily?.n_valid_days} days · {formatNumber(sb?.coverage_pct ?? null)}%</td>
              <td />
            </tr>
          </tbody>
        </table>
      ) : (
        <p role="status" className="rounded border border-navy-100 bg-navy-50 p-2 text-xs text-navy-700">
          No difference is shown. {d.reason ?? ''}
        </p>
      )}
      <div className="h-56" data-testid="chart-compare">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 4, right: 12, bottom: 16, left: 0 }}>
            <CartesianGrid stroke="#e6ebf2" vertical={false} />
            <XAxis dataKey="index" tick={{ fontSize: 10 }} label={{ value: data.alignment.x_axis, position: 'insideBottom', offset: -8, style: { fontSize: 10 } }} />
            <YAxis tick={{ fontSize: 10 }} width={52} label={{ value: 'PM2.5 (µg/m³)', angle: -90, position: 'insideLeft', style: { fontSize: 10 } }} />
            <Tooltip formatter={(v) => [formatNumber(v as number | null), 'PM2.5 µg/m³']} />
            <Legend formatter={(value) => (value === 'a' ? label(data.period_a) : label(data.period_b))} wrapperStyle={{ fontSize: 11 }} />
            <Line dataKey="a" name="a" stroke="#2a8670" strokeWidth={1.6} dot={false} connectNulls={false} isAnimationActive={false} />
            <Line dataKey="b" name="b" stroke="#2f5fa8" strokeWidth={1.6} dot={false} connectNulls={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="text-[11px] text-navy-500">{data.notes.join(' ')}</p>
    </section>
  )
}
