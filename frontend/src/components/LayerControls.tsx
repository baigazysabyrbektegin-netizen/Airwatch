import { CAMX_MONTHS, GRID_LAYERS, LAYER_STYLES, type CamxMonth, type ToggleId } from '../lib/layers'
import type { LayerInfo } from '../lib/types'
import { HistoricalBadge, KindBadge } from './Badge'

interface Props {
  layers: LayerInfo[]
  visible: Record<ToggleId, boolean>
  loading: Partial<Record<ToggleId, boolean>>
  errors: Partial<Record<ToggleId, string>>
  camxMonth: CamxMonth
  basemapOn: boolean
  basemapAvailable: boolean
  onToggle: (id: ToggleId) => void
  onCamxMonth: (m: CamxMonth) => void
  onBasemap: (on: boolean) => void
}

const ORDER: ToggleId[] = ['stations', 'population_grid', 'camx_pm25', 'emissions_context', 'roads', 'context_features', 'administrative_boundary']

export function LayerControls({ layers, visible, loading, errors, camxMonth, basemapOn, basemapAvailable, onToggle, onCamxMonth, onBasemap }: Props) {
  return (
    <section aria-label="Map layers" className="space-y-1">
      <h2 className="px-1 text-xs font-semibold uppercase tracking-wide text-navy-500">Layers</h2>
      <p className="px-1 pb-1 text-xs text-navy-500">Choropleth layers (population, CAMx, emissions) are shown one at a time so colours are not confused.</p>
      {ORDER.map((id) => {
        const info = layers.find((l) => l.id === id)
        const style = LAYER_STYLES[id]
        const isGrid = (GRID_LAYERS as string[]).includes(id)
        const unavailable = info ? !info.available : false
        const inputId = `layer-${id}`
        return (
          <div key={id} className="rounded border border-navy-100 bg-white p-2">
            <div className="flex items-start gap-2">
              <input
                id={inputId}
                type={isGrid ? 'radio' : 'checkbox'}
                name={isGrid ? 'grid-layer' : undefined}
                checked={visible[id]}
                disabled={unavailable}
                onChange={() => onToggle(id)}
                onClick={() => {
                  // Radios cannot be unchecked by clicking; allow clicking the active one to turn it off.
                  if (isGrid && visible[id]) onToggle(id)
                }}
                className="mt-1 accent-leaf-600"
              />
              <div className="min-w-0 flex-1">
                <label htmlFor={inputId} className="block cursor-pointer text-sm font-medium leading-tight">
                  {style.label}
                </label>
                <div className="mt-1 flex flex-wrap items-center gap-1">
                  {info && <KindBadge dataKind={info.data_kind} />}
                  {info && info.time_status === 'historical' && <HistoricalBadge />}
                  {info && <span className="text-[11px] text-navy-500">{info.vintage}</span>}
                </div>
                {info && <p className="mt-1 text-xs leading-snug text-navy-700">{info.description}</p>}
                {unavailable && <p className="mt-1 text-xs text-red-700">Unavailable: {info?.unavailable_reason}</p>}
                {loading[id] && <p className="mt-1 text-xs text-navy-500" role="status">Loading layer…</p>}
                {errors[id] && <p className="mt-1 text-xs text-red-700" role="alert">{errors[id]}</p>}
                {id === 'camx_pm25' && visible.camx_pm25 && (
                  <div className="mt-2">
                    <label htmlFor="camx-month" className="text-xs text-navy-700">Modelled period</label>
                    <select id="camx-month" value={camxMonth} onChange={(e) => onCamxMonth(e.target.value as CamxMonth)} className="ml-2 rounded border border-navy-200 bg-white px-1 py-0.5 text-xs">
                      {CAMX_MONTHS.map((m) => (
                        <option key={m} value={m}>{m === 'annual' ? 'Annual mean' : m.toUpperCase()}</option>
                      ))}
                    </select>
                  </div>
                )}
              </div>
            </div>
          </div>
        )
      })}
      <div className="rounded border border-navy-100 bg-white p-2">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={basemapOn} disabled={!basemapAvailable} onChange={(e) => onBasemap(e.target.checked)} className="accent-leaf-600" />
          Street basemap (OpenFreeMap)
        </label>
        {!basemapAvailable && <p className="mt-1 text-xs text-navy-500">Basemap service not reachable; showing data on a plain background.</p>}
      </div>
    </section>
  )
}
