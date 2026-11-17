import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({
  stations: vi.fn(),
  explorer: vi.fn(),
  compare: vi.fn(),
  plannerOverview: vi.fn(),
  plannerCandidates: vi.fn(),
}))

vi.mock('../lib/apiClient', () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
}))

import WorkspacePage from './WorkspacePage'

describe('Research Workspace page', () => {
  beforeEach(() => {
    vi.spyOn(Storage.prototype, 'getItem').mockReturnValue(null)
    apiMock.stations.mockRejectedValue(new Error('Station service unavailable'))
  })

  it('shows the station API failure clearly', async () => {
    render(<WorkspacePage />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Station service unavailable')
    expect(screen.queryByText('[object Object]')).not.toBeInTheDocument()
  })
})
