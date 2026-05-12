import React, { useEffect, useState } from 'react'
import { getApiBase } from '../lib/runtimeConfig'
import { ProviderSetting, useStore } from '../state/store'

type Props = {
  provider: ProviderSetting
}

const API_BASE = getApiBase()

const ProviderCredentialsForm: React.FC<Props> = ({ provider }) => {
  const upsertProviderSetting = useStore(state => state.upsertProviderSetting)
  const [apiKey, setApiKey] = useState('')
  const [defaultModel, setDefaultModel] = useState(provider.default_model ?? '')
  const [baseUrl, setBaseUrl] = useState(provider.base_url ?? '')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)

  useEffect(() => {
    setDefaultModel(provider.default_model ?? '')
    setBaseUrl(provider.base_url ?? '')
  }, [provider.default_model, provider.base_url])

  const blockCopy = (event: React.ClipboardEvent<HTMLInputElement>) => {
    event.preventDefault()
  }

  const save = async () => {
    if (!apiKey.trim()) {
      setError('Informe uma API key para salvar.')
      return
    }
    setSaving(true)
    setError(null)
    setStatus(null)
    try {
      const response = await fetch(`${API_BASE}/settings/providers/${encodeURIComponent(provider.provider)}/credential`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          api_key: apiKey,
          default_model: defaultModel || null,
          base_url: provider.requires_base_url ? baseUrl || null : null,
        }),
      })
      if (!response.ok) {
        throw new Error(await response.text())
      }
      const data = await response.json()
      upsertProviderSetting(data.provider)
      setApiKey('')
      setStatus('Credencial salva com sucesso.')
    } catch (err) {
      console.error(err)
      setError('Nao foi possivel salvar a credencial.')
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    setSaving(true)
    setError(null)
    setStatus(null)
    try {
      const response = await fetch(`${API_BASE}/settings/providers/${encodeURIComponent(provider.provider)}/credential`, {
        method: 'DELETE',
      })
      if (!response.ok) {
        throw new Error(await response.text())
      }
      const data = await response.json()
      upsertProviderSetting(data.provider)
      setApiKey('')
      setStatus('Credencial removida.')
    } catch (err) {
      console.error(err)
      setError('Nao foi possivel remover a credencial.')
    } finally {
      setSaving(false)
    }
  }

  const testConnection = async () => {
    setTesting(true)
    setError(null)
    setStatus(null)
    try {
      const response = await fetch(`${API_BASE}/settings/providers/${encodeURIComponent(provider.provider)}/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: defaultModel || null }),
      })
      if (!response.ok) {
        throw new Error(await response.text())
      }
      setStatus('Conexao validada com sucesso.')
    } catch (err) {
      console.error(err)
      setError('Falha ao testar a conexao com o provedor.')
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="rounded border border-gray-700 bg-gray-800 p-4 space-y-3">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-lg font-semibold">{provider.display_name}</h3>
          <p className="text-xs text-gray-400">
            {provider.configured ? `Configurado (termina em ${provider.last4 ?? '----'})` : 'Sem credencial salva'}
          </p>
        </div>
        <span className={`rounded px-2 py-1 text-xs ${provider.configured ? 'bg-green-900 text-green-200' : 'bg-gray-700 text-gray-300'}`}>
          {provider.configured ? 'Configured' : 'Not configured'}
        </span>
      </div>

      <div className="space-y-2">
        <label className="block text-sm text-gray-300">
          API key
          <input
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={event => setApiKey(event.target.value)}
            onCopy={blockCopy}
            onCut={blockCopy}
            onContextMenu={event => event.preventDefault()}
            placeholder={provider.configured ? 'Stored securely. Enter a new value to rotate.' : 'sk-...'}
            className="mt-1 w-full rounded border border-gray-700 bg-gray-900 p-2"
          />
        </label>

        <label className="block text-sm text-gray-300">
          Default model
          <input
            value={defaultModel}
            onChange={event => setDefaultModel(event.target.value)}
            list={`${provider.provider}-models`}
            className="mt-1 w-full rounded border border-gray-700 bg-gray-900 p-2"
          />
          <datalist id={`${provider.provider}-models`}>
            {provider.models.map(model => (
              <option key={model} value={model} />
            ))}
          </datalist>
        </label>

        {provider.requires_base_url ? (
          <label className="block text-sm text-gray-300">
            Base URL
            <input
              value={baseUrl}
              onChange={event => setBaseUrl(event.target.value)}
              placeholder="https://your-provider.example/v1"
              className="mt-1 w-full rounded border border-gray-700 bg-gray-900 p-2"
            />
          </label>
        ) : null}
      </div>

      {error ? <div className="rounded bg-red-950/50 p-2 text-sm text-red-200">{error}</div> : null}
      {status ? <div className="rounded bg-gray-900 p-2 text-sm text-gray-200">{status}</div> : null}

      <div className="flex flex-wrap gap-2">
        <button onClick={save} disabled={saving} className="rounded bg-blue-600 px-3 py-2 text-sm disabled:opacity-60">
          {saving ? 'Saving...' : 'Save credential'}
        </button>
        <button
          onClick={testConnection}
          disabled={testing || !provider.configured}
          className="rounded bg-gray-700 px-3 py-2 text-sm disabled:opacity-60"
        >
          {testing ? 'Testing...' : 'Test connection'}
        </button>
        <button
          onClick={remove}
          disabled={saving || !provider.configured}
          className="rounded bg-red-700 px-3 py-2 text-sm disabled:opacity-60"
        >
          Remove
        </button>
      </div>
    </div>
  )
}

export default ProviderCredentialsForm
