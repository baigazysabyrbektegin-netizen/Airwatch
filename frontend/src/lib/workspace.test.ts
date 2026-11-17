import { beforeEach, describe, expect, it } from 'vitest'
import { buildCandidateCsv, buildResearchSummary, loadSavedWorkspaces, saveWorkspace, deleteWorkspace, type WorkspaceSettings } from './workspace'

const settings: WorkspaceSettings = {
  title: 'Winter comparison',
  description: 'Test settings',
  stationIds: ['A', 'B'],
  start: '2021-01-01',
  end: '2021-01-07',
  periodAStart: '2021-01-01',
  periodAEnd: '2021-01-03',
  periodBStart: '2021-01-04',
  periodBEnd: '2021-01-07',
}

describe('research workspace persistence and exports', () => {
  beforeEach(() => localStorage.clear())

  it('saves, restores, updates and deletes lightweight settings', () => {
    const saved = saveWorkspace(settings)
    expect(loadSavedWorkspaces()[0].title).toBe('Winter comparison')
    const updated = saveWorkspace({ ...settings, title: 'Updated' }, saved)
    expect(loadSavedWorkspaces()).toHaveLength(1)
    expect(loadSavedWorkspaces()[0].title).toBe('Updated')
    deleteWorkspace(updated.id)
    expect(loadSavedWorkspaces()).toEqual([])
  })

  it('exports provenance-bearing candidate CSV and summary text', () => {
    const csv = buildCandidateCsv([{ candidate_id: 'cell-1', rank: 1, latitude: 42.8, longitude: 74.6, priority_score: 0.4, score_components: {}, population_2019: 10, camx_pm25_annual_mean_ugm3: 20, emissions_pm25_t_per_km2_yr: 1, nearest_station_id: 'C1', nearest_station_distance_km: 4, data_confidence: 'moderate', explanation: 'gap', limitations: [] }], settings.title)
    expect(csv).toContain('# Candidates are decision-support estimates')
    expect(csv).toContain('cell-1')
    const summary = buildResearchSummary(settings, [], ['Finding from API'], ['Known limitation'])
    expect(summary).toContain('Finding from API')
    expect(summary).toContain('ERA5 is reanalysis')
  })
})
