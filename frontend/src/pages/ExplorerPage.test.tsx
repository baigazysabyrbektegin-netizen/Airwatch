import { cloneElement, type ReactElement } from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// jsdom has no layout: give Recharts a fixed size so it renders real SVG.
vi.mock('recharts', async (orig) => {
  const actual = await orig<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactElement<{ width?: number; height?: number }> }) => (
      <div style={{ width: 600, height: 300 }}>{cloneElement(children, { width: 600, height: 300 })}</div>
    ),
  }
})
const csv = vi.hoisted(() => ({ downloads: [] as { name: string; text: string }[] }))
vi.mock('../lib/csv', async (orig) => ({ ...(await orig<typeof import('../lib/csv')>()), downloadCsv: (name: string, text: string) => csv.downloads.push({ name, text }) }))

import ExplorerPage from './ExplorerPage'

const station = (over: Record<string, unknown>) => ({
  id: 'C1', name: 'Clarity A1', network: 'clarity', sensor_type: 'low-cost', grade: 'low-cost', data_kind: 'measured (low-cost sensor)', latitude: 42.8, longitude: 74.6,
  location_status: 'confirmed', data_available: true, status: 'has_data_in_file', vintage: '2021', data_period: { start: '2021-02-28T01:00:00+06:00', end: '2021-12-31T23:00:00+06:00' },
  n_valid_readings: 10, coverage_pct: 90, quality: null, shares_coordinates_with: [], notes: [], timestamp_convention: 'hour-ending (assumed)', ...over,
})
const STATIONS = {
  count: 3, notes: [],
  stations: [
    station({ id: 'us-embassy-bishkek', name: 'US Embassy Bishkek monitor', network: 'us-embassy', data_kind: 'measured (reference-grade)', location_status: 'APPROXIMATE', data_period: { start: '2019-02-06T06:00:00+06:00', end: '2024-06-30T23:00:00+06:00' } }),
    station({}),
    station({ id: 'C3', name: 'Clarity A3', data_available: false }),
  ],
}
const QUERY = { start_date: '2019-02-06', end_date: '2024-06-30', effective_start: '2019-02-06', effective_end: '2024-06-30', station_data_period: { first: '2019-02-06', last: '2024-06-30' } }
const rel = (n: number, enough: boolean) => ({ n_pairs: n, enough_pairs: enough, min_pairs_required: 30, pearson_r: enough ? -0.54 : null, spearman_rho: enough ? -0.63 : null, reason: enough ? null : `Only ${n} valid PM2.5/weather pairs; at least 30 are needed before a correlation is shown.` })
const EXPLORER = {
  station: { id: 'us-embassy-bishkek', name: 'US Embassy Bishkek monitor', network: 'us-embassy', data_kind: 'measured (reference-grade)', location_status: 'APPROXIMATE', timestamp_convention: 'Date (LT) as given' },
  source: 'embassy', interval: 'daily', unit: 'µg/m³', query: QUERY,
  alignment: { weather_included: true, pairing_basis: 'days', weather_offset_hours: 0, offset_applies: false, statement: 'No offset was chosen to improve correlation.', weather_timestamps: 'UTC' },
  empty: false, empty_reason: null, count: 4, n_valid: 3,
  series: [
    { timestamp: '2022-01-01', pm25: 40, pm25_raw: 40, quality_flag: 'COMPLETE_DAY', flags: [], weather: { temperature_2m: -3, wind_speed_10m: 1, relative_humidity_2m: 80, precipitation: 0 } },
    { timestamp: '2022-01-02', pm25: null, pm25_raw: null, quality_flag: 'NO_READINGS', flags: [], weather: { temperature_2m: -2, wind_speed_10m: 2, relative_humidity_2m: 70, precipitation: 1 } },
    { timestamp: '2022-01-03', pm25: 30, pm25_raw: 30, quality_flag: 'COMPLETE_DAY', flags: [], weather: { temperature_2m: 0, wind_speed_10m: 3, relative_humidity_2m: 60, precipitation: 0 } },
    { timestamp: '2022-01-04', pm25: 20, pm25_raw: 20, quality_flag: 'COMPLETE_DAY', flags: [], weather: { temperature_2m: 2, wind_speed_10m: 2, relative_humidity_2m: 50, precipitation: 2 } },
  ],
  weather: {
    included: true,
    variables: { temperature_2m: { label: 'T', unit: '°C' }, wind_speed_10m: { label: 'W', unit: 'm/s' }, relative_humidity_2m: { label: 'RH', unit: '%' }, precipitation: { label: 'P', unit: 'mm' } },
    provenance: { provider: 'Open-Meteo', model: 'era5', data_kind: 'reanalysis', is_forecast: false, is_local_measurement: false, spatial_resolution: 'about 0.25 degrees (~25 km)',
                  grid: { requested_latitude: 42.917, requested_longitude: 74.51, returned_latitude: 43, returned_longitude: 74.5, elevation_m: 702 }, retrieved_at_utc: '2026-11-11T05:46:34+00:00',
                  period: { first_local: null, last_local: null }, attribution: ['Weather data by Open-Meteo.com, CC BY 4.0.'], licence: 'CC BY 4.0 (Open-Meteo). The free API is for non-commercial use only.', limitations: [] },
  },
  relationships: { variables: { temperature_2m: rel(3, false), wind_speed_10m: rel(40, true), relative_humidity_2m: rel(40, true), precipitation: rel(40, true) },
                   note: 'Correlations describe statistical association. They do not show cause and effect.' },
  warnings: [{ code: 'embassy_regime_2023_2024', severity: 'warning', message: 'Potential measurement-regime issue in 2023-2024.' }, { code: 'historical_not_current', severity: 'info', message: 'All values are historical.' }],
  notes: [],
}
const SUMMARY = {
  station: { id: 'us-embassy-bishkek', name: 'US Embassy Bishkek monitor', network: 'us-embassy' }, source: 'embassy', unit: 'µg/m³', query: QUERY, empty: false, empty_reason: null,
  stats: { mean: 30.3, median: 17, min: 0, max: 917, std: 40, p05: 3, p25: 8, p75: 38, p95: 100, p99: 200 }, stats_basis: 'valid hourly values (model-ready: negatives clipped to 0)',
  n_valid: 45613, expected_hours: 47322, coverage_pct: 96.39, coverage_definition: 'valid hours / expected hours',
  observations: { expected_hours: 47322, valid: 45613, excluded: { hours_without_source_row: 1000, provider_invalid: 600, missing_value: 100, duplicate_conflict: 9, total: 1709 },
                  suspect: { excluded_suspect_hours: 6, flagged_but_included: { negative_noise: 1102, high_value_over_500: 30 } },
                  imputed: { hours_with_feature_interpolation: 241, used_in_statistics: false, note: 'n' }, negative_clipped_to_zero: 1102 },
  daily: { n_valid_days: 1800, n_days: 1976, mean_of_daily_means: 30 },
  monthly: [{ year_month: '2022-01', mean: 80, n_valid_days: 30, eligible_days: 31, meets_75pct_rule: true }, { year_month: '2022-02', mean: null, n_valid_days: 5, eligible_days: 28, meets_75pct_rule: false }],
  climatology: Array.from({ length: 12 }, (_, i) => ({ month: i + 1, n_months: i === 0 ? 2 : 0, mean: i === 0 ? 80 : null, min: null, max: null })),
  seasonal: [{ season: 'DJF', label: 'Winter (Dec–Feb)', eligible_days: 90, n_valid_days: 80, mean_of_daily_means: 70, median_of_daily_means: 60, p95_of_daily_means: 200 },
             { season: 'MAM', label: 'Spring (Mar–May)', eligible_days: 0, n_valid_days: 0, mean_of_daily_means: null, median_of_daily_means: null, p95_of_daily_means: null }],
  seasonal_by_year: [{ season_year: 2021, season: 'DJF', label: 'Winter (Dec–Feb) 2021/2022', eligible_days: 90, n_valid_days: 80, meets_75pct_rule: true, mean_of_daily_means: 70 }],
  warnings: [], notes: [],
}
const period = (label: string, name: string) => ({ label, station: { id: 'x', name, network: 'us-embassy' }, source: 'embassy', query: { ...QUERY, effective_start: label === 'A' ? '2022-01-01' : '2023-01-01', effective_end: label === 'A' ? '2022-03-31' : '2023-03-31' },
  empty: false, empty_reason: null, summary: { ...SUMMARY, coverage_pct: 99.6, daily: { n_valid_days: 90, n_days: 90, mean_of_daily_means: 35 } }, series: [{ index: 0, timestamp: 't', pm25: 35 }, { index: 1, timestamp: 't2', pm25: null }],
  weather: { temperature_2m: { mean_of_daily_values: 2.7, n_days: 90, unit: null } } })
