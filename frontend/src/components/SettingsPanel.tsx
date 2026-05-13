import React, { useEffect, useState } from 'react'
import ProviderCredentialsForm from './ProviderCredentialsForm'
import {
  getApiBase,
  getRuntimeConnectionConfig,
  isDesktopApp,
  resetRuntimeConnectionConfig,
  setRuntimeConnectionConfig,
  subscribeRuntimeConnectionConfig,
} from '../lib/runtimeConfig'
import { useStore } from '../state/store'

const SettingsPanel: React.FC = () => {
  const providers = useStore(state => state.providers)
  const [doctor, setDoctor] = useState<any | null>(null)
  const [onboarding, setOnboarding] = useState<any | null>(null)
  const [network, setNetwork] = useState<any | null>(null)
  const [connectionConfig, setConnectionConfigState] = useState(() => getRuntimeConnectionConfig())
  const [saveMessage, setSaveMessage] = useState<string | null>(null)
  const [connectionTest, setConnectionTest] = useState<string | null>(null)
  const API_BASE = getApiBase()

  useEffect(() => {
    return subscribeRuntimeConnectionConfig(() => {
      setConnectionConfigState(getRuntimeConnectionConfig())
    })
  }, [])

  useEffect(() => {
    const loadDiagnostics = async () => {
      try {
        const [doctorResponse, onboardingResponse, networkResponse] = await Promise.all([
          fetch(`${API_BASE}/diagnostics/doctor`),
          fetch(`${API_BASE}/onboarding/capabilities`),
          fetch(`${API_BASE}/diagnostics/network`),
        ])
        if (doctorResponse.ok) {
          setDoctor(await doctorResponse.json())
        }
        if (onboardingResponse.ok) {
          setOnboarding(await onboardingResponse.json())
        }
        if (networkResponse.ok) {
          const payload = await networkResponse.json()
          setNetwork(payload.network)
        }
      } catch (error) {
        console.error(error)
      }
    }
    void loadDiagnostics()
  }, [API_BASE])

  async function testConnection(targetApiBase: string) {
    setConnectionTest('Testando conexao...')
    try {
      const normalized = /^https?:\/\//i.test(targetApiBase) ? targetApiBase : `http://${targetApiBase}`
      const response = await fetch(`${normalized.replace(/\/$/, '')}/health`)
      if (!response.ok) {
        throw new Error(`health failed: ${response.status}`)
      }
      setConnectionTest('Conexao OK com o backend informado.')
    } catch (error) {
      console.error(error)
      setConnectionTest('Nao consegui acessar o backend informado.')
    }
  }

  function saveConnectionConfig() {
    setRuntimeConnectionConfig(connectionConfig)
    setSaveMessage('Configuracao salva. O app vai passar a usar esse endpoint.')
    setConnectionTest(null)
  }

  function resetConnectionConfig() {
    resetRuntimeConnectionConfig()
    setConnectionConfigState(getRuntimeConnectionConfig())
    setSaveMessage('Configuracao restaurada para o padrao local.')
    setConnectionTest(null)
  }

  return (
    <div className="space-y-4">
      <div className="rounded border border-gray-700 bg-gray-800 p-4">
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="mt-2 text-sm text-gray-400">
          As credenciais sao digitadas nesta UI, enviadas ao backend local e armazenadas com protecao do sistema.
          Depois de salvar, a chave nao retorna ao frontend.
        </p>
      </div>
      <div className="rounded border border-gray-700 bg-gray-800 p-4">
        <h2 className="text-lg font-semibold text-white">Conexao com backend</h2>
        <p className="mt-2 text-sm text-gray-400">
          Configure o backend que este cliente deve usar. Para testar em outra maquina da rede, informe a URL do PC host
          no formato <code>http://IP-DO-HOST:8000</code>.
        </p>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <label className="text-sm text-gray-300">
            URL do backend
            <input
              value={connectionConfig.apiBase}
              onChange={event =>
                setConnectionConfigState(current => ({
                  ...current,
                  apiBase: event.target.value,
                }))
              }
              placeholder="http://192.168.0.10:8000"
              className="mt-1 w-full rounded border border-gray-700 bg-gray-900 p-2"
            />
          </label>
          <label className="text-sm text-gray-300">
            Modo do desktop
            <select
              value={connectionConfig.desktopMode ?? 'local-sidecar'}
              disabled={!isDesktopApp()}
              onChange={event =>
                setConnectionConfigState(current => ({
                  ...current,
                  desktopMode: event.target.value as 'local-sidecar' | 'remote-backend',
                }))
              }
              className="mt-1 w-full rounded border border-gray-700 bg-gray-900 p-2 disabled:opacity-60"
            >
              <option value="local-sidecar">Local sidecar</option>
              <option value="remote-backend">Remote backend</option>
            </select>
          </label>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <button onClick={saveConnectionConfig} className="rounded bg-blue-600 px-3 py-2 text-sm text-white">
            Salvar endpoint
          </button>
          <button
            onClick={() => testConnection(connectionConfig.apiBase)}
            className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200"
          >
            Testar conexao
          </button>
          <button
            onClick={resetConnectionConfig}
            className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200"
          >
            Restaurar padrao local
          </button>
        </div>
        {saveMessage ? <div className="mt-3 text-sm text-emerald-300">{saveMessage}</div> : null}
        {connectionTest ? <div className="mt-2 text-sm text-gray-300">{connectionTest}</div> : null}
        {!isDesktopApp() ? (
          <div className="mt-3 rounded border border-gray-700 bg-gray-900/40 p-3 text-sm text-gray-300">
            No navegador, o modo relevante eh apenas a URL do backend. O modo `local-sidecar` vale so para o exe desktop.
          </div>
        ) : (
          <div className="mt-3 rounded border border-gray-700 bg-gray-900/40 p-3 text-sm text-gray-300">
            Se este PC for o host da rede, use `local-sidecar` e coloque a URL com o IP deste computador. O app vai subir o
            backend local e expor acesso na LAN.
          </div>
        )}
      </div>
      {doctor ? (
        <div className="rounded border border-gray-700 bg-gray-800 p-4">
          <h2 className="text-lg font-semibold text-white">Doctor</h2>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <div className="rounded border border-gray-700 bg-gray-900/40 p-3 text-sm text-gray-300">
              <div>Python: {doctor.capabilities?.python}</div>
              <div>Plataforma: {doctor.capabilities?.platform}</div>
              <div>Admin: {doctor.capabilities?.is_admin ? 'sim' : 'nao'}</div>
              <div>Provedores configurados: {doctor.capabilities?.providers_configured ?? 0}</div>
            </div>
            <div className="rounded border border-gray-700 bg-gray-900/40 p-3 text-sm text-gray-300">
              <div>Playwright: {doctor.capabilities?.dependencies?.playwright ? 'ok' : 'faltando'}</div>
              <div>UIA (`pywinauto`): {doctor.capabilities?.dependencies?.pywinauto ? 'ok' : 'faltando'}</div>
              <div>Office COM: {doctor.capabilities?.dependencies?.win32com ? 'ok' : 'faltando'}</div>
              <div>PDF parser: {doctor.capabilities?.dependencies?.pypdf ? 'ok' : 'faltando'}</div>
            </div>
          </div>
          {doctor.warnings?.length ? (
            <ul className="mt-3 space-y-2 text-sm text-amber-200">
              {doctor.warnings.map((warning: string) => (
                <li key={warning} className="rounded border border-amber-800 bg-amber-950/30 p-2">
                  {warning}
                </li>
              ))}
            </ul>
          ) : (
            <div className="mt-3 rounded border border-emerald-800 bg-emerald-950/20 p-3 text-sm text-emerald-200">
              Ambiente pronto para operacao agentic mais robusta.
            </div>
          )}
        </div>
      ) : null}
      {network ? (
        <div className="rounded border border-gray-700 bg-gray-800 p-4">
          <h2 className="text-lg font-semibold text-white">Diagnostico de rede</h2>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <div className="rounded border border-gray-700 bg-gray-900/40 p-3 text-sm text-gray-300">
              <div>Bind host: {network.bind_host}</div>
              <div>Porta: {network.port}</div>
              <div>LAN habilitada: {network.lan_enabled ? 'sim' : 'nao'}</div>
              <div>Base publica: {network.public_base_url}</div>
            </div>
            <div className="rounded border border-gray-700 bg-gray-900/40 p-3 text-sm text-gray-300">
              <div>IPs locais: {(network.local_ipv4 ?? []).join(', ') || 'nenhum detectado'}</div>
              <div>WS: {network.ws_url}</div>
            </div>
          </div>
          {network.suggested_urls?.length ? (
            <div className="mt-3">
              <div className="text-xs uppercase tracking-wide text-gray-500">URLs sugeridas</div>
              <ul className="mt-2 space-y-1 text-sm text-gray-300">
                {network.suggested_urls.map((item: string) => (
                  <li key={item} className="rounded border border-gray-700 bg-gray-900/40 p-2">
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
      {onboarding?.steps?.length ? (
        <div className="rounded border border-gray-700 bg-gray-800 p-4">
          <h2 className="text-lg font-semibold text-white">Onboarding de capacidades</h2>
          <div className="mt-3 grid gap-2">
            {onboarding.steps.map((step: any) => (
              <div
                key={step.id}
                className={`rounded border p-3 text-sm ${step.ready ? 'border-emerald-700 bg-emerald-950/20 text-emerald-200' : 'border-gray-700 bg-gray-900/40 text-gray-300'}`}
              >
                {step.ready ? 'Pronto' : 'Pendente'}: {step.label}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {providers.map(provider => (
        <ProviderCredentialsForm key={provider.provider} provider={provider} />
      ))}
    </div>
  )
}

export default SettingsPanel
