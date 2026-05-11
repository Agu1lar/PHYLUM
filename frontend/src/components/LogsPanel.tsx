import React from 'react'
import { useStore } from '../state/store'

const LogsPanel: React.FC = ()=>{
  const logs = useStore(s=>s.logs)
  return (
    <div className="p-4 h-full">
      <h3 className="font-semibold">Logs</h3>
      <div className="mt-2 overflow-auto h-96 bg-gray-900 p-2 rounded">
        {logs.map((l,i)=>(<div key={i} className="text-xs text-gray-300">[{l.level}] {l.msg}</div>))}
      </div>
    </div>
  )
}
export default LogsPanel
