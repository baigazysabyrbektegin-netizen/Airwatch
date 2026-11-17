import type { ExplorerResponse, QualityWarning } from '../../lib/types'

export function QualityWarnings({ warnings }: { warnings: QualityWarning[] }) {
  const hard = warnings.filter((w) => w.severity === 'warning')
  const info = warnings.filter((w) => w.severity === 'info')
  if (warnings.length === 0) return null
  return (
    <section aria-label="Data-quality warnings" className="space-y-2">
      {hard.length > 0 && (
        <ul className="space-y-1 rounded border border-sand-700/30 bg-sand-100 p-3 text-xs text-sand-700">
          {hard.map((w) => (
            <li key={w.code}>
              <strong>Data-quality warning.</strong> {w.message}
            </li>
          ))}
        </ul>
      )}
      {info.length > 0 && (
        <details className="rounded border border-navy-100 bg-white p-3 text-xs text-navy-700">
          <summary className="cursor-pointer font-medium">Data notes ({info.length})</summary>
          <ul className="mt-2 list-disc space-y-1 pl-4">
            {info.map((w) => (
              <li key={w.code}>{w.message}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}

export function ProvenanceFooter({ data }: { data: ExplorerResponse }) {
  const p = data.weather?.provenance
  return (
    <footer aria-label="Data sources" className="rounded border border-navy-100 bg-white p-3 text-xs text-navy-700">
      <h2 className="font-semibold text-navy-900">Data sources and vintage</h2>
      <ul className="mt-1 list-disc space-y-1 pl-4">
        <li>
          <strong>PM2.5:</strong> {data.station.name} — {data.station.data_kind}. Historical record {data.query.station_data_period.first} to {data.query.station_data_period.last}; not current conditions.{' '}
          {data.station.timestamp_convention}
        </li>
        {p ? (
          <li>
            <strong>Weather:</strong> ERA5 reanalysis (modelled historical weather, {p.spatial_resolution}) via Open-Meteo — <em>not</em> a forecast and <em>not</em> a local station measurement. Grid cell{' '}
            {p.grid.returned_latitude}, {p.grid.returned_longitude} (requested {p.grid.requested_latitude}, {p.grid.requested_longitude}); retrieved {p.retrieved_at_utc?.slice(0, 10)}.{' '}
            {p.licence}{' '}
            <a className="underline" href="https://open-meteo.com/" target="_blank" rel="noopener noreferrer">
              Weather data by Open-Meteo.com
            </a>{' '}
            (CC BY 4.0). Generated using Copernicus Climate Change Service information.
          </li>
        ) : (
          <li>
            <strong>Weather:</strong> not shown{data.weather?.unavailable ? ' (the ERA5 file has not been created yet)' : ''}.
          </li>
        )}
        <li>{data.alignment.statement}</li>
      </ul>
    </footer>
  )
}
