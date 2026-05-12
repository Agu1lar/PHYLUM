import React from 'react'
import LogsPanel from './LogsPanel'
import RecoveryPanel from './RecoveryPanel'
import TimelinePanel from './TimelinePanel'
import { useStore } from '../state/store'

const Layout: React.FC<{ children?: React.ReactNode }> = ({ children }) => {
  const activeView = useStore(state => state.activeView)
  const showAdvanced = useStore(state => state.showAdvanced)
  return (
    <div className="flex h-screen">
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-gray-900 text-gray-100">{children}</main>
      {activeView === 'dashboard' && showAdvanced ? (
        <aside className="w-96 overflow-auto border-l border-gray-700 bg-gray-800 p-3">
          <div className="space-y-4">
            <RecoveryPanel />
            <TimelinePanel />
            <LogsPanel />
          </div>
        </aside>
      ) : null}
    </div>
  )
}

export default Layout