const diff = (a: number, b: number) => ({ a, b, difference_b_minus_a: b - a, percent_change: ((b - a) / a) * 100 })
const COMPARE = {
  interval: 'daily', unit: 'µg/m³', period_a: period('A', 'US Embassy A'), period_b: period('B', 'US Embassy B'),
  difference: { available: true, reason: null, hourly_mean: diff(35.5, 71.4), median: diff(25, 36), mean_of_daily_means: diff(35.5, 71.6), p95: diff(105, 297), max: diff(504, 855), weather: { temperature_2m: diff(2.7, 1.9) } },
  warnings: [{ code: 'different_seasons', severity: 'warning', message: 'The periods cover different calendar months.' }], alignment: { statement: 'Series are aligned by position.', x_axis: 'index = days since the start of each period' }, notes: ['A difference is descriptive.'],
}

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status }))
let calls: string[] = []
let overrides: Record<string, () => Promise<Response>> = {}

beforeEach(() => {
  calls = []
  overrides = {}
  csv.downloads.length = 0
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    calls.push(url)
    const key = Object.keys(overrides).find((k) => url.startsWith(k))
    if (key) return overrides[key]()
    if (url.startsWith('/api/stations')) return json(STATIONS)
    if (url.startsWith('/api/explorer/summary')) return json(SUMMARY)
    if (url.startsWith('/api/explorer/compare')) return json(COMPARE)
    if (url.startsWith('/api/explorer')) return json(EXPLORER)
    return json({}, 404)
  }))
})
afterEach(() => vi.unstubAllGlobals())

