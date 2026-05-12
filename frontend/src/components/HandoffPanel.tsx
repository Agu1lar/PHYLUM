import React, { useMemo, useState } from 'react'
import { useStore } from '../state/store'

const HandoffPanel: React.FC = () => {
  const currentRun = useStore(state => (state.currentRunId ? state.runs[state.currentRunId] : null))
  const pendingHandoff = currentRun?.pending_handoff ?? null
  const [text, setText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const API_BASE = (import.meta as any).env?.VITE_API_URL || 'http://127.0.0.1:8000'

  const placeholder = useMemo(() => {
    if (!pendingHandoff) return ''
    return pendingHandoff.allow_free_text ? 'Escreva a resposta para continuar a run' : 'Escolha uma opcao abaixo'
  }, [pendingHandoff])

  async function respond(response: any) {
    if (!currentRun || !pendingHandoff || submitting) return
    setSubmitting(true)
    try {
      await fetch(`${API_BASE}/run/${encodeURIComponent(currentRun.request_id)}/reply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ response }),
      })
      await fetch(`${API_BASE}/run/${encodeURIComponent(currentRun.request_id)}/resume`, {
        method: 'POST',
      })
      setText('')
    } catch (error) {
      console.error('handoff reply failed', error)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="rounded bg-gray-900 p-4">
      <h3 className="font-semibold">Handoff</h3>
      {!pendingHandoff ? (
        <div className="mt-2 text-sm text-gray-400">Nenhum handoff pendente.</div>
      ) : (
        <div className="mt-2 space-y-3 text-sm">
          <div className="rounded border border-amber-700 bg-amber-950/30 p-3">
            <div className="font-medium text-amber-100">{pendingHandoff.title}</div>
            <div className="mt-1 text-amber-50/90">{pendingHandoff.prompt}</div>
            {pendingHandoff.reason ? <div className="mt-2 text-xs text-amber-200/70">Motivo: {pendingHandoff.reason}</div> : null}
          </div>
          {pendingHandoff.options?.length ? (
            <div className="flex flex-wrap gap-2">
              {pendingHandoff.options.map(option => (
                <button
                  key={option.id}
                  onClick={() => respond({ choice: option.value ?? option.id, label: option.label })}
                  disabled={submitting}
                  className="rounded bg-blue-700 px-3 py-2 text-sm disabled:opacity-60"
                >
                  {option.label}
                </button>
              ))}
            </div>
          ) : null}
          {pendingHandoff.allow_free_text ? (
            <div className="space-y-2">
              <textarea
                value={text}
                onChange={event => setText(event.target.value)}
                placeholder={placeholder}
                className="min-h-[88px] w-full rounded border border-gray-700 bg-gray-950 p-2"
              />
              <button
                onClick={() => respond({ text })}
                disabled={submitting || !text.trim()}
                className="rounded bg-green-700 px-3 py-2 text-sm disabled:opacity-60"
              >
                {submitting ? 'Enviando...' : 'Responder e retomar'}
              </button>
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}

export default HandoffPanel
