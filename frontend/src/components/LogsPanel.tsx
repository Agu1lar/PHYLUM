import React from 'react'
import { useStore } from '../state/store'

const LogsPanel: React.FC = () => {
  const currentRun = useStore(state => (state.currentRunId ? state.runs[state.currentRunId] : null))
  const events = currentRun?.events ?? []
  return (
    <div className="rounded bg-gray-900 p-4 h-full">
      <h3 className="font-semibold">Run Events</h3>
      <div className="mt-2 overflow-auto h-96 bg-black/20 p-2 rounded space-y-2">
        {events.length === 0 ? <div className="text-xs text-gray-500">Sem eventos ainda.</div> : null}
        {events.map((event, index) => (
          <div key={index} className="text-xs text-gray-300 border-b border-gray-800 pb-2">
            <div className="font-medium text-gray-200">{event.type}</div>
            <pre className="mt-1 whitespace-pre-wrap text-gray-400">{JSON.stringify(event.payload, null, 2)}</pre>
          </div>
        ))}
      </div>
    </div>
  )
}
export default LogsPanel
