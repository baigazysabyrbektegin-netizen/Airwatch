import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({
  stations: vi.fn(),
  plannerOverview: vi.fn(),
  plannerCandidates: vi.fn(),
  plannerCoverage: vi.fn(),
  recalculatePlanner: vi.fn(),
}))

vi.mock('../lib/apiClient', () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
}))
vi.mock('../components/MapView', () => ({
  MapView: () => <div data-testid="planner-map" />,
}))

import PlannerPage from './PlannerPage'

const station = { id: 'C1', name: 'Clarity C1', network: 'clarity', data_available: true }
const overview = {
  existing_clarity_stations: 1,
  population_2019_underserved: 100,
  covered_cell_pct: 50,
  coverage_radius_km: 3,
}
const candidates = {
  parameters: { n: 1, coverage_radius_km: 3, min_separation_km: 2, exposure_weight: 0.7, source_weight: 0.3 },
  count: 1,
  candidates: [{
    candidate_id: 'cell-1',
    rank: 1,
    latitude: 42.8,
    longitude: 74.6,
    priority_score: 0.5,
    score_components: { monitoring_deficit: 0.5 },
    population_2019: 100,
    nearest_station_id: 'C1',
    nearest_station_distance_km: 4,
    data_confidence: 'moderate',
    explanation: 'Far from the network.',
  }],
}

describe('Sensor Planner page', () => {
  beforeEach(() => {
    apiMock.stations.mockResolvedValue({ stations: [station] })
    apiMock.plannerOverview.mockResolvedValue(overview)
    apiMock.plannerCandidates.mockResolvedValue(candidates)
    apiMock.plannerCoverage.mockResolvedValue({ type: 'FeatureCollection', features: [] })
    apiMock.recalculatePlanner.mockReset()
  })

  it('shows a recalculation error without rendering an object string', async () => {
    apiMock.recalculatePlanner.mockRejectedValue(new Error('Planner service unavailable'))
    render(<PlannerPage />)

    await screen.findByText('Candidate ranking')
    fireEvent.click(screen.getByRole('button', { name: 'Recalculate rankings' }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Planner service unavailable'))
    expect(screen.queryByText('[object Object]')).not.toBeInTheDocument()
  })
})
