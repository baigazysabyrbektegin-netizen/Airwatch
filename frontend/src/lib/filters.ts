import type { Interval } from './types'

export interface Filters {
  source: 'embassy' | 'clarity'
  stationId: string
  start: string
  end: string
  interval: Interval
}

export const HOURLY_MAX_DAYS = 120

export function daysBetween(start: string, end: string): number {
  const a = Date.parse(start)
  const b = Date.parse(end)
  if (Number.isNaN(a) || Number.isNaN(b)) return 0
  return Math.round((b - a) / 86_400_000) + 1
}

export function validateFilters(f: Filters): string | null {
  if (!f.start || !f.end) return 'Choose a start and an end date.'
  if (f.start > f.end) return 'The start date must not be later than the end date.'
  if (f.source === 'clarity' && !f.stationId) return 'Choose a Clarity station.'
  if (f.interval === 'hourly' && daysBetween(f.start, f.end) > HOURLY_MAX_DAYS) return `Hourly data is limited to ${HOURLY_MAX_DAYS} days. Choose a shorter range or daily/monthly.`
  return null
}

