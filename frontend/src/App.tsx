import { useEffect, useState } from 'react'
import { fetchHealth, type HealthResponse } from './api'

type State =
  | { kind: 'loading' }
  | { kind: 'ok'; data: HealthResponse }
  | { kind: 'error'; message: string }

export default function App() {
  const [state, setState] = useState<State>({ kind: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    fetchHealth(controller.signal)
      .then((data) => setState({ kind: 'ok', data }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setState({ kind: 'error', message: err instanceof Error ? err.message : String(err) })
      })
    return () => controller.abort()
  }, [])

  return (
    <main className="mx-auto max-w-2xl px-4 py-12">
      <h1 className="text-3xl font-semibold tracking-tight">AirWatch AI</h1>
      <p className="mt-2 text-slate-600 dark:text-slate-400">
        Bishkek Urban Air Quality Research &amp; Intelligent Sensor Planning Platform
      </p>

      <section
        aria-live="polite"
        className="mt-8 rounded-lg border border-slate-300 p-4 dark:border-slate-700"
      >
        <h2 className="text-sm font-medium uppercase tracking-wide text-slate-500">
          Backend status
        </h2>
        {state.kind === 'loading' && <p className="mt-2">Checking backend…</p>}
        {state.kind === 'error' && (
          <p className="mt-2 text-red-700 dark:text-red-400">
            Backend unreachable: {state.message}. Start it with{' '}
            <code>uvicorn app.main:app --reload</code> in <code>backend/</code>.
          </p>
        )}
        {state.kind === 'ok' && (
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-slate-500">Status</dt>
            <dd>{state.data.status}</dd>
            <dt className="text-slate-500">Service</dt>
            <dd>{state.data.service}</dd>
            <dt className="text-slate-500">Version</dt>
            <dd>{state.data.version}</dd>
            <dt className="text-slate-500">Python</dt>
            <dd>{state.data.python}</dd>
          </dl>
        )}
      </section>

      <p className="mt-6 text-xs text-slate-500">
        Foundation build: no air-quality data is loaded or displayed yet.
      </p>
    </main>
  )
}
