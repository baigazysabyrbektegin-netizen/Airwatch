import { lazy, Suspense, useState } from 'react'
import { Sidebar } from './components/Sidebar'
import type { PageId } from './lib/nav'
import { AirQualityMap } from './pages/AirQualityMap'
import PlannerPage from './pages/PlannerPage'
import WorkspacePage from './pages/WorkspacePage'

// Charts (Recharts) are only needed on this page, so it is loaded on demand to keep the first load small.
const ExplorerPage = lazy(() => import('./pages/ExplorerPage'))

export default function App() {
  const [page, setPage] = useState<PageId>('map')
  return (
    <div className="flex h-full flex-col overflow-hidden md:flex-row">
      <Sidebar page={page} onNavigate={setPage} />
      {page === 'map' && <AirQualityMap />}
      {page === 'explorer' && (
        <Suspense fallback={<p role="status" className="p-6 text-sm text-navy-700">Loading explorer…</p>}>
          <ExplorerPage />
        </Suspense>
      )}
      {page === 'planner' && <PlannerPage />}
      {page === 'workspace' && <WorkspacePage />}
    </div>
  )
}
