import { useState } from 'react'
import type { MetadataResponse } from '../lib/types'

/** Always-visible short statement of what the map does and does not show, plus the full source list on demand. */
export function ProvenanceNotice({ metadata, metaError, onOpenSources }: { metadata: MetadataResponse | null; metaError: string | null; onOpenSources: () => void }) {
  return (
    <div className="border-b border-sand-700/30 bg-sand-100 px-4 py-2 text-xs text-sand-700">
      <strong>Historical research data.</strong> Station records are archived measurements (US Embassy: reference monitor; Clarity: low-cost sensors, calibration not independently verified). CAMx PM2.5 is a <em>model</em> output, emissions are a <em>2018 inventory</em>,
      population a <em>2019 estimate</em>. None of these show current air quality.{' '}
      <button type="button" onClick={onOpenSources} className="font-medium underline">
        Sources &amp; limitations
      </button>
      {metaError && <span className="ml-2 text-red-700">(Source details unavailable: {metaError})</span>}
      {metadata && metadata.unavailable_manifests.length > 0 && <span className="ml-2 text-red-700">Missing manifests: {metadata.unavailable_manifests.join(', ')}</span>}
    </div>
  )
}

export function MetadataDialog({ metadata, onClose }: { metadata: MetadataResponse | null; onClose: () => void }) {
  const [tab, setTab] = useState<'rules' | 'datasets' | 'licence'>('rules')
  return (
    <div role="dialog" aria-modal="true" aria-label="Data sources and limitations" className="fixed inset-0 z-50 flex items-center justify-center bg-navy-950/50 p-4">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-lg bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-navy-100 px-4 py-3">
          <h2 className="font-semibold">Data sources &amp; limitations</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded px-2 py-1 text-navy-500 hover:bg-navy-100">✕</button>
        </div>
        <div className="flex gap-1 border-b border-navy-100 px-3 pt-2 text-sm">
          {(['rules', 'datasets', 'licence'] as const).map((t) => (
            <button key={t} type="button" onClick={() => setTab(t)} className={`rounded-t px-3 py-1.5 ${tab === t ? 'bg-navy-100 font-medium' : 'text-navy-500'}`}>
              {t === 'rules' ? 'How to read the map' : t === 'datasets' ? 'Datasets' : 'Licence & basemap'}
            </button>
          ))}
        </div>
        <div className="overflow-y-auto p-4 text-sm">
          {!metadata && <p role="status">Loading…</p>}
          {metadata && tab === 'rules' && (
            <ul className="list-disc space-y-2 pl-5">{metadata.presentation_rules.map((r, i) => <li key={i}>{r}</li>)}</ul>
          )}
          {metadata && tab === 'datasets' && (
            <div className="space-y-4">
              {metadata.datasets.map((d) => (
                <div key={d.id} className="rounded border border-navy-100 p-3">
                  <div className="font-medium">{d.title}</div>
                  <div className="mt-0.5 text-xs text-navy-500">{[d.data_kind, d.vintage, d.resolution, d.unit].filter(Boolean).join(' · ')}</div>
                  {d.limitations.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-navy-700">{d.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul>}
                </div>
              ))}
            </div>
          )}
          {metadata && tab === 'licence' && (
            <div className="space-y-3">
              <p>{metadata.licence.attribution}</p>
              {metadata.licence.notes.length > 0 && <ul className="list-disc space-y-1 pl-5">{metadata.licence.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
              <p>{metadata.licence.other}</p>
              <p>
                <strong>Basemap:</strong> {metadata.basemap.default_provider}. {metadata.basemap.usage} {metadata.basemap.attribution}{' '}
                <a className="underline" href={metadata.basemap.policy_url} target="_blank" rel="noopener noreferrer">Usage policy</a>. {metadata.basemap.fallback}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