async function ready() {
  render(<ExplorerPage />)
  await screen.findByRole('region', { name: 'Summary' })
}

describe('Data & Weather Explorer page', () => {
  it('shows loading, then loads the Embassy record with default filters', async () => {
    render(<ExplorerPage />)
    expect(screen.getByText('Loading stations…')).toBeInTheDocument()
    await screen.findByRole('region', { name: 'Summary' })
    expect(calls).toContain('/api/explorer?source=embassy&start_date=2019-02-06&end_date=2024-06-30&interval=daily')
    expect(calls).toContain('/api/explorer/summary?source=embassy&start_date=2019-02-06&end_date=2024-06-30')
    expect(screen.getByLabelText('Aggregation')).toHaveValue('daily')
  })

  it('shows the five summary cards with real values and the observation breakdown', async () => {
    await ready()
    const s = screen.getByRole('region', { name: 'Summary' })
    expect(within(s).getByText('Average PM2.5').nextSibling).toHaveTextContent('30.3')
    expect(within(s).getByText('Median PM2.5').nextSibling).toHaveTextContent('17')
    expect(within(s).getByText('Maximum PM2.5').nextSibling).toHaveTextContent('917')
    expect(within(s).getByText('Valid observations').nextSibling).toHaveTextContent('45,613')
    expect(within(s).getByText('Data coverage').nextSibling).toHaveTextContent('96.4%')
    expect(within(s).getByText(/Excluded:/).parentElement).toHaveTextContent('1,709 hours')
    expect(within(s).getByText(/Imputed:/).parentElement).toHaveTextContent('used in these statistics: no')
  })

  it('renders the four chart groups with source and measurement-type labels', async () => {
    await ready()
    for (const id of ['chart-pm25', 'chart-weather-temperature_2m', 'chart-weather-wind_speed_10m', 'chart-weather-relative_humidity_2m', 'chart-weather-precipitation', 'chart-monthly-trend', 'chart-climatology', 'chart-seasons', 'chart-scatter']) {
      expect(screen.getByTestId(id)).toBeInTheDocument()
    }
    expect(screen.getByTestId('chart-pm25').querySelector('svg')).not.toBeNull()
    expect(screen.getByText(/US Embassy reference monitor · US Embassy Bishkek monitor · measured PM2\.5, daily/)).toBeInTheDocument()
    expect(screen.getByText(/ERA5 reanalysis via Open-Meteo, daily means/)).toBeInTheDocument()
    expect(screen.getByText(/not a forecast and not a local station measurement/)).toBeInTheDocument()
  })

  it('preserves gaps: a missing day is not drawn as zero', async () => {
    await ready()
    const path = screen.getByTestId('chart-pm25').querySelector('.recharts-line-curve')
    expect(path).not.toBeNull()
    expect(path!.getAttribute('d')!.match(/M/g)!.length).toBeGreaterThan(1) // the line is broken at the null
  })

  it('shows data-quality warnings and the always-visible provenance', async () => {
    await ready()
    expect(screen.getByText(/Potential measurement-regime issue in 2023-2024/)).toBeInTheDocument()
    expect(screen.getByText('Data notes (1)')).toBeInTheDocument()
    const src = screen.getByRole('contentinfo', { name: 'Data sources' })
    expect(src).toHaveTextContent('Historical record 2019-02-06 to 2024-06-30; not current conditions')
    expect(src).toHaveTextContent('Grid cell 43, 74.5 (requested 42.917, 74.51)')
    expect(within(src).getByRole('link', { name: 'Weather data by Open-Meteo.com' })).toHaveAttribute('href', 'https://open-meteo.com/')
    expect(src).toHaveTextContent('non-commercial')
  })

  it('shows the scatter sample size and correlation, labelled as association', async () => {
    const user = userEvent.setup()
    await ready()
    const stats = screen.getByTestId('scatter-stats')
    expect(stats).toHaveTextContent('n = 3 valid pairs') // temperature: not enough pairs
    expect(stats).toHaveTextContent('at least 30 are needed')
    expect(stats).not.toHaveTextContent('Pearson')
    await user.selectOptions(screen.getByLabelText('Weather variable'), 'wind_speed_10m')
    expect(screen.getByTestId('scatter-stats')).toHaveTextContent('Pearson r = -0.54; Spearman ρ = -0.63')
    expect(screen.getByText(/do not show cause and effect/)).toBeInTheDocument()
  })

  it('switching to Clarity offers stations with data only and resets the dates to that station', async () => {
    const user = userEvent.setup()
    await ready()
    await user.selectOptions(screen.getByLabelText('Data source'), 'clarity')
    const st = screen.getByLabelText('Station')
    expect(within(st).getAllByRole('option').map((o) => o.textContent)).toEqual(['Clarity A1'])
    expect(screen.getByLabelText('Start date')).toHaveValue('2021-02-28')
    expect(screen.getByLabelText('End date')).toHaveValue('2021-12-31')
    await user.click(screen.getByRole('button', { name: 'Apply filters' }))
    await waitFor(() => expect(calls).toContain('/api/explorer?source=clarity&station_id=C1&start_date=2021-02-28&end_date=2021-12-31&interval=daily'))
  })

  it('applies a new date range and aggregation only when Apply is pressed', async () => {
    const user = userEvent.setup()
    await ready()
    const before = calls.length
    fireEvent.change(screen.getByLabelText('Start date'), { target: { value: '2022-01-01' } })
    fireEvent.change(screen.getByLabelText('End date'), { target: { value: '2022-01-31' } })
    await user.selectOptions(screen.getByLabelText('Aggregation'), 'hourly')
    expect(calls.length).toBe(before)
    await user.click(screen.getByRole('button', { name: 'Apply filters' }))
    await waitFor(() => expect(calls).toContain('/api/explorer?source=embassy&start_date=2022-01-01&end_date=2022-01-31&interval=hourly'))
  })

  it('blocks hourly requests longer than 120 days and explains why', async () => {
    const user = userEvent.setup()
    await ready()
    await user.selectOptions(screen.getByLabelText('Aggregation'), 'hourly')
    expect(screen.getByRole('button', { name: 'Apply filters' })).toBeDisabled()
    expect(screen.getByRole('note')).toHaveTextContent('limited to 120 days')
    fireEvent.change(screen.getByLabelText('Start date'), { target: { value: '2022-01-01' } })
    fireEvent.change(screen.getByLabelText('End date'), { target: { value: '2022-02-01' } })
    expect(screen.getByRole('button', { name: 'Apply filters' })).toBeEnabled()
  })

  it('rejects a start date after the end date', async () => {
    await ready()
    fireEvent.change(screen.getByLabelText('Start date'), { target: { value: '2024-01-01' } })
    fireEvent.change(screen.getByLabelText('End date'), { target: { value: '2023-01-01' } })
    expect(screen.getByRole('note')).toHaveTextContent('must not be later')
    expect(screen.getByRole('button', { name: 'Apply filters' })).toBeDisabled()
  })

  it('shows a useful error from the API and keeps the page usable', async () => {
    overrides['/api/explorer?'] = () => json({ error: 'invalid_query', message: 'Hourly data is limited to 120 days per request.' }, 422)
    render(<ExplorerPage />)
    expect(await screen.findByText('Hourly data is limited to 120 days per request.')).toBeInTheDocument()
    expect(screen.getByText('Could not load the observations')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Apply filters' })).toBeInTheDocument()
  })

  it('shows an actionable error with retry when stations cannot be loaded', async () => {
    overrides['/api/stations'] = () => json({ error: 'data_unavailable', message: 'Clarity inventory missing', hint: 'Run the pipeline' }, 503)
    const user = userEvent.setup()
    render(<ExplorerPage />)
    expect(await screen.findByText('Clarity inventory missing')).toBeInTheDocument()
    delete overrides['/api/stations']
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await screen.findByRole('region', { name: 'Summary' })
  })

  it('shows a clear empty state instead of empty charts', async () => {
    overrides['/api/explorer?'] = () => json({ ...EXPLORER, empty: true, empty_reason: 'The selected dates do not overlap this station\'s data period (2019-02-06 to 2024-06-30).', series: [], count: 0 })
    render(<ExplorerPage />)
    expect(await screen.findByText('No observations to show')).toBeInTheDocument()
    expect(screen.getByText(/do not overlap/)).toBeInTheDocument()
    expect(screen.queryByTestId('chart-pm25')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Export CSV' })).toBeDisabled()
  })

  it('says so when ERA5 weather has not been created yet', async () => {
    overrides['/api/explorer?'] = () => json({ ...EXPLORER, weather: { included: false, unavailable: true }, relationships: { variables: {} } })
    render(<ExplorerPage />)
    expect(await screen.findByText(/ERA5 weather is not available: the weather file has not been created yet/)).toBeInTheDocument()
    expect(screen.queryByTestId('chart-scatter')).not.toBeInTheDocument()
    expect(screen.getByTestId('chart-pm25')).toBeInTheDocument()
  })

  it('exports the filtered observations as CSV with provenance', async () => {
    const user = userEvent.setup()
    await ready()
    await user.click(screen.getByRole('button', { name: 'Export CSV' }))
    expect(csv.downloads).toHaveLength(1)
    const { name, text } = csv.downloads[0]
    expect(name).toBe('airwatch_us-embassy-bishkek_daily_2019-02-06_2024-06-30.csv')
    expect(text).toContain('# AirWatch export - historical data, not current conditions')
    expect(text).toContain('timestamp,pm25_valid_ugm3,pm25_original_ugm3,quality_flag,flags,temperature_2m_°C')
    expect(text).toContain('2022-01-02,,,NO_READINGS,,-2,')                 // the missing day is empty, not 0
    expect(text).toContain('WARNING: Potential measurement-regime issue')
  })

  it('compares two periods and shows differences, warnings and the overlay chart', async () => {
    const user = userEvent.setup()
    await ready()
    await user.click(screen.getByLabelText(/Compare with a second period/))
    fireEvent.change(screen.getByLabelText('Period B start'), { target: { value: '2023-01-01' } })
    fireEvent.change(screen.getByLabelText('Period B end'), { target: { value: '2023-03-31' } })
    await user.click(screen.getByRole('button', { name: 'Apply filters' }))
    const panel = await screen.findByRole('region', { name: 'Period comparison' })
    await waitFor(() => expect(calls.some((c) => c.startsWith('/api/explorer/compare?station_a=us-embassy-bishkek') && c.includes('start_b=2023-01-01') && c.includes('end_b=2023-03-31') && c.includes('interval=daily'))).toBe(true))
    expect(within(panel).getByText(/The periods cover different calendar months/)).toBeInTheDocument()
    expect(within(panel).getByRole('table', { name: 'Comparison statistics' })).toHaveTextContent('+35.9 (+101%)')
    expect(within(panel).getByText(/Series are aligned by position/)).toBeInTheDocument()
    expect(within(panel).getByTestId('chart-compare')).toBeInTheDocument()
  })

  it('explains when a comparison cannot show a difference', async () => {
    overrides['/api/explorer/compare'] = () => json({ ...COMPARE, difference: { available: false, reason: 'A difference is shown only when both periods have at least 10 valid daily means.' } })
    const user = userEvent.setup()
    await ready()
    await user.click(screen.getByLabelText(/Compare with a second period/))
    fireEvent.change(screen.getByLabelText('Period B start'), { target: { value: '2023-01-01' } })
    fireEvent.change(screen.getByLabelText('Period B end'), { target: { value: '2023-01-02' } })
    await user.click(screen.getByRole('button', { name: 'Apply filters' }))
    expect(await screen.findByText(/No difference is shown/)).toBeInTheDocument()
    expect(screen.getByText(/at least 10 valid daily means/)).toBeInTheDocument()
  })

  it('shows a comparison error without hiding the main results', async () => {
    overrides['/api/explorer/compare'] = () => json({ error: 'invalid_query', message: 'Comparison supports interval=daily or monthly.' }, 422)
    const user = userEvent.setup()
    await ready()
    await user.click(screen.getByLabelText(/Compare with a second period/))
    fireEvent.change(screen.getByLabelText('Period B start'), { target: { value: '2023-01-01' } })
    fireEvent.change(screen.getByLabelText('Period B end'), { target: { value: '2023-01-02' } })
    await user.click(screen.getByRole('button', { name: 'Apply filters' }))
    expect(await screen.findByText('Comparison supports interval=daily or monthly.')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Summary' })).toBeInTheDocument()
  })
})
