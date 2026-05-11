import React from 'react'
import { useStore } from '../state/store'

const ApprovalsPanel: React.FC = ()=>{
  const approvals = useStore(s=>s.approvals)
  return (
    <div className="p-4">
      <h3 className="font-semibold">Pending Approvals</h3>
      <ul className="mt-2 space-y-2">
        {approvals.map((a,i)=>(
          <li key={i} className="p-2 bg-gray-800 rounded flex justify-between">
            <div>
              <div className="font-medium">{a.title}</div>
              <div className="text-sm text-gray-400">{a.request_id}</div>
            </div>
            <div>
              <button className="px-2 py-1 mr-2 bg-green-600 rounded text-sm">Approve</button>
              <button className="px-2 py-1 bg-red-600 rounded text-sm">Reject</button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
export default ApprovalsPanel
