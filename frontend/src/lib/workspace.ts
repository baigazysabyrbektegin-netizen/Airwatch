import type { CompareResponse, PlannerCandidate, Station } from './types'

export interface WorkspaceSettings {
  title: string
  description: string
  stationIds: string[]
  start: string
  end: string
  periodAStart: string
  periodAEnd: string
  periodBStart: string
  periodBEnd: string
}

export interface SavedWorkspace extends WorkspaceSettings {
  id: string
  createdAt: string
  updatedAt: string
}

const KEY = 'airwatch-research-workspace-v1'

export function loadSavedWorkspaces(): SavedWorkspace[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    return Array.isArray(parsed) ? parsed.filter((x): x is SavedWorkspace => Boolean(x && typeof x === 'object' && 'id' in x && 'title' in x)) : []
  } catch {
    return []
  }
}

export function saveWorkspace(settings: WorkspaceSettings, existing?: SavedWorkspace): SavedWorkspace {
  const now = new Date().toISOString()
  const saved = { ...settings, id: existing?.id ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`, createdAt: existing?.createdAt ?? now, updatedAt: now }
  const all = loadSavedWorkspaces().filter((x) => x.id !== saved.id)
  localStorage.setItem(KEY, JSON.stringify([saved, ...all]))
  return saved
}

export function deleteWorkspace(id: string): void {
  localStorage.setItem(KEY, JSON.stringify(loadSavedWorkspaces().filter((x) => x.id !== id)))
}

function cell(value: unknown): string {
  if (value === null || value === undefined) return ''
  const text = String(value)
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

export function buildStationComparisonCsv(rows: { station: Station; timestamp: string; pm25: number | null; quality: string }[], settings: WorkspaceSettings): string {
  const header = ['timestamp', 'station_id', 'station_name', 'network', 'data_kind', 'pm25_ugm3', 'quality_flag']
  const notes = [
    `# AirWatch Research Workspace export`,
    `# Title: ${settings.title}`,
    `# Period: ${settings.start} to ${settings.end} (station timestamps retained; sources are not merged)`,
    '# Measured historical PM2.5 only; missing values are empty, not zero.',
  ]
  return [...notes, header.join(','), ...rows.map((r) => [r.timestamp, r.station.id, r.station.name, r.station.network, r.station.data_kind, r.pm25, r.quality].map(cell).join(','))].join('\n') + '\n'
}

export function buildCandidateCsv(candidates: PlannerCandidate[], title: string): string {
  const header = ['rank', 'candidate_id', 'latitude', 'longitude', 'priority_score', 'population_2019', 'nearest_station_id', 'nearest_station_distance_km', 'explanation']
  return [`# AirWatch Sensor Planner export`, `# Title: ${title}`, '# Candidates are decision-support estimates; grid centroids are approximate.', header.join(','), ...candidates.map((c) => [c.rank, c.candidate_id, c.latitude, c.longitude, c.priority_score, c.population_2019, c.nearest_station_id, c.nearest_station_distance_km, c.explanation].map(cell).join(','))].join('\n') + '\n'
}

export function buildPeriodComparisonCsv(data: CompareResponse, title: string): string {
  const rows = [
    ['metric', 'period_a', 'period_b', 'difference_b_minus_a'],
    ['mean_hourly_pm25_ugm3', data.difference.hourly_mean?.a, data.difference.hourly_mean?.b, data.difference.hourly_mean?.difference_b_minus_a],
    ['median_pm25_ugm3', data.difference.median?.a, data.difference.median?.b, data.difference.median?.difference_b_minus_a],
    ['mean_daily_means_pm25_ugm3', data.difference.mean_of_daily_means?.a, data.difference.mean_of_daily_means?.b, data.difference.mean_of_daily_means?.difference_b_minus_a],
    ['p95_pm25_ugm3', data.difference.p95?.a, data.difference.p95?.b, data.difference.p95?.difference_b_minus_a],
    ['max_pm25_ugm3', data.difference.max?.a, data.difference.max?.b, data.difference.max?.difference_b_minus_a],
  ]
  return [`# AirWatch Period Comparison`, `# Title: ${title}`, '# Historical measured PM2.5; sources and periods remain separate.', ...rows.map((row) => row.map(cell).join(','))].join('\n') + '\n'
}

export function buildResearchSummary(settings: WorkspaceSettings, stations: Station[], findings: string[], limitations: string[]): string {
  return [
    `AirWatch Research Summary`,
    `Title: ${settings.title}`,
    `Description: ${settings.description || 'None provided'}`,
    `Created: ${new Date().toISOString()}`,
    '',
    'Selected stations:',
    ...stations.map((s) => `- ${s.name} (${s.network}; ${s.data_kind})`),
    '',
    'Findings:',
    ...(findings.length ? findings.map((x) => `- ${x}`) : ['- No findings were loaded.']),
    '',
    'Methodology and limitations:',
    ...limitations.map((x) => `- ${x}`),
    '',
    'Provenance: historical measured PM2.5 remains separate by station; ERA5 is reanalysis; CAMx and emissions are modelled; population is a 2019 estimate; planner locations are approximate candidates.',
  ].join('\n') + '\n'
}
