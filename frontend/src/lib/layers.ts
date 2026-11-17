// Map layer configuration: what can be toggled, how each layer is coloured, and how legends are built.
// Colour scales are sequential (not red/amber/green) so they do not imply health thresholds.

import type { FieldStats, LayerInfo } from './types'

export type ToggleId = 'stations' | 'population_grid' | 'roads' | 'camx_pm25' | 'emissions_context' | 'context_features' | 'administrative_boundary'
export type GridLayerId = 'population_grid' | 'camx_pm25' | 'emissions_context'

export const GRID_LAYERS: GridLayerId[] = ['population_grid', 'camx_pm25', 'emissions_context']

export interface LayerStyle {
  id: ToggleId
  label: string
  short: string
  valueField?: string
  ramp?: string[]
  unitLabel?: string
}

export const LAYER_STYLES: Record<ToggleId, LayerStyle> = {
  stations: { id: 'stations', label: 'Monitoring stations', short: 'Stations' },
  population_grid: { id: 'population_grid', label: 'Population grid (2019)', short: 'Population', valueField: 'pop_2019', unitLabel: 'persons per cell',
    ramp: ['#eef6f3', '#c4e3d8', '#8ccbb7', '#4fa88f', '#237a66', '#0d4d44'] },
  roads: { id: 'roads', label: 'Roads', short: 'Roads' },
  camx_pm25: { id: 'camx_pm25', label: 'CAMx modelled PM2.5', short: 'CAMx', valueField: 'camx_pm25_ugm3', unitLabel: 'µg/m³ (modelled)',
    ramp: ['#eef3fb', '#c7d9f0', '#93b6e0', '#5b8ccb', '#2f5fa8', '#16306b'] },
  emissions_context: { id: 'emissions_context', label: 'PM2.5 emissions (2018 inventory)', short: 'Emissions', valueField: 'emis_pm25_total_t_per_km2_yr', unitLabel: 't/km²/yr (inventory)',
    ramp: ['#f5f1ea', '#e6d9bf', '#d3b98a', '#b98f5a', '#8f6234', '#5d3b1e'] },
  context_features: { id: 'context_features', label: 'Mapped emission-source context', short: 'Context' },
  administrative_boundary: { id: 'administrative_boundary', label: 'Administrative boundary (country level)', short: 'Boundary' },
}

export const ROAD_COLORS: Record<string, string> = { primary: '#16306b', secondary: '#2f5fa8', tertiary: '#7aa0d4' }

export const CAMX_MONTHS = ['annual', 'jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'] as const
export type CamxMonth = (typeof CAMX_MONTHS)[number]

export const KIND_LABEL: Record<string, string> = { measured: 'Measured', modelled: 'Modelled', estimated: 'Estimated', static: 'Static geographic', mixed: 'Mixed' }

/** Reduce the API's free-text data_kind to one badge label. */
export function kindBadge(dataKind: string): { label: string; tone: 'measured' | 'modelled' | 'estimated' | 'static' | 'mixed' } {
  const k = dataKind.toLowerCase()
  if (k.startsWith('measured')) return { label: 'Measured', tone: 'measured' }
  if (k.startsWith('modelled')) return { label: 'Modelled', tone: 'modelled' }
  if (k.startsWith('estimated')) return { label: 'Estimated', tone: 'estimated' }
  if (k.startsWith('static')) return { label: 'Static', tone: 'static' }
  return { label: 'Mixed', tone: 'mixed' }
}

export interface LegendBin {
  color: string
  from: number
  to: number
}

/** Breakpoints from the API's quantile statistics (5th..95th percentile) so a few extreme cells do not wash out the map. */
export function breaksFromStats(stats: FieldStats | undefined): number[] | null {
  if (!stats || stats.min === undefined || stats.max === undefined) return null
  const raw = [stats.p05, stats.p25, stats.median, stats.p75, stats.p95].filter((v): v is number => typeof v === 'number')
  if (raw.length < 5) return null
  const out: number[] = []
  for (const v of raw) if (out.length === 0 || v > out[out.length - 1]) out.push(v)
  return out.length >= 2 ? out : null
}

export function legendBins(stats: FieldStats | undefined, ramp: string[]): LegendBin[] {
  const br = breaksFromStats(stats)
  if (!br || stats?.min === undefined || stats?.max === undefined) return []
  const edges = [stats.min, ...br, stats.max]
  const bins: LegendBin[] = []
  for (let i = 0; i < edges.length - 1; i++) bins.push({ color: ramp[Math.min(i, ramp.length - 1)], from: edges[i], to: edges[i + 1] })
  return bins
}

/** MapLibre `step` expression: values below the first break get ramp[0]. Missing values are drawn transparent, never as the lowest class. */
export function fillColorExpression(field: string, stats: FieldStats | undefined, ramp: string[]): unknown {
  const br = breaksFromStats(stats)
  if (!br) return 'rgba(0,0,0,0)'
  const step: unknown[] = ['step', ['to-number', ['get', field], -1e18], ramp[0]]
  br.forEach((b, i) => {
    step.push(b, ramp[Math.min(i + 1, ramp.length - 1)])
  })
  return ['case', ['==', ['get', field], null], 'rgba(0,0,0,0)', step]
}

export function formatNumber(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return 'no data'
  const abs = Math.abs(v)
  if (abs >= 1000) return v.toLocaleString('en-US', { maximumFractionDigits: 0 })
  return v.toLocaleString('en-US', { maximumFractionDigits: abs >= 10 ? 1 : digits })
}

export function layerById(layers: LayerInfo[], id: string): LayerInfo | undefined {
  return layers.find((l) => l.id === id)
}
