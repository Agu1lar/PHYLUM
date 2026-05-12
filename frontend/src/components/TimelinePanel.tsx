import React from 'react'
import { useStore } from '../state/store'

const interestingEvents = new Set([
  'run_started',
  'run_resumed',
  'agent_step',
  'tool_call_proposed',
  'task_planned',
  'task_started',
  'task_retry_scheduled',
  'task_finished',
  'approval_requested',
  'approval_resolved',
  'user_input_requested',
  'user_input_received',
  'run_paused',
  'run_finished',
  'run_failed',
  'run_cancelled',
])

const TimelinePanel: React.FC = () => {
  const currentRun = useStore(state => (state.currentRunId ? state.runs[state.currentRunId] : null))
  const events = (currentRun?.events ?? []).filter(event => interestingEvents.has(event.type))

  function renderPayload(event: { type: string; payload: any }) {
    if (event.type === 'run_failed') {
      return event.payload?.user_message ?? event.payload?.reflection?.summary ?? 'Nao consegui concluir a tarefa.'
    }
    if (event.type === 'run_finished') {
      return event.payload?.reflection?.summary ?? 'Execucao concluida.'
    }
    if (event.type === 'user_input_requested') {
      return event.payload?.handoff?.prompt ?? 'Preciso de mais contexto para continuar.'
    }
    if (event.type === 'task_finished') {
      return event.payload?.result?.action_result?.summary ?? 'Acao concluida.'
    }
    if (event.type === 'task_started') {
      return event.payload?.task?.title ?? event.payload?.task_id ?? 'Task iniciada.'
    }
    if (event.type === 'approval_requested') {
      return event.payload?.approval?.reason ?? event.payload?.approval?.title ?? 'Aprovacao pendente.'
    }
    if (event.type === 'approval_resolved') {
      return event.payload?.status === 'approved' ? 'Aprovacao concedida.' : 'Aprovacao rejeitada.'
    }
    if (event.type === 'tool_call_proposed') {
      return `${event.payload?.tool_name ?? event.payload?.tool ?? 'tool'} ${JSON.stringify(event.payload?.arguments ?? event.payload?.params ?? {})}`
    }
    return JSON.stringify(event.payload, null, 2)
  }

  return (
    <div className="rounded bg-gray-900 p-4">
      <h3 className="font-semibold">Timeline</h3>
      <div className="mt-3 space-y-2">
        {events.length === 0 ? <div className="text-sm text-gray-400">Sem eventos relevantes ainda.</div> : null}
        {events.map((event, index) => (
          <div key={`${event.type}-${index}`} className="rounded border border-gray-800 bg-gray-950/60 p-3 text-sm">
            <div className="font-medium text-gray-100">{event.type}</div>
            <div className="mt-1 text-xs text-gray-400 whitespace-pre-wrap">
              {renderPayload(event)}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default TimelinePanel
