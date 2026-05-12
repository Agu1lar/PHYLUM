import React, { useState } from 'react'
import { useStore } from '../state/store'

const ChatPanel: React.FC = () => {
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
  const API_BASE = (import.meta as any).env?.VITE_API_URL || 'http://127.0.0.1:8000'
  const configuredProviders = providers.filter(provider => provider.configured)
  const currentProvider = providers.find(provider => provider.provider === selectedProvider) ?? null

  const submit = async () => {
    const trimmed = text.trim()
    if (!trimmed || submitting) return
    addMessage({ role: 'user', text: trimmed })
    setSubmitting(true)
    try {
      let response: Response
      if (currentRun?.pending_handoff) {
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

  return (
    <div className="rounded border border-gray-700 bg-gray-800 p-4 flex flex-col min-h-[360px]">
      <div className="grid gap-3 md:grid-cols-3 mb-3">
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
                {provider.display_name}
              </option>
            ))}
          </select>
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
      <div className="mb-3 rounded border border-gray-700 bg-gray-900/50 p-3 text-sm text-gray-300">
        {selectedRuntimeMode === 'agentic'
          ? configuredProviders.length > 0
            ? 'Agent mode ativo. A run usa provider LLM configurado e pode pausar para pedir contexto.'
            : 'Waiting for provider configuration. Se voce enviar agora, o backend cai em manual assist mode.'
          : 'Manual assist mode ativo. O sistema planeja os passos e orienta a execucao sem autonomia plena.'}
      </div>
      <div className="flex-1 overflow-auto space-y-2">
        {messages.map((message, index) => (
          <div key={index} className={message.role === 'agent' ? 'text-left' : 'text-right'}>
            <div className="text-xs text-gray-500 mb-1">{message.role}</div>
            <div className="inline-block bg-gray-700 p-2 rounded">{message.text}</div>
          </div>
        ))}
      </div>
      <div className="mt-3 flex gap-2">
        <input
          value={text}
          onChange={e => setText(e.target.value)}
          placeholder={
            currentRun?.pending_handoff
              ? 'Responda ao handoff atual para a run continuar'
              : 'Ex.: search web driver hp, find executable chrome, list devices ou write hello to C:\\Temp\\agente.txt'
          }
          className="flex-1 p-2 rounded bg-gray-900 border border-gray-700"
        />
        <button onClick={submit} disabled={submitting} className="px-4 py-2 bg-blue-600 rounded disabled:opacity-60">
          {submitting ? 'Sending...' : currentRun?.pending_handoff ? 'Reply' : 'Run'}
        </button>
      </div>
    </div>
  )
}
export default ChatPanel
