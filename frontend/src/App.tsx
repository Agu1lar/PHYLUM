import React from 'react'
import Layout from './components/Layout'
import { useStore } from './state/store'

export default function App() {
  const connected = useStore(state => state.connected)
  return (
    <div className="min-h-screen">
      <Layout>
        <div className="p-4">
          <h1 className="text-2xl font-semibold mb-2">Agente Desktop</h1>
          <p className="text-sm text-gray-300">Status: {connected ? 'Connected' : 'Disconnected'}</p>
        </div>
      </Layout>
    </div>
  )
}
