import React, { useMemo } from 'react'
import { useStickToBottom } from '../hooks/useStickToBottom'
import { useStore } from '../state/store'
import ScrollToBottomButton from './ScrollToBottomButton'

const interestingEvents = new Set([
  'run_started',
  'run_resumed',
  'agent_step',
  'model_routed',
  'model_escalated',
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
  const scrollKey = useMemo(() => `${events.length}:${events[events.length - 1]?.type ?? ''}`, [events])
  const { containerRef, endRef, showJumpButton, scrollToBottom, handleScroll } = useStickToBottom([scrollKey])

  function renderPayload(event: { type: string; payload: any }) {
    if (event.type === 'run_failed') {
      return event.payload?.user_message ?? event.payload?.reflection?.summary ?? 'Nao consegui concluir a tarefa.'
    }
    if (event.type === 'run_finished') {
      return event.payload?.reflection?.summary ?? 'Execucao concluida.'
    }
    if (event.type === 'agent_step') {
      return event.payload?.summary ?? 'Passo do agente.'
    }
    if (event.type === 'model_routed') {
      const model = event.payload?.selected_model ?? event.payload?.routing?.selected_model
      return model ? `Modelo selecionado: ${model}` : 'Modelo roteado.'
    }
    if (event.type === 'model_escalated') {
      return `Escalada: ${event.payload?.from_model ?? '?'} → ${event.payload?.to_model ?? '?'}`
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
    <div className="rounded-xl bg-gray-900 p-4">
      <h3 className="font-semibold text-gray-100">Timeline</h3>
      <div className="relative mt-3">
        <div
          ref={containerRef}
          onScroll={handleScroll}
          className="chat-scroll max-h-80 overflow-y-auto scroll-smooth pr-1"
        >
          <div className="space-y-2">
            {events.length === 0 ? <div className="text-sm text-gray-400">Sem eventos relevantes ainda.</div> : null}
            {events.map((event, index) => (
              <div key={`${event.type}-${index}`} className="rounded-lg border border-gray-800 bg-gray-950/60 p-3 text-sm">
                <div className="font-medium text-gray-100">{event.type}</div>
                <div className="mt-1 whitespace-pre-wrap text-xs text-gray-400">{renderPayload(event)}</div>
              </div>
            ))}
            <div ref={endRef} className="h-px" aria-hidden />
          </div>
        </div>
        <ScrollToBottomButton visible={showJumpButton} onClick={() => scrollToBottom('smooth')} label="Ver timeline recente" />
      </div>
    </div>
  )
}

export default TimelinePanel
