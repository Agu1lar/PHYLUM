import React from 'react'
import { useStore } from '../state/store'

const AgentPanel: React.FC = ()=>{
  const tasks = useStore(s=>s.tasks)
  return (
    <div className="p-4">
      <h2 className="text-lg font-semibold mb-2">Agent Activities</h2>
      <ul className="space-y-2">
        {tasks.map((t,i)=>(
          <li key={i} className="p-2 bg-gray-800 rounded flex justify-between">
            <div>
              <div className="font-medium">{t.title}</div>
              <div className="text-sm text-gray-400">{t.status}</div>
            </div>
            <div className="text-xs text-gray-500">{t.progress}%</div>
          </li>
        ))}
      </ul>
    </div>
  )
}
export default AgentPanel
