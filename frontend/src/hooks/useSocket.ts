import { useEffect, useRef } from 'react'
import { useStore } from '../state/store'

const WS_URL = import.meta.env.VITE_WS_URL || 'ws://127.0.0.1:8000/ws'

export default function useSocket(){
  const wsRef = useRef<WebSocket | null>(null)
  const addMessage = useStore.getState().addMessage
  const addLog = useStore.getState().addLog
  const setConnected = useStore.getState().setConnected

  useEffect(()=>{
    let mounted = true
    let retries = 0
    function connect(){
      const ws = new WebSocket(WS_URL)
      wsRef.current = ws
      ws.onopen = ()=>{ setConnected(true); retries=0; addLog({level:'info', msg:'WebSocket connected'}) }
      ws.onmessage = (ev)=>{
        try{
          const d = JSON.parse(ev.data)
          handleMessage(d)
        }catch(e){ console.error(e) }
      }
      ws.onclose = ()=>{ setConnected(false); addLog({level:'warn', msg:'WebSocket closed'}); if(mounted) setTimeout(()=>connect(), Math.min(10000, 1000*(2**retries))); retries++ }
      ws.onerror = (e)=>{ addLog({level:'error', msg:'WebSocket error'}); ws.close() }
    }
    function handleMessage(d:any){
      if(d.type === 'chat'){ addMessage({role:'agent', text:d.text}) }
      if(d.type === 'log'){ addLog(d.payload) }
      if(d.type === 'approval'){ useStore.getState().addApproval(d.payload) }
      if(d.type === 'task'){ useStore.getState().addTask(d.payload) }
      if(d.type === 'history'){ useStore.getState().addHistory(d.payload) }
      if(d.type === 'tool'){ useStore.getState().addTool(d.payload) }
      if(d.type === 'terminal'){ useStore.getState().addTerminalLine(d.payload) }
    }
    connect()
    return ()=>{ mounted=false; wsRef.current?.close() }
  }, [])

  return {
    send: (obj:any)=>{ try{ wsRef.current?.send(JSON.stringify(obj)) }catch(e){ console.error(e) } }
  }
}
