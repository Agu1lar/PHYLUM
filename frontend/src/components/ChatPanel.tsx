import React, { useEffect, useMemo, useRef, useState } from 'react'
import { getApiBase } from '../lib/runtimeConfig'
import { useStore } from '../state/store'

const threadEventTypes = new Set([
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
  const scrollContainerRef = useRef<HTMLDivElement | null>(null)
  const threadEndRef = useRef<HTMLDivElement | null>(null)
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

  useEffect(() => {
    const viewport = scrollContainerRef.current
    if (!viewport) return
    viewport.scrollTop = viewport.scrollHeight
  }, [threadEvents.length, currentRun?.status, messages.length])

  const submit = async () => {
    const trimmed = text.trim()
    if (!trimmed || submitting) return
    setSubmitting(true)
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

  function renderThreadEvent(event: { type: string; payload: any }, index: number) {
    if (event.type === 'tool_call_proposed') {
      const toolName = event.payload?.tool_name ?? event.payload?.tool ?? 'tool'
      const args = JSON.stringify(event.payload?.preview ?? event.payload?.arguments ?? event.payload?.params ?? {}, null, 2)
      return (
        <div key={`${event.type}-${index}`} className="rounded border border-blue-900/60 bg-blue-950/30 p-3">
          <div className="text-xs uppercase tracking-wide text-blue-300">Proposta de acao</div>
          <div className="mt-1 font-medium text-blue-100">{toolName}</div>
          <pre className="mt-2 overflow-auto whitespace-pre-wrap text-xs text-blue-50">{args}</pre>
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
        <div key={`${event.type}-${index}`} className="rounded border border-amber-800 bg-amber-950/30 p-3">
          <div className="text-xs uppercase tracking-wide text-amber-300">Aprovacao necessaria</div>
          <div className="mt-1 font-medium text-amber-100">{approval?.title}</div>
          <div className="mt-1 text-sm text-amber-50">{approval?.reason}</div>
          {commandExplanation ? (
            <div className="mt-2 rounded border border-amber-700/60 bg-amber-900/20 p-2 text-sm text-amber-50">
              {commandExplanation}
            </div>
          ) : null}
          {command ? (
            <div className="mt-2">
              <div className="text-xs uppercase tracking-wide text-amber-300">Comando</div>
              <pre className="mt-1 overflow-auto whitespace-pre-wrap rounded bg-black/20 p-2 text-xs text-amber-50">
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
              className="rounded bg-green-600 px-3 py-2 text-sm disabled:opacity-50"
            >
              {confirmationLevel === 'double' && confirmingApprovalId === approvalId ? 'Confirmar definitivamente' : 'Aprovar esta acao'}
            </button>
            {availableScopes.includes('run_scope') ? (
              <button
                disabled={approval?.status && approval.status !== 'pending'}
                onClick={() => postApproval(approvalId, 'approved', confirmationLevel, 'run_scope', approval?.request_id)}
                className="rounded bg-emerald-700 px-3 py-2 text-sm disabled:opacity-50"
              >
                Aprovar este fluxo
              </button>
            ) : null}
            <button
              disabled={approval?.status && approval.status !== 'pending'}
              onClick={() => postApproval(approvalId, 'rejected', confirmationLevel, 'single', approval?.request_id)}
              className="rounded bg-red-700 px-3 py-2 text-sm disabled:opacity-50"
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
        <div key={`${event.type}-${index}`} className="rounded border border-violet-800 bg-violet-950/30 p-3">
          <div className="text-xs uppercase tracking-wide text-violet-300">Preciso da sua decisao</div>
          <div className="mt-1 font-medium text-violet-100">{handoff?.title}</div>
          <div className="mt-1 text-sm text-violet-50">{handoff?.prompt}</div>
          {handoff?.options?.length ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {handoff.options.map((option: any) => (
                <button
                  key={option.id}
                  onClick={() => answerHandoff({ option_id: option.id, value: option.value, label: option.label })}
                  className="rounded border border-violet-700 bg-violet-900/50 px-3 py-2 text-sm text-violet-100"
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
      return (
        <div key={`${event.type}-${index}`} className="text-right">
          <div className="inline-block rounded bg-gray-700 p-2">{responseText}</div>
        </div>
      )
    }

    if (event.type === 'task_planned') {
      return (
        <div key={`${event.type}-${index}`} className="rounded border border-gray-800 bg-gray-900/40 p-3 text-sm text-gray-300">
          Planejado: {event.payload?.task?.title ?? event.payload?.task?.id}
        </div>
      )
    }

    if (event.type === 'task_started') {
      return (
        <div key={`${event.type}-${index}`} className="rounded border border-sky-900 bg-sky-950/20 p-3 text-sm text-sky-100">
          Executando: {event.payload?.task?.title ?? event.payload?.task?.id}
        </div>
      )
    }

    if (event.type === 'task_finished') {
      const actionResult = event.payload?.result?.action_result
      return (
        <div key={`${event.type}-${index}`} className="rounded border border-emerald-900 bg-emerald-950/20 p-3">
          <div className="text-xs uppercase tracking-wide text-emerald-300">Resultado da acao</div>
          <div className="mt-1 text-sm text-emerald-50">{actionResult?.summary ?? 'Acao concluida.'}</div>
          <div className="mt-1 text-xs text-emerald-200">Status: {actionResult?.status ?? event.payload?.status ?? 'completed'}</div>
        </div>
      )
    }

    if (event.type === 'task_retry_scheduled') {
      return (
        <div key={`${event.type}-${index}`} className="rounded border border-yellow-900 bg-yellow-950/20 p-3 text-sm text-yellow-50">
          Tentando outro caminho: {event.payload?.classification?.reason ?? 'nova tentativa agendada.'}
        </div>
      )
    }

    if (event.type === 'run_failed' || event.type === 'run_finished' || event.type === 'run_cancelled') {
      const summary =
        event.payload?.user_message ?? event.payload?.reflection?.summary ?? (event.type === 'run_cancelled' ? 'Execucao cancelada.' : 'Execucao finalizada.')
      return (
        <div key={`${event.type}-${index}`} className="rounded border border-gray-700 bg-gray-900/70 p-3 text-sm text-gray-100">
          {summary}
        </div>
      )
    }

    if (event.type === 'run_paused') {
      const awaitingApproval = event.payload?.status === 'awaiting_approval'
      return (
        <div key={`${event.type}-${index}`} className="rounded border border-violet-900 bg-violet-950/20 p-3 text-sm text-violet-100">
          {awaitingApproval
            ? 'A execucao ficou pausada aguardando uma aprovacao.'
            : 'A execucao ficou pausada aguardando sua resposta.'}
        </div>
      )
    }

    if (event.type === 'approval_resolved') {
      return (
        <div key={`${event.type}-${index}`} className="rounded border border-gray-800 bg-gray-900/40 p-3 text-sm text-gray-300">
          Aprovacao {event.payload?.status === 'approved' ? 'aprovada' : 'rejeitada'}.
        </div>
      )
    }

    if (event.type === 'approval_grant_created') {
      return (
        <div key={`${event.type}-${index}`} className="rounded border border-emerald-900 bg-emerald-950/20 p-3 text-sm text-emerald-100">
          Fluxo aprovado: as proximas acoes compativeis desta run podem continuar sem nova aprovacao.
        </div>
      )
    }

    if (event.type === 'approval_grant_revoked') {
      return (
        <div key={`${event.type}-${index}`} className="rounded border border-gray-700 bg-gray-900/50 p-3 text-sm text-gray-200">
          O grant de fluxo foi revogado. Novas acoes sensiveis voltarao a pedir aprovacao.
        </div>
      )
    }

    return null
  }

  return (
    <div className={`flex h-full min-h-0 flex-col overflow-hidden rounded border border-gray-700 bg-gray-800 ${showAdvanced ? 'p-4' : 'p-5 shadow-2xl shadow-black/20'}`}>
      {showAdvanced ? (
      <div className="mb-3 grid shrink-0 gap-3 md:grid-cols-3">
        <label className="text-sm text-gray-300">
          Runtime
          <select
            value={selectedRuntimeMode}
            onChange={event => selectRuntimeMode(event.target.value as 'manual_assist' | 'agentic')}
            className="mt-1 w-full rounded border border-gray-700 bg-gray-900 p-2"
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
            className="mt-1 w-full rounded border border-gray-700 bg-gray-900 p-2 disabled:opacity-60"
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
            className="mt-1 w-full rounded border border-gray-700 bg-gray-900 p-2 disabled:opacity-60"
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
      <div className="mb-3 shrink-0 rounded border border-gray-700 bg-gray-900/50 p-3 text-sm text-gray-300">
        {selectedRuntimeMode === 'agentic'
          ? configuredProviders.length > 0
            ? 'Agent mode ativo. A run usa provider LLM configurado e pode pausar para pedir contexto.'
            : 'Waiting for provider configuration. Se voce enviar agora, o backend cai em manual assist mode.'
          : 'Manual assist mode ativo. O sistema planeja os passos e orienta a execucao sem autonomia plena.'}
      </div>
      ) : null}
      <div ref={scrollContainerRef} className="flex-1 min-h-0 overflow-y-auto pr-1">
        <div className="space-y-3">
        {currentRun?.inputs?.text ? (
          <div className="text-right">
            <div className="inline-block rounded bg-gray-700 p-2">{currentRun.inputs.text}</div>
          </div>
        ) : null}
        {currentRun
          ? threadEvents.map((event, index) => renderThreadEvent(event, index))
          : messages.map((message, index) => (
              <div key={index} className={message.role === 'agent' ? 'text-left' : 'text-right'}>
                <div className="text-xs text-gray-500 mb-1">{message.role}</div>
                <div className="inline-block bg-gray-700 p-2 rounded">{message.text}</div>
              </div>
            ))}
          <div ref={threadEndRef} />
        </div>
      </div>
      <div className="mt-3 flex shrink-0 gap-2">
        <input
          value={text}
          onChange={e => setText(e.target.value)}
          placeholder={
            currentRun?.pending_handoff?.allow_free_text
              ? 'Responda ao handoff atual para a run continuar'
              : 'Ex.: search web driver hp, find executable chrome, list devices ou write hello to C:\\Temp\\agente.txt'
          }
          className="flex-1 p-2 rounded bg-gray-900 border border-gray-700"
        />
        <button onClick={submit} disabled={submitting} className="px-4 py-2 bg-blue-600 rounded disabled:opacity-60">
          {submitting ? 'Sending...' : currentRun?.pending_handoff?.allow_free_text ? 'Reply' : 'Run'}
        </button>
      </div>
    </div>
  )
}
export default ChatPanel
