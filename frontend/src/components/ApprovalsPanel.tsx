import React from 'react'
import { getApiBase } from '../lib/runtimeConfig'
import { useStore } from '../state/store'

const ApprovalsPanel: React.FC = () => {
  const currentRun = useStore(state => (state.currentRunId ? state.runs[state.currentRunId] : null))
  const approvals = currentRun?.approvals ?? []
  const pendingApprovals = approvals.filter(approval => approval.status === 'pending')

  const API_BASE = getApiBase()

  async function postApproval(id: string, status: string) {
    try {
      const url = `${API_BASE}/approval/${encodeURIComponent(id)}`
      await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) })
    } catch (e) {
      console.error('approval update failed', e)
    }
  }

  async function resumeRun() {
    if (!currentRun) return
    try {
      await fetch(`${API_BASE}/run/${encodeURIComponent(currentRun.request_id)}/resume`, { method: 'POST' })
    } catch (e) {
      console.error('resume failed', e)
    }
  }

  return (
    <div className="rounded bg-gray-900 p-4">
      <h3 className="font-semibold">Approvals</h3>
      <ul className="mt-2 space-y-2">
        {approvals.length === 0 ? <li className="text-sm text-gray-400">Nenhuma aprovacao pendente.</li> : null}
        {approvals.map(approval => (
          <li key={approval.approval_id} className="p-2 bg-gray-800 rounded flex justify-between items-center gap-3">
            <div>
              <div className="font-medium">{approval.title}</div>
              <div className="text-sm text-gray-400">{approval.reason || approval.task_id}</div>
              {approval.details?.path ? <div className="text-xs text-gray-500">Path: {approval.details.path}</div> : null}
              {approval.details?.dest ? <div className="text-xs text-gray-500">Destino: {approval.details.dest}</div> : null}
              <div className="text-xs text-gray-500">Status: {approval.status || 'pending'}</div>
            </div>
            <div>
              <button
                disabled={approval.status && approval.status !== 'pending'}
                onClick={() => postApproval(approval.approval_id, 'approved')}
                className="px-2 py-1 mr-2 bg-green-600 rounded text-sm"
              >
                Approve
              </button>
              <button
                disabled={approval.status && approval.status !== 'pending'}
                onClick={() => postApproval(approval.approval_id, 'rejected')}
                className="px-2 py-1 bg-red-600 rounded text-sm"
              >
                Reject
              </button>
            </div>
          </li>
        ))}
      </ul>
      {currentRun && pendingApprovals.length === 0 && ['awaiting_approval', 'paused'].includes(currentRun.status) ? (
        <button onClick={resumeRun} className="mt-3 rounded bg-blue-700 px-3 py-2 text-sm">
          Retomar run
        </button>
      ) : null}
    </div>
  )
}
export default ApprovalsPanel
