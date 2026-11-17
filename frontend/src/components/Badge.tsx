import { kindBadge } from '../lib/layers'

const TONES: Record<string, string> = {
  measured: 'bg-leaf-100 text-leaf-700 border-leaf-600/30',
  modelled: 'bg-sky-100 text-sky-700 border-sky-700/30',
  estimated: 'bg-sand-100 text-sand-700 border-sand-700/30',
  static: 'bg-navy-100 text-navy-700 border-navy-500/30',
  mixed: 'bg-navy-100 text-navy-700 border-navy-500/30',
}

export function KindBadge({ dataKind }: { dataKind: string }) {
  const { label, tone } = kindBadge(dataKind)
  return (
    <span className={`inline-block rounded border px-1.5 py-0.5 text-[11px] font-medium ${TONES[tone]}`} title={dataKind}>
      {label}
    </span>
  )
}

export function HistoricalBadge({ text = 'Historical' }: { text?: string }) {
  return <span className="inline-block rounded border border-navy-200 bg-white px-1.5 py-0.5 text-[11px] font-medium text-navy-700">{text}</span>
}
