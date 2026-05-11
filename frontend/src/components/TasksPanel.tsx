import React from 'react'
import { useStore } from '../state/store'

const TasksPanel: React.FC = ()=>{
  const tasks = useStore(s=>s.tasks)
  return (
    <div className="p-4">
      <h3 className="font-semibold">Tasks</h3>
      <ul className="mt-2 space-y-2">
        {tasks.map((t,i)=>(<li key={i} className="p-2 bg-gray-800 rounded">{t.title} - {t.status}</li>))}
      </ul>
    </div>
  )
}
export default TasksPanel
