import React from 'react'
import { useStore } from '../state/store'

const ToolsPanel: React.FC = ()=>{
  const tools = useStore(s=>s.tools)
  return (
    <div className="p-4">
      <h3 className="font-semibold">Tools</h3>
      <ul className="mt-2 space-y-2">
        {tools.map((t,i)=>(<li key={i} className="p-2 bg-gray-800 rounded">{t.name}</li>))}
      </ul>
    </div>
  )
}
export default ToolsPanel
