import { useEffect } from 'react'
import { useStore } from '../state/store'

const WS_URL = (import.meta as any).env?.VITE_WS_URL || 'ws://127.0.0.1:8000/ws'

let sharedSocket: WebSocket | null = null
let reconnectTimer: number | null = null
let consumers = 0
let retries = 0

function connect() {
  if (sharedSocket && (sharedSocket.readyState === WebSocket.OPEN || sharedSocket.readyState === WebSocket.CONNECTING)) {
    return sharedSocket
  }
  const socket = new WebSocket(WS_URL)
  sharedSocket = socket
  socket.onopen = () => {
    retries = 0
    useStore.getState().setConnected(true)
  }
  socket.onmessage = event => {
    try {
      const message = JSON.parse(event.data)
      if (message.type === 'pong') {
        return
      }
      useStore.getState().applyEvent(message)
      if (message.type === 'run_finished') {
        useStore.getState().addMessage({
          role: 'agent',
          text: message.payload?.reflection?.summary ?? 'Execucao concluida.',
        })
      }
      if (message.type === 'run_failed') {
        useStore.getState().addMessage({ role: 'agent', text: `Execucao falhou: ${message.payload?.error ?? 'erro desconhecido'}` })
      }
      if (message.type === 'run_cancelled') {
        useStore.getState().addMessage({ role: 'agent', text: 'Execucao cancelada.' })
      }
      if (message.type === 'user_input_requested') {
        useStore.getState().addMessage({
          role: 'agent',
          text: message.payload?.handoff?.prompt ?? 'Preciso de mais contexto para continuar.',
        })
      }
      if (message.type === 'run_paused') {
        useStore.getState().addMessage({ role: 'agent', text: 'Execucao pausada aguardando sua resposta.' })
      }
      if (message.type === 'task_retry_scheduled') {
        useStore.getState().addMessage({ role: 'agent', text: 'Estou tentando uma estrategia alternativa para continuar a run.' })
      }
    } catch (error) {
      console.error(error)
    }
  }
  socket.onclose = () => {
    useStore.getState().setConnected(false)
    if (consumers > 0) {
      const delay = Math.min(10000, 1000 * 2 ** retries)
      retries += 1
      reconnectTimer = window.setTimeout(() => {
        connect()
      }, delay)
    }
  }
  socket.onerror = () => {
    socket.close()
  }
  return socket
}

export default function useSocket() {
  useEffect(() => {
    consumers += 1
    connect()
    return () => {
      consumers -= 1
      if (consumers <= 0) {
        if (reconnectTimer !== null) {
          window.clearTimeout(reconnectTimer)
          reconnectTimer = null
        }
        sharedSocket?.close()
        sharedSocket = null
      }
    }
  }, [])

  return {
    connected: useStore(state => state.connected),
  }
}
