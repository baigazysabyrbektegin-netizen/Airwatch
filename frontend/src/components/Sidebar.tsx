import { NAV, type PageId } from '../lib/nav'

export function Sidebar({ page, onNavigate }: { page: PageId; onNavigate: (p: PageId) => void }) {
  return (
    <nav aria-label="Main" className="flex shrink-0 flex-row gap-1 overflow-x-auto border-b border-navy-200 bg-navy-950 p-2 text-white md:w-64 md:flex-col md:border-b-0 md:border-r md:p-3">
      <div className="hidden px-2 pb-4 pt-2 md:block">
        <div className="text-lg font-semibold tracking-tight">AirWatch</div>
        <div className="mt-0.5 text-xs leading-snug text-navy-200">Bishkek urban air-quality research platform</div>
      </div>
      {NAV.map((item) => {
        const active = item.id === page
        return (
          <button
            key={item.id}
            type="button"
            onClick={() => onNavigate(item.id)}
            aria-current={active ? 'page' : undefined}
            className={`flex items-center justify-between gap-2 whitespace-nowrap rounded px-3 py-2 text-left text-sm transition-colors ${
              active ? 'bg-white/15 font-medium' : 'text-navy-200 hover:bg-white/10'
            }`}
          >
            <span>{item.label}</span>
            {!item.ready && <span className="rounded bg-white/10 px-1 py-0.5 text-[9px] uppercase tracking-wide">Upcoming</span>}
          </button>
        )
      })}
    </nav>
  )
}
