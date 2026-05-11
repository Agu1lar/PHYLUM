import React from 'react'
import Sidebar from './Sidebar'

const Layout: React.FC<{children?: React.ReactNode}> = ({children}) => {
  return (
    <div className="flex h-screen">
      <Sidebar />
      <main className="flex-1 bg-gray-900 text-gray-100 overflow-auto">{children}</main>
      <aside className="w-96 bg-gray-800 border-l border-gray-700 p-3 overflow-auto">
        <div className="space-y-4">
          <div className="p-2 bg-gray-900 rounded">Logs</div>
          <div className="p-2 bg-gray-900 rounded">Approvals</div>
        </div>
      </aside>
    </div>
  )
}

export default Layout
