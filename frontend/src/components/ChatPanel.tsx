import React, { useMemo, useState } from 'react'
import { getApiBase } from '../lib/runtimeConfig'
import { useStickToBottom } from '../hooks/useStickToBottom'
import { useStore } from '../state/store'
import ScrollToBottomButton from './ScrollToBottomButton'

const threadEventTypes = new Set([
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
  'approval_grant_created',
  'approval_grant_revoked',
  'user_input_requested',
  'user_input_received',
  'run_paused',
  'run_finished',
  'run_failed',
  'run_cancelled',
])

const TERMINAL_RUN_STATUSES = new Set(['completed', 'failed', 'cancelled'])

const statusLabels: Record<string, string> = {
  planning: 'Planejando',
  running: 'Executando',
  recovering: 'Recuperando',
  resuming: 'Retomando',
  paused: 'Pausado',
  awaiting_input: 'Aguardando resposta',
  awaiting_approval: 'Aguardando aprovacao',
  cancelling: 'Cancelando',
  completed: 'Concluido',
  failed: 'Falhou',
  cancelled: 'Cancelado',
}

const ChatPanel: React.FC<{ showAdvanced?: boolean }> = ({ showAdvanced = true }) => {
  const messages = useStore(state => state.messages)
  const addMessage = useStore(state => state.addMessage)
  const hydrateRun = useStore(state => state.hydrateRun)
  const currentRun = useStore(state => (state.currentRunId ? state.runs[state.currentRunId] : null))
  const providers = useStore(state => state.providers)
  const selectedRuntimeMode = useStore(state => state.selectedRuntimeMode)
  const selectedProvider = useStore(state => state.selectedProvider)
  const selectedModel = useStore(state => state.selectedModel)
  const selectRuntimeMode = useStore(state => state.selectRuntimeMode)
  const selectProvider = useStore(state => state.selectProvider)
  const selectModel = useStore(state => state.selectModel)
  const [text, setText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const API_BASE = getApiBase()
  const configuredProviders = providers.filter(provider => provider.configured)
  const currentProvider = providers.find(provider => provider.provider === selectedProvider) ?? null
  const [confirmingApprovalId, setConfirmingApprovalId] = useState<string | null>(null)
  const threadEvents = useMemo(
    () => (currentRun?.events ?? []).filter(event => threadEventTypes.has(event.type)),
    [currentRun?.events],
  )
  const scrollSignature = useMemo(() => {
    const last = threadEvents[threadEvents.length - 1]
    const lastKey = last
      ? `${last.type}:${JSON.stringify(last.payload?.summary ?? last.payload?.task?.id ?? last.payload?.status ?? '')}`
      : ''
    return `${threadEvents.length}|${messages.length}|${currentRun?.status ?? ''}|${lastKey}`
  }, [threadEvents, messages.length, currentRun?.status])
  const { containerRef, endRef, showJumpButton, scrollToBottom, handleScroll } = useStickToBottom([scrollSignature])
  const isActiveRun = Boolean(currentRun?.status && !TERMINAL_RUN_STATUSES.has(currentRun.status))
  const latestActivity = useMemo(() => {
    for (let index = threadEvents.length - 1; index >= 0; index -= 1) {
      const event = threadEvents[index]
      if (event.type === 'agent_step') return event.payload?.summary as string | undefined
      if (event.type === 'task_started') return `Executando: ${event.payload?.task?.title ?? event.payload?.task?.id ?? 'tarefa'}`
      if (event.type === 'task_finished') return event.payload?.result?.action_result?.summary as string | undefined
    }
    return undefined
  }, [threadEvents])

  const submit = async () => {
    const trimmed = text.trim()
    if (!trimmed || submitting) return
    setSubmitting(true)
    scrollToBottom('auto')
    try {
      let response: Response
      if (currentRun?.pending_handoff?.allow_free_text) {
        addMessage({ role: 'user', text: trimmed })
        await fetch(`${API_BASE}/run/${encodeURIComponent(currentRun.request_id)}/reply`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ response: { text: trimmed } }),
        })
        response = await fetch(`${API_BASE}/run/${encodeURIComponent(currentRun.request_id)}/resume`, {
          method: 'POST',
        })
      } else {
        response = await fetch(`${API_BASE}/run`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            inputs: { text: trimmed },
            runtime_mode: selectedRuntimeMode === 'manual_assist' ? 'heuristic' : selectedRuntimeMode,
            provider: selectedRuntimeMode === 'agentic' ? selectedProvider : null,
            model: selectedRuntimeMode === 'agentic' ? selectedModel || null : null,
          }),
        })
      }
      if (!response.ok) {
        throw new Error(`run request failed: ${response.status}`)
      }
      const data = await response.json()
      if (data.state) {
        hydrateRun(data.state)
      }
      setText('')
    } catch (error) {
      console.error(error)
      addMessage({ role: 'agent', text: 'Nao foi possivel iniciar a execucao.' })
    } finally {
      setSubmitting(false)
    }
  }

  const handleComposerKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void submit()
    }
  }

  async function postApproval(
    id: string,
    status: 'approved' | 'rejected',
    confirmationLevel?: string,
    scope: 'single' | 'run_scope' = 'single',
    requestId?: string,
  ) {
    if (status === 'approved' && confirmationLevel === 'double' && confirmingApprovalId !== id) {
      setConfirmingApprovalId(id)
      return
    }
    setConfirmingApprovalId(null)
    try {
      await fetch(`${API_BASE}/approval/${encodeURIComponent(id)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status, scope }),
      })
      if (requestId) {
        await fetch(`${API_BASE}/run/${encodeURIComponent(requestId)}/resume`, {
          method: 'POST',
        })
      }
    } catch (error) {
      console.error(error)
    }
  }

  async function answerHandoff(value: any) {
    if (!currentRun?.pending_handoff) return
    try {
      await fetch(`${API_BASE}/run/${encodeURIComponent(currentRun.request_id)}/reply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ response: value }),
      })
      await fetch(`${API_BASE}/run/${encodeURIComponent(currentRun.request_id)}/resume`, {
        method: 'POST',
      })
    } catch (error) {
      console.error(error)
    }
  }

  function renderUserBubble(content: string, key: string) {
    return (
      <div key={key} className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-blue-600 px-4 py-2.5 text-sm text-white shadow-sm">
          {content}
        </div>
      </div>
    )
  }

  function renderAgentBubble(content: string, key: string, tone: 'default' | 'muted' = 'default') {
    return (
      <div key={key} className="flex justify-start">
        <div
          className={`max-w-[90%] rounded-2xl rounded-bl-md px-4 py-2.5 text-sm shadow-sm ${
            tone === 'muted' ? 'border border-gray-700/80 bg-gray-900/60 text-gray-300' : 'border border-gray-700 bg-gray-900/90 text-gray-100'
          }`}
        >
          {content}
        </div>
      </div>
    )
  }

  function renderActivityLine(summary: string, key: string) {
    return (
      <div key={key} className="flex items-start gap-2 py-0.5 pl-1 text-xs text-gray-400">
        <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-blue-400/80" aria-hidden />
        <span className="leading-relaxed">{summary}</span>
      </div>
    )
  }

  function renderThreadEvent(event: { type: string; payload: any }, index: number) {
    const key = `${event.type}-${index}`

    if (event.type === 'agent_step') {
      const summary = event.payload?.summary?.trim()
      if (!summary) return null
      return renderActivityLine(summary, key)
    }

    if (event.type === 'model_routed') {
      const model = event.payload?.selected_model ?? event.payload?.routing?.selected_model
      const tier = event.payload?.routing?.tier
      if (!model) return null
      return renderActivityLine(`Modelo: ${model}${tier ? ` (${tier})` : ''}`, key)
    }

    if (event.type === 'model_escalated') {
      const fromModel = event.payload?.from_model
      const toModel = event.payload?.to_model
      if (!toModel) return null
      return renderActivityLine(
        fromModel ? `Escalando modelo: ${fromModel} → ${toModel}` : `Escalando para modelo: ${toModel}`,
        key,
      )
    }

    if (event.type === 'tool_call_proposed') {
      const toolName = event.payload?.tool_name ?? event.payload?.tool ?? 'tool'
      const args = JSON.stringify(event.payload?.preview ?? event.payload?.arguments ?? event.payload?.params ?? {}, null, 2)
      return (
        <div key={key} className="rounded-xl border border-blue-900/50 bg-blue-950/25 p-3">
          <div className="text-[11px] font-medium uppercase tracking-wide text-blue-300">Proposta de acao</div>
          <div className="mt-1 font-medium text-blue-100">{toolName}</div>
          <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-black/25 p-2 text-xs text-blue-50">{args}</pre>
        </div>
      )
    }

    if (event.type === 'approval_requested') {
      const approvalId = event.payload?.approval?.approval_id
      const approval = currentRun?.approvals.find(item => item.approval_id === approvalId) ?? event.payload?.approval
      const confirmationLevel = approval?.details?.confirmation_level ?? approval?.approval_mode ?? 'single'
      const predictedEffects = approval?.details?.predicted_effects ?? []
      const availableScopes: string[] = approval?.details?.available_scopes ?? ['single']
      const command = approval?.details?.command
      const commandExplanation = approval?.details?.command_explanation
      return (
        <div key={key} className="rounded-xl border border-amber-800/80 bg-amber-950/30 p-3">
          <div className="text-[11px] font-medium uppercase tracking-wide text-amber-300">Aprovacao necessaria</div>
          <div className="mt-1 font-medium text-amber-100">{approval?.title}</div>
          <div className="mt-1 text-sm text-amber-50">{approval?.reason}</div>
          {commandExplanation ? (
            <div className="mt-2 rounded-lg border border-amber-700/50 bg-amber-900/20 p-2 text-sm text-amber-50">
              {commandExplanation}
            </div>
          ) : null}
          {command ? (
            <div className="mt-2">
              <div className="text-[11px] uppercase tracking-wide text-amber-300">Comando</div>
              <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-black/25 p-2 text-xs text-amber-50">
                {command}
              </pre>
            </div>
          ) : null}
          {predictedEffects.length > 0 ? (
            <ul className="mt-2 space-y-1 text-xs text-amber-100">
              {predictedEffects.map((effect: any, effectIndex: number) => (
                <li key={`${approvalId}-effect-${effectIndex}`}>
                  {effect.operation}
                  {effect.targets?.length ? ` -> ${effect.targets.map((target: any) => target.value).join(', ')}` : ''}
                </li>
              ))}
            </ul>
          ) : null}
          <div className="mt-2 text-xs text-amber-200">
            Risco: {approval?.risk?.level ?? 'desconhecido'}
            {confirmationLevel === 'double' ? ' | dupla confirmacao' : ''}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              disabled={approval?.status && approval.status !== 'pending'}
              onClick={() => postApproval(approvalId, 'approved', confirmationLevel, 'single', approval?.request_id)}
              className="rounded-lg bg-green-600 px-3 py-2 text-sm hover:bg-green-500 disabled:opacity-50"
            >
              {confirmationLevel === 'double' && confirmingApprovalId === approvalId ? 'Confirmar definitivamente' : 'Aprovar esta acao'}
            </button>
            {availableScopes.includes('run_scope') ? (
              <button
                disabled={approval?.status && approval.status !== 'pending'}
                onClick={() => postApproval(approvalId, 'approved', confirmationLevel, 'run_scope', approval?.request_id)}
                className="rounded-lg bg-emerald-700 px-3 py-2 text-sm hover:bg-emerald-600 disabled:opacity-50"
              >
                Aprovar este fluxo
              </button>
            ) : null}
            <button
              disabled={approval?.status && approval.status !== 'pending'}
              onClick={() => postApproval(approvalId, 'rejected', confirmationLevel, 'single', approval?.request_id)}
              className="rounded-lg bg-red-700 px-3 py-2 text-sm hover:bg-red-600 disabled:opacity-50"
            >
              Rejeitar
            </button>
            <div className="self-center text-xs text-amber-200">Status: {approval?.status ?? 'pending'}</div>
          </div>
        </div>
      )
    }

    if (event.type === 'user_input_requested') {
      const handoff = event.payload?.handoff
      return (
        <div key={key} className="rounded-xl border border-violet-800/80 bg-violet-950/30 p-3">
          <div className="text-[11px] font-medium uppercase tracking-wide text-violet-300">Preciso da sua decisao</div>
          <div className="mt-1 font-medium text-violet-100">{handoff?.title}</div>
          <div className="mt-1 text-sm text-violet-50">{handoff?.prompt}</div>
          {handoff?.options?.length ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {handoff.options.map((option: any) => (
                <button
                  key={option.id}
                  onClick={() => answerHandoff({ option_id: option.id, value: option.value, label: option.label })}
                  className="rounded-lg border border-violet-700 bg-violet-900/50 px-3 py-2 text-sm text-violet-100 hover:bg-violet-900"
                >
                  {option.label}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      )
    }

    if (event.type === 'user_input_received') {
      const responseText =
        event.payload?.response?.text ??
        event.payload?.response?.label ??
        JSON.stringify(event.payload?.response ?? {}, null, 2)
      return renderUserBubble(responseText, key)
    }

    if (event.type === 'task_planned') {
      return renderActivityLine(`Planejado: ${event.payload?.task?.title ?? event.payload?.task?.id}`, key)
    }

    if (event.type === 'task_started') {
      return renderActivityLine(`Executando: ${event.payload?.task?.title ?? event.payload?.task?.id}`, key)
    }

    if (event.type === 'task_finished') {
      const actionResult = event.payload?.result?.action_result
      return (
        <div key={key} className="rounded-xl border border-emerald-900/60 bg-emerald-950/20 p-3">
          <div className="text-[11px] font-medium uppercase tracking-wide text-emerald-300">Resultado</div>
          <div className="mt-1 text-sm text-emerald-50">{actionResult?.summary ?? 'Acao concluida.'}</div>
          <div className="mt-1 text-xs text-emerald-200/80">Status: {actionResult?.status ?? event.payload?.status ?? 'completed'}</div>
        </div>
      )
    }

    if (event.type === 'task_retry_scheduled') {
      return renderActivityLine(
        `Tentando outro caminho: ${event.payload?.classification?.reason ?? 'nova tentativa agendada.'}`,
        key,
      )
    }

    if (event.type === 'run_failed' || event.type === 'run_finished' || event.type === 'run_cancelled') {
      const summary =
        event.payload?.user_message ?? event.payload?.reflection?.summary ?? (event.type === 'run_cancelled' ? 'Execucao cancelada.' : 'Execucao finalizada.')
      return renderAgentBubble(summary, key)
    }

    if (event.type === 'run_paused') {
      const awaitingApproval = event.payload?.status === 'awaiting_approval'
      return renderAgentBubble(
        awaitingApproval
          ? 'A execucao ficou pausada aguardando uma aprovacao.'
          : 'A execucao ficou pausada aguardando sua resposta.',
        key,
        'muted',
      )
    }

    if (event.type === 'approval_resolved') {
      return renderActivityLine(
        `Aprovacao ${event.payload?.status === 'approved' ? 'aprovada' : 'rejeitada'}.`,
        key,
      )
    }

    if (event.type === 'approval_grant_created') {
      return renderActivityLine('Fluxo aprovado: proximas acoes compativeis podem continuar sem nova aprovacao.', key)
    }

    if (event.type === 'approval_grant_revoked') {
      return renderActivityLine('Grant de fluxo revogado. Novas acoes sensiveis voltarao a pedir aprovacao.', key)
    }

    return null
  }

  return (
    <div
      className={`flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-gray-700/90 bg-gray-800/95 ${
        showAdvanced ? 'p-4' : 'p-5 shadow-2xl shadow-black/25'
      }`}
    >
      {showAdvanced ? (
        <div className="mb-3 grid shrink-0 gap-3 md:grid-cols-3">
          <label className="text-sm text-gray-300">
            Runtime
            <select
              value={selectedRuntimeMode}
              onChange={event => selectRuntimeMode(event.target.value as 'manual_assist' | 'agentic')}
              className="mt-1 w-full rounded-lg border border-gray-700 bg-gray-900 p-2"
            >
              <option value="agentic">Agent mode</option>
              <option value="manual_assist">Manual assist mode</option>
            </select>
          </label>
          <label className="text-sm text-gray-300">
            Provider
            <select
              value={selectedProvider ?? ''}
              onChange={event => selectProvider(event.target.value || null)}
              disabled={selectedRuntimeMode !== 'agentic'}
              className="mt-1 w-full rounded-lg border border-gray-700 bg-gray-900 p-2 disabled:opacity-60"
            >
              <option value="">Selecione</option>
              {configuredProviders.map(provider => (
                <option key={provider.provider} value={provider.provider}>
                  {provider.display_name}{provider.provider === 'anthropic' ? ' (recomendado)' : ''}
                </option>
              ))}
            </select>
            {selectedProvider && selectedProvider !== 'anthropic' && (
              <p className="mt-1 text-[11px] text-amber-400/70">
                Provider nao validado extensivamente. Para melhor estabilidade, use Anthropic.
              </p>
            )}
          </label>
          <label className="text-sm text-gray-300">
            Model
            <input
              value={selectedModel}
              onChange={event => selectModel(event.target.value)}
              list="chat-models"
              disabled={selectedRuntimeMode !== 'agentic'}
              className="mt-1 w-full rounded-lg border border-gray-700 bg-gray-900 p-2 disabled:opacity-60"
            />
            <datalist id="chat-models">
              {(currentProvider?.models ?? []).map(model => (
                <option key={model} value={model} />
              ))}
            </datalist>
          </label>
        </div>
      ) : (
        <div className="mb-4 shrink-0">
          <div className="text-xs uppercase tracking-[0.2em] text-blue-300">PHYLUM</div>
          <h1 className="mt-2 text-2xl font-semibold text-white">Converse com a IA</h1>
          <p className="mt-2 text-sm text-gray-400">
            Descreva o que voce quer fazer no computador. As configuracoes e os paineis tecnicos ficam em avancado.
          </p>
        </div>
      )}
      {showAdvanced ? (
        <div className="mb-3 shrink-0 rounded-lg border border-gray-700/80 bg-gray-900/50 p-3 text-sm text-gray-300">
          {selectedRuntimeMode === 'agentic'
            ? configuredProviders.length > 0
              ? 'Agent mode ativo. A run usa provider LLM configurado e pode pausar para pedir contexto.'
              : 'Waiting for provider configuration. Se voce enviar agora, o backend cai em manual assist mode.'
            : 'Manual assist mode ativo. O sistema planeja os passos e orienta a execucao sem autonomia plena.'}
        </div>
      ) : null}

      {isActiveRun ? (
        <div className="mb-2 shrink-0 flex items-center gap-2 rounded-lg border border-blue-900/40 bg-blue-950/30 px-3 py-2 text-xs text-blue-100">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-400 opacity-60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-blue-400" />
          </span>
          <span className="font-medium">{statusLabels[currentRun!.status] ?? currentRun!.status}</span>
          {latestActivity ? <span className="truncate text-blue-200/80">— {latestActivity}</span> : null}
        </div>
      ) : null}

      <div className="relative min-h-0 flex-1">
        <div
          ref={containerRef}
          onScroll={handleScroll}
          className="chat-scroll h-full overflow-y-auto scroll-smooth pr-1"
        >
          <div className="flex flex-col gap-3 pb-2">
            {!currentRun && messages.length === 0 ? (
              <div className="rounded-xl border border-dashed border-gray-700/80 bg-gray-900/30 px-4 py-8 text-center text-sm text-gray-400">
                Envie uma instrucao para iniciar. O registro da execucao aparecera aqui em tempo real.
              </div>
            ) : null}
            {currentRun?.inputs?.text ? renderUserBubble(currentRun.inputs.text, 'run-input') : null}
            {currentRun
              ? threadEvents.map((event, index) => renderThreadEvent(event, index))
              : messages.map((message, index) =>
                  message.role === 'user'
                    ? renderUserBubble(message.text, `msg-${index}`)
                    : renderAgentBubble(message.text, `msg-${index}`),
                )}
            <div ref={endRef} className="h-px shrink-0" aria-hidden />
          </div>
        </div>
        <ScrollToBottomButton visible={showJumpButton} onClick={() => scrollToBottom('smooth')} />
      </div>

      <div className="mt-3 flex shrink-0 items-end gap-2 border-t border-gray-700/60 pt-3">
        <textarea
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={handleComposerKeyDown}
          rows={2}
          placeholder={
            currentRun?.pending_handoff?.allow_free_text
              ? 'Responda ao handoff atual para a run continuar'
              : 'Descreva a tarefa… (Enter envia, Shift+Enter nova linha)'
          }
          className="max-h-32 min-h-[2.75rem] flex-1 resize-y rounded-xl border border-gray-700 bg-gray-900 px-3 py-2 text-sm leading-relaxed focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500/40"
        />
        <button
          onClick={() => void submit()}
          disabled={submitting || !text.trim()}
          className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? 'Enviando…' : currentRun?.pending_handoff?.allow_free_text ? 'Responder' : 'Executar'}
        </button>
      </div>
    </div>
  )
}
export default ChatPanel
