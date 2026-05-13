import React from 'react'
import { getApiBase } from '../lib/runtimeConfig'
import { useStore } from '../state/store'

type ApprovalsPanelProps = {
  showAllRuns?: boolean
  compact?: boolean
}

const ApprovalsPanel: React.FC<ApprovalsPanelProps> = ({ showAllRuns = false, compact = false }) => {
  const currentRunId = useStore(state => state.currentRunId)
  const runs = useStore(state => state.runs)
  const currentRun = useStore(state => (state.currentRunId ? state.runs[state.currentRunId] : null))
  const visibleRuns = showAllRuns ? Object.values(runs) : currentRun ? [currentRun] : []
  const approvals = visibleRuns.flatMap(run => run.approvals.map(approval => ({ ...approval, request_id: run.request_id })))
  const grants = visibleRuns.flatMap(run => run.approval_grants.map(grant => ({ ...grant, request_id: run.request_id })))
  const pendingApprovals = approvals.filter(approval => approval.status === 'pending')
  const activeGrants = grants.filter(grant => grant.status === 'active')

  const API_BASE = getApiBase()

  async function postApproval(id: string, requestId: string, status: string, scope: 'single' | 'run_scope' = 'single') {
    try {
      const url = `${API_BASE}/approval/${encodeURIComponent(id)}`
      await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status, scope }) })
      await fetch(`${API_BASE}/run/${encodeURIComponent(requestId)}/resume`, { method: 'POST' })
    } catch (e) {
      console.error('approval update failed', e)
    }
  }

  async function resumeRun(requestId: string) {
    try {
      await fetch(`${API_BASE}/run/${encodeURIComponent(requestId)}/resume`, { method: 'POST' })
    } catch (e) {
      console.error('resume failed', e)
    }
  }

  async function revokeGrant(requestId: string, grantId: string) {
    try {
      await fetch(`${API_BASE}/run/${encodeURIComponent(requestId)}/approval-grants/${encodeURIComponent(grantId)}`, {
        method: 'DELETE',
      })
    } catch (e) {
      console.error('revoke grant failed', e)
    }
  }

  return (
    <div className={`rounded bg-gray-900 ${compact ? 'p-3' : 'p-4'}`}>
      <h3 className="font-semibold">Approvals</h3>
      <div className="mt-2 flex flex-wrap gap-2 text-xs text-gray-400">
        <span>Pendentes: {pendingApprovals.length}</span>
        <span>Grants ativos: {activeGrants.length}</span>
        {showAllRuns ? <span>Runs visiveis: {visibleRuns.length}</span> : null}
      </div>
      <ul className="mt-3 space-y-2">
        {approvals.length === 0 ? <li className="text-sm text-gray-400">Nenhuma aprovacao pendente.</li> : null}
        {approvals.map(approval => (
          <li key={approval.approval_id} className="p-2 bg-gray-800 rounded flex justify-between items-center gap-3">
            <div>
              <div className="font-medium">{approval.title}</div>
              <div className="text-sm text-gray-400">{approval.reason || approval.task_id}</div>
              {showAllRuns ? <div className="text-xs text-gray-500">Run: {approval.request_id}</div> : null}
              {approval.details?.path ? <div className="text-xs text-gray-500">Path: {approval.details.path}</div> : null}
              {approval.details?.dest ? <div className="text-xs text-gray-500">Destino: {approval.details.dest}</div> : null}
              <div className="text-xs text-gray-500">Status: {approval.status || 'pending'}</div>
            </div>
            <div className="flex flex-wrap justify-end gap-2">
              <button
                disabled={approval.status && approval.status !== 'pending'}
                onClick={() => postApproval(approval.approval_id, approval.request_id, 'approved', 'single')}
                className="px-2 py-1 bg-green-600 rounded text-sm"
              >
                Aprovar acao
              </button>
              {(approval.details?.available_scopes ?? []).includes('run_scope') ? (
                <button
                  disabled={approval.status && approval.status !== 'pending'}
                  onClick={() => postApproval(approval.approval_id, approval.request_id, 'approved', 'run_scope')}
                  className="px-2 py-1 bg-emerald-700 rounded text-sm"
                >
                  Aprovar fluxo
                </button>
              ) : null}
              <button
                disabled={approval.status && approval.status !== 'pending'}
                onClick={() => postApproval(approval.approval_id, approval.request_id, 'rejected')}
                className="px-2 py-1 bg-red-600 rounded text-sm"
              >
                Rejeitar
              </button>
            </div>
          </li>
        ))}
      </ul>
      <div className="mt-4 space-y-2">
        {activeGrants.length > 0 ? <div className="text-xs uppercase tracking-wide text-gray-500">Grants ativos</div> : null}
        {activeGrants.map(grant => (
          <div key={grant.grant_id} className="rounded border border-gray-700 bg-gray-800 p-3 text-sm">
            <div className="font-medium">{grant.title ?? 'Fluxo aprovado'}</div>
            <div className="text-xs text-gray-400">
              {grant.family ?? grant.tool} | risco maximo: {grant.max_risk_level ?? 'medium'}
            </div>
            {showAllRuns ? <div className="mt-1 text-xs text-gray-500">Run: {grant.request_id}</div> : null}
            {grant.reason ? <div className="mt-1 text-xs text-gray-400">{grant.reason}</div> : null}
            <button
              onClick={() => revokeGrant(grant.request_id, grant.grant_id)}
              className="mt-2 rounded bg-gray-700 px-2 py-1 text-xs text-gray-100"
            >
              Revogar grant
            </button>
          </div>
        ))}
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        {(showAllRuns ? visibleRuns : currentRun ? [currentRun] : [])
          .filter(run => pendingApprovals.filter(approval => approval.request_id === run.request_id).length === 0)
          .filter(run => ['awaiting_approval', 'paused'].includes(run.status))
          .map(run => (
            <button
              key={run.request_id}
              onClick={() => resumeRun(run.request_id)}
              className="rounded bg-blue-700 px-3 py-2 text-sm"
            >
              {showAllRuns && run.request_id !== currentRunId ? `Retomar ${run.request_id.slice(0, 8)}` : 'Retomar run'}
            </button>
          ))}
      </div>
    </div>
  )
}
export default ApprovalsPanel
