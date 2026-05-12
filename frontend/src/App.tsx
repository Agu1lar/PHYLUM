import React, { useEffect, useMemo, useState } from 'react'
import Layout from './components/Layout'
import AgentPanel from './components/AgentPanel'
import ChatPanel from './components/ChatPanel'
import ChatHistoryPanel from './components/ChatHistoryPanel'
import SettingsPanel from './components/SettingsPanel'
import TasksPanel from './components/TasksPanel'
import useSocket from './hooks/useSocket'
import { ensureDesktopBackend } from './lib/desktopHost'
import { getApiBase, isDesktopApp } from './lib/runtimeConfig'
import { useStore } from './state/store'

export default function App() {
  useSocket()
  const API_BASE = getApiBase()
  const connected = useStore(state => state.connected)
  const activeView = useStore(state => state.activeView)
  const showAdvanced = useStore(state => state.showAdvanced)
  const currentRunId = useStore(state => state.currentRunId)
  const currentRun = useStore(state => (state.currentRunId ? state.runs[state.currentRunId] : null))
  const runs = useStore(state => state.runs)
  const setProviderSettings = useStore(state => state.setProviderSettings)
  const setSupportedTools = useStore(state => state.setSupportedTools)
  const setRunsFromList = useStore(state => state.setRunsFromList)
  const setActiveView = useStore(state => state.setActiveView)
  const setShowAdvanced = useStore(state => state.setShowAdvanced)
  const setCurrentRun = useStore(state => state.setCurrentRun)
  const startNewChat = useStore(state => state.startNewChat)
  const removeRun = useStore(state => state.removeRun)
  const [installerAvailable, setInstallerAvailable] = useState(false)
  const [checkingInstaller, setCheckingInstaller] = useState(true)

  useEffect(() => {
    const loadBootstrap = async () => {
      try {
        await ensureDesktopBackend()
        const [providerResponse, toolsResponse, runsResponse] = await Promise.all([
          fetch(`${API_BASE}/settings/providers`),
          fetch(`${API_BASE}/tools`),
          fetch(`${API_BASE}/runs`),
        ])
        if (!providerResponse.ok) {
          throw new Error(`settings request failed: ${providerResponse.status}`)
        }
        if (!toolsResponse.ok) {
          throw new Error(`tools request failed: ${toolsResponse.status}`)
        }
        if (!runsResponse.ok) {
          throw new Error(`runs request failed: ${runsResponse.status}`)
        }
        const providerData = await providerResponse.json()
        const toolsData = await toolsResponse.json()
        const runsData = await runsResponse.json()
        setProviderSettings(providerData.providers ?? [])
        setSupportedTools(toolsData.tools ?? [])
        setRunsFromList(runsData.runs ?? [])
      } catch (error) {
        console.error(error)
      }
    }
    void loadBootstrap()
  }, [API_BASE, setProviderSettings, setRunsFromList, setSupportedTools])

  useEffect(() => {
    const loadInstallerState = async () => {
      setCheckingInstaller(true)
      try {
        const response = await fetch(`${API_BASE}/downloads/windows-installer/meta`)
        if (!response.ok) {
          setInstallerAvailable(false)
          return
        }
        const data = await response.json()
        setInstallerAvailable(Boolean(data.available))
      } catch (error) {
        setInstallerAvailable(false)
      } finally {
        setCheckingInstaller(false)
      }
    }
    void loadInstallerState()
  }, [API_BASE])

  const statusPill = useMemo(() => {
    if (!connected) return 'Desconectado'
    if (!currentRun?.status) return 'Pronto'
    return currentRun.status
  }, [connected, currentRun?.status])

  function openDashboard() {
    setActiveView('dashboard')
  }

  function openSettings() {
    setActiveView('settings')
  }

  function handleNewChat() {
    startNewChat()
    setActiveView('dashboard')
  }

  async function handleDeleteRun(requestId: string) {
    try {
      const response = await fetch(`${API_BASE}/run/${encodeURIComponent(requestId)}`, { method: 'DELETE' })
      if (!response.ok) {
        throw new Error(`delete run failed: ${response.status}`)
      }
      removeRun(requestId)
    } catch (error) {
      console.error(error)
    }
  }

  function downloadInstaller() {
    window.open(`${API_BASE}/downloads/windows-installer`, '_blank')
  }

  return (
    <div className="h-screen">
      <Layout>
        <div className="flex h-full flex-col overflow-hidden p-4">
          <div className="mb-4 flex shrink-0 items-center justify-between gap-4">
            <div>
              <div className="text-xs uppercase tracking-[0.2em] text-blue-300">Agente Desktop</div>
              <div className="mt-1 text-sm text-gray-400">
                {activeView === 'settings' ? 'Configuracoes do agente e dos provedores.' : 'Assistente local focado em automacao por linguagem natural.'}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full border border-gray-700 bg-gray-800 px-3 py-1 text-xs text-gray-300">{statusPill}</span>
              {activeView === 'dashboard' ? (
                <button
                  onClick={() => setShowAdvanced(!showAdvanced)}
                  className="rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200"
                >
                  {showAdvanced ? 'Ocultar avancado' : 'Mostrar avancado'}
                </button>
              ) : (
                <button
                  onClick={openDashboard}
                  className="rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200"
                >
                  Voltar ao chat
                </button>
              )}
              <button
                onClick={openSettings}
                className="rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200"
              >
                Configuracoes
              </button>
              {!isDesktopApp() ? (
                <button
                  onClick={downloadInstaller}
                  disabled={!installerAvailable || checkingInstaller}
                  className="rounded bg-blue-600 px-3 py-2 text-sm text-white disabled:opacity-50"
                >
                  {checkingInstaller ? 'Verificando app...' : installerAvailable ? 'Baixar app' : 'Instalador indisponivel'}
                </button>
              ) : null}
            </div>
          </div>
          {activeView === 'settings' ? (
            <div className="min-h-0 overflow-auto">
              <div className="mx-auto max-w-5xl">
                <SettingsPanel />
              </div>
            </div>
          ) : (
            <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[300px,minmax(0,1fr)]">
              <div className="min-h-0">
                <ChatHistoryPanel
                  runs={Object.values(runs)}
                  currentRunId={currentRunId}
                  onSelectRun={setCurrentRun}
                  onNewChat={handleNewChat}
                  onDeleteRun={handleDeleteRun}
                />
              </div>
              {showAdvanced ? (
                <div className="grid min-h-0 gap-4 xl:grid-cols-[minmax(0,1.35fr),minmax(320px,0.95fr)]">
                  <div className="flex min-h-0 flex-col gap-4">
                    <div className="shrink-0 rounded border border-gray-700 bg-gray-800 p-4">
                      <h1 className="text-xl font-semibold text-white">Painel de execucao</h1>
                      <p className="mt-2 text-sm text-gray-300">Socket: {connected ? 'Connected' : 'Disconnected'}</p>
                      <p className="text-sm text-gray-400">Run atual: {currentRunId ?? 'nenhuma execucao iniciada'}</p>
                      <p className="text-sm text-gray-400">Status: {currentRun?.status ?? 'idle'}</p>
                    </div>
                    <div className="min-h-0 flex-1">
                      <ChatPanel showAdvanced />
                    </div>
                    <TasksPanel />
                  </div>
                  <div className="min-h-0">
                    <AgentPanel />
                  </div>
                </div>
              ) : (
                <div className="flex min-h-0 items-center justify-center">
                  <div className="h-full min-h-0 w-full max-w-5xl">
                    <ChatPanel showAdvanced={false} />
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </Layout>
    </div>
  )
}
