import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Sidebar } from './Sidebar'

describe('Sidebar', () => {
  it('has a working Data & Weather Explorer entry and marks only the unbuilt pages as upcoming', async () => {
    const go = vi.fn()
    render(<Sidebar page="map" onNavigate={go} />)
    const explorer = screen.getByRole('button', { name: /Data & Weather Explorer/ })
    expect(explorer).not.toHaveTextContent(/upcoming/i)
    expect(screen.getByRole('button', { name: /Research Workspace/ })).not.toHaveTextContent(/upcoming/i)
    expect(screen.getByRole('button', { name: /Sensor Planner/ })).not.toHaveTextContent(/upcoming/i)
    expect(screen.getByRole('button', { name: /Research Workspace/ })).not.toHaveTextContent(/upcoming/i)
    await userEvent.click(explorer)
    expect(go).toHaveBeenCalledWith('explorer')
  })
})
