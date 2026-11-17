export type PageId = 'map' | 'explorer' | 'planner' | 'workspace'

export const NAV: { id: PageId; label: string; ready: boolean }[] = [
  { id: 'map', label: 'Air Quality Map', ready: true },
  { id: 'explorer', label: 'Data & Weather Explorer', ready: true },
  { id: 'planner', label: 'Sensor Planner', ready: true },
  { id: 'workspace', label: 'Research Workspace', ready: true },
]

