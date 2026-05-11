import React, {useEffect, useState} from 'react'
import { useStore } from '../state/store'
import useSocket from '../hooks/useSocket'

const ChatPanel: React.FC = () => {
  const messages = useStore(s => s.messages)
  const addMessage = useStore(s => s.addMessage)
  const { send } = useSocket()
  const [text, setText] = useState('')

  const submit = () =>{
    if(!text) return
    send({type:'chat', text})
    addMessage({role:'user', text})
    setText('')
  }

  return (
    <div className="h-full p-4 flex flex-col">
      <div className="flex-1 overflow-auto space-y-2">
        {messages.map((m,i)=>(
          <div key={i} className={m.role==='agent'? 'text-left':'text-right'}>
            <div className="inline-block bg-gray-700 p-2 rounded">{m.text}</div>
          </div>
        ))}
      </div>
      <div className="mt-2 flex">
        <input value={text} onChange={e=>setText(e.target.value)} className="flex-1 p-2 rounded bg-gray-800 border border-gray-700" />
        <button onClick={submit} className="ml-2 px-4 py-2 bg-accent rounded">Send</button>
      </div>
    </div>
  )
}
export default ChatPanel
