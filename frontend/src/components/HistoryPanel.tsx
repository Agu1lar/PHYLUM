import React from 'react'
import { useStore } from '../state/store'

const HistoryPanel: React.FC = ()=>{
  const history = useStore(s=>s.history)
  return (
    <div className="p-4">
      <h3 className="font-semibold">History</h3>
      <ul className="mt-2 space-y-2">
        {history.map((h,i)=>(<li key={i} className="p-2 bg-gray-800 rounded">{h.action} - {h.timestamp}</li>))}
      </ul>
    </div>
  )
}
export default HistoryPanel
