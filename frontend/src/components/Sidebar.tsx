import React from 'react'
import { ViewMode, useStore } from '../state/store'

const Sidebar: React.FC = () => {
  const activeView = useStore(state => state.activeView)
  const setActiveView = useStore(state => state.setActiveView)
  const items: Array<{ id: ViewMode; label: string }> = [
    { id: 'dashboard', label: 'Runs' },
    { id: 'settings', label: 'Settings' },
  ]

  return (
    <nav className="w-64 bg-gray-800 border-r border-gray-700 p-4 flex flex-col">
      <div className="mb-6">
        <div className="text-xl font-bold">Agente</div>
        <div className="text-xs text-gray-400">Desktop Control</div>
      </div>
      <ul className="space-y-2 flex-1">
        {items.map(item => (
          <li
            key={item.id}
            onClick={() => setActiveView(item.id)}
            className={`p-2 rounded cursor-pointer ${
              activeView === item.id ? 'bg-gray-700 text-white' : 'hover:bg-gray-700 text-gray-300'
            }`}
          >
            {item.label}
          </li>
        ))}
      </ul>
      <div className="text-xs text-gray-500">Desktop-first runtime UI</div>
    </nav>
  )
}

export default Sidebar
