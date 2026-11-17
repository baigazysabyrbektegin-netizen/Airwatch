import { formatNumber, GRID_LAYERS, legendBins, LAYER_STYLES, ROAD_COLORS, type GridLayerId, type ToggleId } from '../lib/layers'
import type { LayerFeatureCollection, LayerInfo } from '../lib/types'

interface Props {
  visible: Record<ToggleId, boolean>
  layerData: Partial<Record<ToggleId, LayerFeatureCollection>>
  layers: LayerInfo[]
  camxMonth: string
}

export function Legend({ visible, layerData, layers, camxMonth }: Props) {
  const grid = GRID_LAYERS.find((g) => visible[g]) as GridLayerId | undefined
  const style = grid ? LAYER_STYLES[grid] : null
  const data = grid ? layerData[grid] : undefined
  const bins = style && data ? legendBins(data.metadata?.stats?.[style.valueField as string], style.ramp as string[]) : []
  const info = grid ? layers.find((l) => l.id === grid) : undefined

  return (
    <aside aria-label="Legend" className="max-w-[17rem] rounded-lg border border-navy-200 bg-white/95 p-3 text-xs shadow">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-navy-500">Legend</h2>
      {visible.stations && (
        <ul className="mt-2 space-y-1">
          <li className="flex items-center gap-2"><span className="inline-block h-3 w-3 rounded-full border border-white bg-leaf-600 ring-1 ring-navy-200" />Clarity low-cost sensor</li>
          <li className="flex items-center gap-2"><span className="inline-block h-3 w-3 rounded-full border-2 border-navy-900 bg-white" />US Embassy reference monitor (approximate location)</li>
        </ul>
      )}
      {grid && style && (
        <div className="mt-3">
          <div className="font-medium">
            {style.label}
            {grid === 'camx_pm25' && <span className="font-normal text-navy-500"> · {camxMonth === 'annual' ? 'annual mean' : camxMonth.toUpperCase()}</span>}
          </div>
          <div className="text-navy-500">{style.unitLabel}</div>
          {bins.length > 0 ? (
            <ul className="mt-1 space-y-0.5">
              {bins.map((b, i) => (
                <li key={i} className="flex items-center gap-2">
                  <span className="inline-block h-3 w-5 rounded-sm border border-navy-200" style={{ background: b.color }} />
                  {formatNumber(b.from)} – {formatNumber(b.to)}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-navy-500">{data ? 'No class breaks available for this layer.' : 'Loading…'}</p>
          )}
          <p className="mt-1 text-navy-500">Classes follow the 5th/25th/50th/75th/95th percentiles of the displayed cells. Transparent cells have no data.</p>
          {info && <p className="mt-1 font-medium text-sand-700">{info.vintage} — historical, not current conditions.</p>}
        </div>
      )}
      {visible.roads && (
        <ul className="mt-3 space-y-1">
          <li className="font-medium">Roads (OpenStreetMap)</li>
          {Object.entries(ROAD_COLORS).map(([k, c]) => (
            <li key={k} className="flex items-center gap-2"><span className="inline-block h-0.5 w-5" style={{ background: c, height: 3 }} />{k}</li>
          ))}
        </ul>
      )}
      {visible.context_features && <p className="mt-3"><span className="mr-2 inline-block h-3 w-3 bg-sand-700 align-middle" />Mapped emission-source context</p>}
      {visible.administrative_boundary && <p className="mt-3"><span className="mr-2 inline-block w-5 border-t-2 border-dashed border-navy-900 align-middle" />Administrative boundary (country level)</p>}
    </aside>
  )
}
