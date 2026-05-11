import React from 'react'

const Sidebar: React.FC = () => {
  return (
    <nav className="w-64 bg-gray-800 border-r border-gray-700 p-4 flex flex-col">
      <div className="mb-6">
        <div className="text-xl font-bold">Agente</div>
        <div className="text-xs text-gray-400">Desktop Control</div>
      </div>
      <ul className="space-y-2 flex-1">
        <li className="p-2 rounded hover:bg-gray-700 cursor-pointer">Chat</li>
        <li className="p-2 rounded hover:bg-gray-700 cursor-pointer">Agent</li>
        <li className="p-2 rounded hover:bg-gray-700 cursor-pointer">Logs</li>
        <li className="p-2 rounded hover:bg-gray-700 cursor-pointer">Approvals</li>
        <li className="p-2 rounded hover:bg-gray-700 cursor-pointer">History</li>
        <li className="p-2 rounded hover:bg-gray-700 cursor-pointer">Tools</li>
      </ul>
      <div className="text-xs text-gray-500">Dark theme</div>
    </nav>
  )
}

export default Sidebar
