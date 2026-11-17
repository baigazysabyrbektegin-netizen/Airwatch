import { describe, expect, it } from 'vitest'
import { buildCsv, csvFilename } from './csv'
import type { ExplorerResponse } from './types'

function response(over: Partial<ExplorerResponse> = {}): ExplorerResponse {
  return {
    station: { id: 'us-embassy-bishkek', name: 'US Embassy, Bishkek', network: 'us-embassy', data_kind: 'measured (reference-grade)', location_status: 'APPROXIMATE', timestamp_convention: 'Date (LT) as given' },
    source: 'embassy', interval: 'hourly', unit: 'µg/m³',
    query: { start_date: '2023-03-31', end_date: '2023-03-31', effective_start: '2023-03-31', effective_end: '2023-03-31', station_data_period: { first: '2019-02-06', last: '2024-06-30' } },
    alignment: { weather_included: true, pairing_basis: 'x', weather_offset_hours: 0, offset_applies: true, statement: 'No offset was chosen to improve correlation.', weather_timestamps: 'UTC' },
    empty: false, empty_reason: null, count: 3,
    series: [
      { timestamp: '2023-03-31T00:00:00+06:00', pm25: 20, pm25_raw: 20, quality_flag: 'VALID', flags: [], weather: { temperature_2m: 1.5, wind_speed_10m: null } },
      { timestamp: '2023-03-31T01:00:00+06:00', pm25: null, pm25_raw: null, quality_flag: 'NO_ROW', flags: [], weather: { temperature_2m: 1.2, wind_speed_10m: 0.4 } },
      { timestamp: '2023-03-31T02:00:00+06:00', pm25: 0, pm25_raw: -2, quality_flag: 'VALID', flags: ['NEGATIVE_NOISE', 'X'], weather: { temperature_2m: 1.0, wind_speed_10m: 0.5 } },
    ],
    weather: { included: true, variables: { temperature_2m: { label: 'T', unit: '°C' }, wind_speed_10m: { label: 'W', unit: 'm/s' } }, provenance: { attribution: ['Weather data by Open-Meteo.com, CC BY 4.0.'] } as never },
    relationships: {}, warnings: [{ code: 'w', severity: 'warning', message: 'Regime issue, "quoted"' }, { code: 'i', severity: 'info', message: 'info only' }], notes: [],
    ...over,
  }
}

describe('buildCsv', () => {
  const lines = buildCsv(response()).trimEnd().split('\n')
  const header = lines.findIndex((l) => !l.startsWith('# '))

  it('starts with a provenance header that names the caveats', () => {
    const head = lines.slice(0, header).join('\n')
    expect(head).toContain('historical data, not current conditions')
    expect(head).toContain('ERA5 reanalysis via Open-Meteo - not a forecast and not a local station measurement')
    expect(head).toContain('Weather data by Open-Meteo.com')
    expect(head).toContain('No offset was chosen to improve correlation')
    expect(head).toContain('WARNING: Regime issue')
    expect(head).not.toContain('info only')
  })

  it('writes units in the column names and one row per observation', () => {
    expect(lines[header]).toBe('timestamp,pm25_valid_ugm3,pm25_original_ugm3,quality_flag,flags,temperature_2m_°C,wind_speed_10m_m/s')
    expect(lines).toHaveLength(header + 4)
  })

  it('leaves missing values empty, never zero, and keeps real zeros and originals', () => {
    expect(lines[header + 1]).toBe('2023-03-31T00:00:00+06:00,20,20,VALID,,1.5,')
    expect(lines[header + 2]).toBe('2023-03-31T01:00:00+06:00,,,NO_ROW,,1.2,0.4')
    expect(lines[header + 3]).toBe('2023-03-31T02:00:00+06:00,0,-2,VALID,NEGATIVE_NOISE;X,1,0.5')
  })

  it('omits weather columns when weather is not included and says so', () => {
    const csv = buildCsv(response({ weather: { included: false } }))
    expect(csv).toContain('Weather: not included in this export.')
    expect(csv.split('\n').find((l) => l.startsWith('timestamp,'))).toBe('timestamp,pm25_valid_ugm3,pm25_original_ugm3,quality_flag,flags')
  })

  it('quotes fields containing commas', () => {
    const csv = buildCsv(response({ series: [{ timestamp: 'a,b', pm25: 1, pm25_raw: 1, quality_flag: 'VALID', flags: [], weather: {} }] }))
    expect(csv).toContain('"a,b",1,1,VALID,')
  })

  it('builds a safe file name', () => {
    expect(csvFilename(response())).toBe('airwatch_us-embassy-bishkek_hourly_2023-03-31_2023-03-31.csv')
  })
})
