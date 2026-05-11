import React, {useState} from 'react'
import { useStore } from '../state/store'
import useSocket from '../hooks/useSocket'

const TerminalPanel: React.FC = ()=>{
  const lines = useStore(s=>s.terminal)
  const { send } = useSocket()
  const [input, setInput] = useState('')
  const submit = ()=>{
    if(!input) return
    send({type:'terminal', command: input})
    setInput('')
  }
  return (
    <div className="p-4 flex flex-col h-full">
      <div className="flex-1 overflow-auto bg-black text-green-200 p-3 rounded">
        {lines.map((l,i)=>(<div key={i} className="text-xs">{l}</div>))}
      </div>
      <div className="mt-2 flex">
        <input value={input} onChange={e=>setInput(e.target.value)} className="flex-1 p-2 rounded bg-gray-800 border border-gray-700" />
        <button onClick={submit} className="ml-2 px-4 py-2 bg-accent rounded">Send</button>
      </div>
    </div>
  )
}
export default TerminalPanel
