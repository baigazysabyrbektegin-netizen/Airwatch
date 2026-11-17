import { formatNumber } from '../../lib/layers'
import type { SummaryResponse } from '../../lib/types'

function Card({ label, value, unit, hint }: { label: string; value: string; unit?: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-navy-200 bg-white p-3">
      <div className="text-xs font-medium text-navy-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-navy-900">
        {value}
        {unit && <span className="ml-1 text-xs font-normal text-navy-500">{unit}</span>}
      </div>
      {hint && <div className="mt-0.5 text-[11px] text-navy-500">{hint}</div>}
    </div>
  )
}

const words = (k: string) => k.replace(/_/g, ' ')

export function SummaryCards({ summary }: { summary: SummaryResponse }) {
  if (!summary.stats) return null
  const s = summary.stats
  const o = summary.observations
  return (
    <section aria-label="Summary" className="space-y-2">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-5">
        <Card label="Average PM2.5" value={formatNumber(s.mean)} unit="µg/m³" hint="mean of valid hourly values" />
        <Card label="Median PM2.5" value={formatNumber(s.median)} unit="µg/m³" />
        <Card label="Maximum PM2.5" value={formatNumber(s.max)} unit="µg/m³" hint={`minimum ${formatNumber(s.min)}`} />
        <Card label="Valid observations" value={formatNumber(summary.n_valid ?? null, 0)} hint={`of ${formatNumber(summary.expected_hours ?? null, 0)} hours expected`} />
        <Card label="Data coverage" value={summary.coverage_pct == null ? 'no data' : `${formatNumber(summary.coverage_pct)}%`} hint="valid / expected hours" />
      </div>
      <details className="rounded border border-navy-100 bg-white p-3 text-xs text-navy-700">
        <summary className="cursor-pointer font-medium">How observations were counted</summary>
        <p className="mt-2">
          Percentiles (µg/m³): 5th {formatNumber(s.p05)} · 25th {formatNumber(s.p25)} · 75th {formatNumber(s.p75)} · 95th {formatNumber(s.p95)} · 99th {formatNumber(s.p99)}. {summary.stats_basis}.
        </p>
        {o && (
          <ul className="mt-2 list-disc space-y-1 pl-4">
            <li>
              <strong>Valid, used in statistics:</strong> {formatNumber(o.valid, 0)} of {formatNumber(o.expected_hours, 0)} hours.
            </li>
            <li>
              <strong>Excluded:</strong> {formatNumber(o.excluded.total ?? 0, 0)} hours (
              {Object.entries(o.excluded)
                .filter(([k]) => k !== 'total')
                .map(([k, v]) => `${words(k)}: ${v}`)
                .join(', ')}
              ).
            </li>
            <li>
              <strong>Suspect:</strong> {o.suspect.excluded_suspect_hours} hours excluded as suspect; flagged but still included:{' '}
              {Object.entries(o.suspect.flagged_but_included)
                .map(([k, v]) => `${words(k)}: ${v}`)
                .join(', ')}
              .
            </li>
            <li>
              <strong>Imputed:</strong> {o.imputed.hours_with_feature_interpolation} hours have an interpolated model-feature value; used in these statistics: {o.imputed.used_in_statistics ? 'yes' : 'no'}.
            </li>
            {o.negative_clipped_to_zero > 0 && <li>{o.negative_clipped_to_zero} valid readings were negative and are clipped to 0 in the model-ready value (originals are kept in the export).</li>}
          </ul>
        )}
        <p className="mt-2 text-navy-500">{summary.coverage_definition}</p>
      </details>
    </section>
  )
}
