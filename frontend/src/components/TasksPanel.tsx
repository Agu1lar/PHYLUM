import React from 'react'
import { useStore } from '../state/store'

const TasksPanel: React.FC = () => {
  const currentRun = useStore(state => (state.currentRunId ? state.runs[state.currentRunId] : null))
  const tasks = currentRun?.tasks ?? []
  return (
    <div className="max-h-72 overflow-auto rounded border border-gray-700 bg-gray-800 p-4">
      <h3 className="font-semibold">Tasks</h3>
      <ul className="mt-3 space-y-2">
        {tasks.length === 0 ? <li className="text-sm text-gray-400">Nenhuma task planejada ainda.</li> : null}
        {tasks.map(task => (
          <li key={task.id} className="rounded bg-gray-900 p-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="font-medium">{task.title}</div>
                <div className="text-xs text-gray-500">{task.tool} / {task.action}</div>
              </div>
              <div className="text-sm text-gray-300">{task.status}</div>
            </div>
            {'attempt' in task ? <div className="mt-2 text-xs text-cyan-300">Attempt: {(task as any).attempt}</div> : null}
            {task.error ? <div className="mt-2 text-xs text-red-300">{task.error}</div> : null}
          </li>
        ))}
      </ul>
    </div>
  )
}
export default TasksPanel
