import React, { useMemo } from 'react'
import ApprovalsPanel from './ApprovalsPanel'
import HandoffPanel from './HandoffPanel'
import { useStore } from '../state/store'

const ApprovalWidgetWindow: React.FC = () => {
  const runs = useStore(state => state.runs)
  const connected = useStore(state => state.connected)
  const stats = useMemo(() => {
    const allRuns = Object.values(runs)
    const pendingApprovals = allRuns.reduce(
      (total, run) => total + run.approvals.filter(approval => approval.status === 'pending').length,
      0,
    )
    const activeGrants = allRuns.reduce(
      (total, run) => total + run.approval_grants.filter(grant => grant.status === 'active').length,
      0,
    )
    const pendingHandoffs = allRuns.filter(run => run.pending_handoff).length
    return {
      runs: allRuns.length,
      pendingApprovals,
      activeGrants,
      pendingHandoffs,
    }
  }, [runs])

  return (
    <div className="flex min-h-screen flex-col bg-gray-950 p-3 text-gray-100">
      <div className="mb-3 rounded border border-gray-800 bg-gray-900 p-3">
        <div className="text-xs uppercase tracking-[0.2em] text-blue-300">Approval Widget</div>
        <div className="mt-2 text-sm text-gray-300">
          {connected ? 'Conectado ao backend local.' : 'Reconectando ao backend local...'}
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-gray-400">
          <div className="rounded bg-gray-800 p-2">Runs: {stats.runs}</div>
          <div className="rounded bg-gray-800 p-2">Aprovacoes: {stats.pendingApprovals}</div>
          <div className="rounded bg-gray-800 p-2">Handoffs: {stats.pendingHandoffs}</div>
          <div className="rounded bg-gray-800 p-2">Grants: {stats.activeGrants}</div>
        </div>
      </div>
      <div className="space-y-3">
        <ApprovalsPanel showAllRuns compact />
        <HandoffPanel showAllRuns compact />
      </div>
    </div>
  )
}

export default ApprovalWidgetWindow
