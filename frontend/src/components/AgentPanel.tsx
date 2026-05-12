import React from 'react'
import { useStore } from '../state/store'

const AgentPanel: React.FC = () => {
  const currentRun = useStore(state => (state.currentRunId ? state.runs[state.currentRunId] : null))
  const supportedTools = useStore(state => state.supportedTools)
  const tasks = currentRun?.tasks ?? []
  const completed = tasks.filter(task => task.status === 'completed').length
  const API_BASE = (import.meta as any).env?.VITE_API_URL || 'http://127.0.0.1:8000'
  const isCancelable = currentRun ? !['completed', 'failed', 'cancelled'].includes(currentRun.status) : false
  const executionMode = currentRun?.reflection?.details?.execution_mode ?? currentRun?.outputs?.execution_mode ?? currentRun?.runtime_mode ?? 'agentic'

  async function cancelRun() {
    if (!currentRun || !isCancelable) return
    try {
      await fetch(`${API_BASE}/run/${encodeURIComponent(currentRun.request_id)}/cancel`, { method: 'POST' })
    } catch (error) {
      console.error('cancel failed', error)
    }
  }

  return (
    <div className="rounded border border-gray-700 bg-gray-800 p-4">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">Execution Summary</h2>
        <button
          onClick={cancelRun}
          disabled={!isCancelable}
          className="rounded bg-amber-600 px-3 py-1 text-sm disabled:opacity-40"
        >
          Cancel run
        </button>
      </div>
      {!currentRun ? (
        <div className="space-y-3">
          <p className="text-sm text-gray-400">Envie uma instrução para iniciar a primeira run.</p>
          <div className="rounded bg-gray-900 p-3">
            <div className="text-xs uppercase text-gray-500">Canonical Tools</div>
            <div className="mt-2 flex flex-wrap gap-2">
              {supportedTools.map(tool => (
                <span key={tool.function.name} className="rounded bg-gray-800 px-2 py-1 text-xs text-gray-200">
                  {tool.function.name}
                </span>
              ))}
            </div>
          </div>
        </div>
      ) : (
        <div className="space-y-3 text-sm">
          <div className="grid grid-cols-2 gap-2">
            <div className="rounded bg-gray-900 p-3">
              <div className="text-xs uppercase text-gray-500">Status</div>
              <div className="mt-1 font-medium">{currentRun.status}</div>
            </div>
            <div className="rounded bg-gray-900 p-3">
              <div className="text-xs uppercase text-gray-500">Current Task</div>
              <div className="mt-1 font-medium">{currentRun.current_task_id ?? 'none'}</div>
            </div>
            <div className="rounded bg-gray-900 p-3">
              <div className="text-xs uppercase text-gray-500">Tasks</div>
              <div className="mt-1 font-medium">{completed}/{tasks.length} concluida(s)</div>
            </div>
            <div className="rounded bg-gray-900 p-3">
              <div className="text-xs uppercase text-gray-500">Approvals</div>
              <div className="mt-1 font-medium">{currentRun.approvals.length}</div>
            </div>
            <div className="rounded bg-gray-900 p-3">
              <div className="text-xs uppercase text-gray-500">Runtime</div>
              <div className="mt-1 font-medium">{executionMode}</div>
            </div>
            <div className="rounded bg-gray-900 p-3">
              <div className="text-xs uppercase text-gray-500">Provider</div>
              <div className="mt-1 font-medium">{currentRun.provider ?? 'manual/local'}</div>
            </div>
            <div className="rounded bg-gray-900 p-3">
              <div className="text-xs uppercase text-gray-500">Handoffs</div>
              <div className="mt-1 font-medium">{currentRun.handoffs?.length ?? 0}</div>
            </div>
          </div>
          {currentRun.error ? (
            <div className="rounded border border-red-900 bg-red-950/40 p-3 text-red-200">{currentRun.error}</div>
          ) : null}
          {currentRun.reflection ? (
            <div className="rounded bg-gray-900 p-3">
              <div className="text-xs uppercase text-gray-500">Final Reflection</div>
              <div className="mt-1">{currentRun.reflection.summary ?? currentRun.reflection.verdict}</div>
            </div>
          ) : null}
          <div className="rounded bg-gray-900 p-3">
            <div className="text-xs uppercase text-gray-500">Canonical Tools</div>
            <div className="mt-2 flex flex-wrap gap-2">
              {supportedTools.map(tool => (
                <span key={tool.function.name} className="rounded bg-gray-800 px-2 py-1 text-xs text-gray-200">
                  {tool.function.name}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
export default AgentPanel
