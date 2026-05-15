import React, { useMemo } from 'react'
import { RunRecord } from '../state/store'

type Props = {
  runs: RunRecord[]
  currentRunId: string | null
  onSelectRun: (requestId: string) => void
  onNewChat: () => void
  onDeleteRun: (requestId: string) => void
}

function formatLabel(run: RunRecord): string {
  const inputText = run.inputs?.text?.trim()
  if (inputText) {
    return inputText.length > 48 ? `${inputText.slice(0, 45)}...` : inputText
  }
  const reflection = run.reflection?.summary?.trim()
  if (reflection) {
    return reflection.length > 48 ? `${reflection.slice(0, 45)}...` : reflection
  }
  return `Chat ${run.request_id.slice(0, 8)}`
}

function formatTimestamp(value?: string): string {
  if (!value) return 'sem data'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

const ChatHistoryPanel: React.FC<Props> = ({ runs, currentRunId, onSelectRun, onNewChat, onDeleteRun }) => {
  const sortedRuns = useMemo(
    () =>
      [...runs].sort((a, b) => {
        const left = a.last_updated ?? a.created_at ?? ''
        const right = b.last_updated ?? b.created_at ?? ''
        return right.localeCompare(left)
      }),
    [runs],
  )

  return (
    <aside className="flex min-h-0 flex-col rounded border border-gray-700 bg-gray-800">
      <div className="border-b border-gray-700 p-3">
        <button onClick={onNewChat} className="w-full rounded bg-blue-600 px-3 py-2 text-sm font-medium text-white">
          Novo chat
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {sortedRuns.length === 0 ? (
          <div className="rounded border border-dashed border-gray-700 p-3 text-sm text-gray-400">
            Nenhum chat salvo ainda.
          </div>
        ) : (
          <div className="space-y-2">
            {sortedRuns.map(run => {
              const selected = run.request_id === currentRunId
              return (
                <div
                  key={run.request_id}
                  onClick={() => onSelectRun(run.request_id)}
                  className={`cursor-pointer rounded border p-3 ${
                    selected ? 'border-blue-500 bg-blue-950/30' : 'border-gray-700 bg-gray-900/40 hover:bg-gray-900/70'
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium text-gray-100">{formatLabel(run)}</div>
                      <div className="mt-1 flex items-center gap-2 text-xs text-gray-400">
                        <span
                          className={`rounded-full px-2 py-0.5 ${
                            ['running', 'planning', 'recovering', 'resuming'].includes(run.status)
                              ? 'bg-blue-900/50 text-blue-200'
                              : run.status === 'completed'
                                ? 'bg-emerald-900/40 text-emerald-200'
                                : run.status === 'failed'
                                  ? 'bg-red-900/40 text-red-200'
                                  : 'bg-gray-800 text-gray-400'
                          }`}
                        >
                          {run.status}
                        </span>
                        <span>{formatTimestamp(run.last_updated ?? run.created_at)}</span>
                      </div>
                    </div>
                    <button
                      onClick={event => {
                        event.stopPropagation()
                        onDeleteRun(run.request_id)
                      }}
                      className="rounded border border-gray-600 px-2 py-1 text-xs text-gray-300 hover:bg-red-950/40"
                    >
                      Excluir
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </aside>
  )
}

export default ChatHistoryPanel
