import React, { useEffect } from 'react'
import Layout from './components/Layout'
import AgentPanel from './components/AgentPanel'
import ChatPanel from './components/ChatPanel'
import SettingsPanel from './components/SettingsPanel'
import TasksPanel from './components/TasksPanel'
import useSocket from './hooks/useSocket'
import { useStore } from './state/store'

export default function App() {
  useSocket()
  const API_BASE = (import.meta as any).env?.VITE_API_URL || 'http://127.0.0.1:8000'
  const connected = useStore(state => state.connected)
  const activeView = useStore(state => state.activeView)
  const currentRunId = useStore(state => state.currentRunId)
  const currentRun = useStore(state => (state.currentRunId ? state.runs[state.currentRunId] : null))
  const setProviderSettings = useStore(state => state.setProviderSettings)
  const setSupportedTools = useStore(state => state.setSupportedTools)
  const hydrateRun = useStore(state => state.hydrateRun)

  useEffect(() => {
    const loadBootstrap = async () => {
      try {
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
        const latestRun = (runsData.runs ?? [])[0]
        if (latestRun) {
          hydrateRun(latestRun)
        }
      } catch (error) {
        console.error(error)
      }
    }
    void loadBootstrap()
  }, [API_BASE, hydrateRun, setProviderSettings, setSupportedTools])

  return (
    <div className="min-h-screen">
      <Layout>
        <div className="p-4 space-y-4">
          {activeView === 'settings' ? (
            <SettingsPanel />
          ) : (
            <>
              <div className="rounded border border-gray-700 bg-gray-800 p-4">
                <h1 className="text-2xl font-semibold mb-2">Agente Desktop</h1>
                <p className="text-sm text-gray-300">Socket: {connected ? 'Connected' : 'Disconnected'}</p>
                <p className="text-sm text-gray-400">Run atual: {currentRunId ?? 'nenhuma execucao iniciada'}</p>
                <p className="text-sm text-gray-400">Status: {currentRun?.status ?? 'idle'}</p>
              </div>
              <div className="grid gap-4 xl:grid-cols-[1.3fr,1fr]">
                <div className="space-y-4">
                  <ChatPanel />
                  <TasksPanel />
                </div>
                <AgentPanel />
              </div>
            </>
          )}
        </div>
      </Layout>
    </div>
  )
}
