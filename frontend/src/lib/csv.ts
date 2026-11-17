import type { ExplorerResponse, WeatherVar } from './types'

function cell(v: unknown): string {
  if (v === null || v === undefined) return '' // missing stays empty, never 0
  const s = String(v)
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

export const WEATHER_ORDER: WeatherVar[] = ['temperature_2m', 'relative_humidity_2m', 'wind_speed_10m', 'wind_direction_10m', 'surface_pressure', 'precipitation', 'boundary_layer_height']

/** The filtered observations exactly as shown, with a provenance header so the file is not separated from its caveats. */
export function buildCsv(r: ExplorerResponse): string {
  const wv = r.weather?.included ? WEATHER_ORDER.filter((v) => r.weather?.variables?.[v]) : []
  const head = [
    'AirWatch export - historical data, not current conditions',
    `Station: ${r.station.name} (${r.station.id}); network: ${r.source}; kind: ${r.station.data_kind}`,
    `Interval: ${r.interval}; selected range ${r.query.effective_start} to ${r.query.effective_end} (Asia/Bishkek)`,
    `Timestamps: ${r.station.timestamp_convention}`,
    'pm25_valid_ugm3 = value used in statistics (empty = no valid observation, NOT zero); pm25_original_ugm3 = value before cleaning (Embassy hourly only).',
  ]
  if (r.weather?.included) {
    head.push(`Weather: ERA5 reanalysis via Open-Meteo - not a forecast and not a local station measurement. ${r.alignment.statement}`)
    for (const a of r.weather.provenance?.attribution ?? []) head.push(a)
  } else {
    head.push('Weather: not included in this export.')
  }
  for (const w of r.warnings.filter((x) => x.severity === 'warning')) head.push(`WARNING: ${w.message}`)
  const header = ['timestamp', 'pm25_valid_ugm3', 'pm25_original_ugm3', 'quality_flag', 'flags', ...wv.map((v) => `${v}_${r.weather?.variables?.[v]?.unit ?? ''}`)]
  const rows = r.series.map((p) => [p.timestamp, p.pm25, p.pm25_raw, p.quality_flag, p.flags.join(';'), ...wv.map((v) => p.weather[v] ?? null)])
  return [...head.map((h) => `# ${h.replace(/\r?\n/g, ' ')}`), header.join(','), ...rows.map((row) => row.map(cell).join(','))].join('\n') + '\n'
}

export function csvFilename(r: ExplorerResponse): string {
  return `airwatch_${r.station.id}_${r.interval}_${r.query.effective_start}_${r.query.effective_end}.csv`.replace(/[^A-Za-z0-9._-]/g, '_')
}

export function downloadCsv(filename: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
