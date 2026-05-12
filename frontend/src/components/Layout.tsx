import React from 'react'
import Sidebar from './Sidebar'
import ApprovalsPanel from './ApprovalsPanel'
import LogsPanel from './LogsPanel'
import HandoffPanel from './HandoffPanel'
import RecoveryPanel from './RecoveryPanel'
import TimelinePanel from './TimelinePanel'
import { useStore } from '../state/store'

const Layout: React.FC<{ children?: React.ReactNode }> = ({ children }) => {
  const activeView = useStore(state => state.activeView)
  return (
    <div className="flex h-screen">
      <Sidebar />
      <main className="flex-1 bg-gray-900 text-gray-100 overflow-auto">{children}</main>
      {activeView === 'dashboard' ? (
        <aside className="w-96 bg-gray-800 border-l border-gray-700 p-3 overflow-auto">
          <div className="space-y-4">
            <ApprovalsPanel />
            <HandoffPanel />
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
