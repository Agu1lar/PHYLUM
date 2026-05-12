import React from 'react'
import { useStore } from '../state/store'

const RecoveryPanel: React.FC = () => {
  const currentRun = useStore(state => (state.currentRunId ? state.runs[state.currentRunId] : null))
  const recovery = currentRun?.recovery

  return (
    <div className="rounded bg-gray-900 p-4">
      <h3 className="font-semibold">Recovery</h3>
      {!recovery ? (
        <div className="mt-2 text-sm text-gray-400">Nenhuma estrategia de recovery ativa.</div>
      ) : (
        <div className="mt-2 rounded border border-cyan-800 bg-cyan-950/20 p-3 text-sm">
          <div className="font-medium text-cyan-100">Classificacao: {recovery.classification ?? recovery.suggested_action ?? 'n/a'}</div>
          {recovery.reason ? <div className="mt-1 text-cyan-50/90">{recovery.reason}</div> : null}
        </div>
      )}
    </div>
  )
}

export default RecoveryPanel
