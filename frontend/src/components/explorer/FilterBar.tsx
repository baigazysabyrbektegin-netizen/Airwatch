import type { Interval, Station } from '../../lib/types'

import { HOURLY_MAX_DAYS, validateFilters, type Filters } from '../../lib/filters'

interface Props {
  filters: Filters
  clarityStations: Station[]
  limits: { min: string; max: string } | null
  busy: boolean
  onChange: (f: Filters) => void
  onApply: () => void
}

const field = 'rounded border border-navy-200 bg-white px-2 py-1.5 text-sm'

export function FilterBar({ filters, clarityStations, limits, busy, onChange, onApply }: Props) {
  const problem = validateFilters(filters)
  const set = (p: Partial<Filters>) => onChange({ ...filters, ...p })
  return (
    <form
      aria-label="Filters"
      className="flex flex-wrap items-end gap-3 rounded-lg border border-navy-200 bg-white p-3"
      onSubmit={(e) => {
        e.preventDefault()
        if (!problem) onApply()
      }}
    >
      <label className="text-xs text-navy-700">
        <span className="mb-1 block font-medium">Data source</span>
        <select aria-label="Data source" className={field} value={filters.source} onChange={(e) => set({ source: e.target.value as Filters['source'] })}>
          <option value="embassy">US Embassy (reference monitor)</option>
          <option value="clarity">Clarity (low-cost sensors)</option>
        </select>
      </label>
      {filters.source === 'clarity' && (
        <label className="text-xs text-navy-700">
          <span className="mb-1 block font-medium">Clarity station</span>
          <select aria-label="Station" className={field} value={filters.stationId} onChange={(e) => set({ stationId: e.target.value })}>
            {clarityStations.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className="text-xs text-navy-700">
        <span className="mb-1 block font-medium">From</span>
        <input aria-label="Start date" type="date" className={field} value={filters.start} min={limits?.min} max={limits?.max} onChange={(e) => set({ start: e.target.value })} />
      </label>
      <label className="text-xs text-navy-700">
        <span className="mb-1 block font-medium">To</span>
        <input aria-label="End date" type="date" className={field} value={filters.end} min={limits?.min} max={limits?.max} onChange={(e) => set({ end: e.target.value })} />
      </label>
      <label className="text-xs text-navy-700">
        <span className="mb-1 block font-medium">Aggregation</span>
        <select aria-label="Aggregation" className={field} value={filters.interval} onChange={(e) => set({ interval: e.target.value as Interval })}>
          <option value="hourly">Hourly (max {HOURLY_MAX_DAYS} days)</option>
          <option value="daily">Daily</option>
          <option value="monthly">Monthly</option>
        </select>
      </label>
      <button type="submit" disabled={busy || problem !== null} className="rounded bg-navy-900 px-4 py-1.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50">
        {busy ? 'Loading…' : 'Apply filters'}
      </button>
      {problem && (
        <p role="note" className="basis-full text-xs text-sand-700">
          {problem}
        </p>
      )}
    </form>
  )
}
