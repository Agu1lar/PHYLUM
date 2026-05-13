# Creates the frontend scaffold for Agente Desktop (Tauri + React + TypeScript + Tailwind)
# Run this script from PowerShell (Windows):
#   powershell -ExecutionPolicy Bypass -File .\create_frontend.ps1

$root = Join-Path $PSScriptRoot 'frontend'
Write-Host "Creating frontend scaffold at $root"

$files = @{
  'package.json' = @'
{
  "name": "agente-desktop-frontend",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview",
    "tauri:dev": "tauri dev",
    "tauri:build": "tauri build"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "zustand": "^4.4.0"
  },
  "devDependencies": {
    "@types/react": "^18.0.28",
    "@types/react-dom": "^18.0.11",
    "@vitejs/plugin-react": "^4.0.0",
    "tailwindcss": "^3.4.7",
    "postcss": "^8.4.21",
    "autoprefixer": "^10.4.14",
    "typescript": "^5.1.3",
    "vite": "^5.0.0"
  }
}
'@

  'tsconfig.json' = @'
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2021", "DOM"],
    "jsx": "react-jsx",
    "module": "ESNext",
    "moduleResolution": "Node",
    "strict": true,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true
  },
  "include": ["src"]
}
'@

  'vite.config.ts' = @'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: { port: 5173 }
})
'@

  'index.html' = @'
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Agente Desktop</title>
  </head>
  <body class="bg-gray-900 text-gray-100">
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
'@

  'postcss.config.js' = @'
module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  }
}
'@

  'tailwind.config.js' = @'
module.exports = {
  content: [
    './index.html',
    './src/**/*.{ts,tsx,js,jsx}'
  ],
  theme: {
    extend: {
      colors: {
        accent: '#7c3aed'
      }
    }
  },
  plugins: []
}
'@

  '.env' = 'VITE_WS_URL=ws://127.0.0.1:8000/ws'

  'src/main.tsx' = @'
import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles/index.css'

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
'@

  'src/App.tsx' = @'
import React from 'react'
import Layout from './components/Layout'
import { useStore } from './state/store'

export default function App() {
  const connected = useStore(state => state.connected)
  return (
    <div className="min-h-screen">
      <Layout>
        <div className="p-4">
          <h1 className="text-2xl font-semibold mb-2">Agente Desktop</h1>
          <p className="text-sm text-gray-300">Status: {connected ? 'Connected' : 'Disconnected'}</p>
        </div>
      </Layout>
    </div>
  )
}
'@

  'src/styles/index.css' = @'
@tailwind base;
@tailwind components;
@tailwind utilities;

:root{
  --bg:#0b0f14;
  --panel:#0f1720;
}

body{background:var(--bg);} 
'@

  'src/components/Layout.tsx' = @'
import React from 'react'
import Sidebar from './Sidebar'

const Layout: React.FC<{children?: React.ReactNode}> = ({children}) => {
  return (
    <div className="flex h-screen">
      <Sidebar />
      <main className="flex-1 bg-gray-900 text-gray-100 overflow-auto">{children}</main>
      <aside className="w-96 bg-gray-800 border-l border-gray-700 p-3 overflow-auto">
        <div className="space-y-4">
          <div className="p-2 bg-gray-900 rounded">Logs</div>
          <div className="p-2 bg-gray-900 rounded">Approvals</div>
        </div>
      </aside>
    </div>
  )
}

export default Layout
'@

  'src/components/Sidebar.tsx' = @'
import React from 'react'

const Sidebar: React.FC = () => {
  return (
    <nav className="w-64 bg-gray-800 border-r border-gray-700 p-4 flex flex-col">
      <div className="mb-6">
        <div className="text-xl font-bold">Agente</div>
        <div className="text-xs text-gray-400">Desktop Control</div>
      </div>
      <ul className="space-y-2 flex-1">
        <li className="p-2 rounded hover:bg-gray-700 cursor-pointer">Chat</li>
        <li className="p-2 rounded hover:bg-gray-700 cursor-pointer">Agent</li>
        <li className="p-2 rounded hover:bg-gray-700 cursor-pointer">Logs</li>
        <li className="p-2 rounded hover:bg-gray-700 cursor-pointer">Approvals</li>
        <li className="p-2 rounded hover:bg-gray-700 cursor-pointer">History</li>
        <li className="p-2 rounded hover:bg-gray-700 cursor-pointer">Tools</li>
      </ul>
      <div className="text-xs text-gray-500">Dark theme</div>
    </nav>
  )
}

export default Sidebar
'@

  'src/components/ChatPanel.tsx' = @'
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
'@

  'src/components/AgentPanel.tsx' = @'
import React from 'react'
import { useStore } from '../state/store'

const AgentPanel: React.FC = ()=>{
  const tasks = useStore(s=>s.tasks)
  return (
    <div className="p-4">
      <h2 className="text-lg font-semibold mb-2">Agent Activities</h2>
      <ul className="space-y-2">
        {tasks.map((t,i)=>(
          <li key={i} className="p-2 bg-gray-800 rounded flex justify-between">
            <div>
              <div className="font-medium">{t.title}</div>
              <div className="text-sm text-gray-400">{t.status}</div>
            </div>
            <div className="text-xs text-gray-500">{t.progress}%</div>
          </li>
        ))}
      </ul>
    </div>
  )
}
export default AgentPanel
'@

  'src/components/LogsPanel.tsx' = @'
import React from 'react'
import { useStore } from '../state/store'

const LogsPanel: React.FC = ()=>{
  const logs = useStore(s=>s.logs)
  return (
    <div className="p-4 h-full">
      <h3 className="font-semibold">Logs</h3>
      <div className="mt-2 overflow-auto h-96 bg-gray-900 p-2 rounded">
        {logs.map((l,i)=>(<div key={i} className="text-xs text-gray-300">[{l.level}] {l.msg}</div>))}
      </div>
    </div>
  )
}
export default LogsPanel
'@

  'src/components/ApprovalsPanel.tsx' = @'
import React from 'react'
import { useStore } from '../state/store'

const ApprovalsPanel: React.FC = ()=>{
  const approvals = useStore(s=>s.approvals)
  return (
    <div className="p-4">
      <h3 className="font-semibold">Pending Approvals</h3>
      <ul className="mt-2 space-y-2">
        {approvals.map((a,i)=>(
          <li key={i} className="p-2 bg-gray-800 rounded flex justify-between">
            <div>
              <div className="font-medium">{a.title}</div>
              <div className="text-sm text-gray-400">{a.request_id}</div>
            </div>
            <div>
              <button className="px-2 py-1 mr-2 bg-green-600 rounded text-sm">Approve</button>
              <button className="px-2 py-1 bg-red-600 rounded text-sm">Reject</button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
export default ApprovalsPanel
'@

  'src/components/TasksPanel.tsx' = @'
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
'@

  'src/components/HistoryPanel.tsx' = @'
import React from 'react'
import { useStore } from '../state/store'

const HistoryPanel: React.FC = ()=>{
  const history = useStore(s=>s.history)
  return (
    <div className="p-4">
      <h3 className="font-semibold">History</h3>
      <ul className="mt-2 space-y-2">
        {history.map((h,i)=>(<li key={i} className="p-2 bg-gray-800 rounded">{h.action} - {h.timestamp}</li>))}
      </ul>
    </div>
  )
}
export default HistoryPanel
'@

  'src/components/ToolsPanel.tsx' = @'
import React from 'react'
import { useStore } from '../state/store'

const ToolsPanel: React.FC = ()=>{
  const tools = useStore(s=>s.tools)
  return (
    <div className="p-4">
      <h3 className="font-semibold">Tools</h3>
      <ul className="mt-2 space-y-2">
        {tools.map((t,i)=>(<li key={i} className="p-2 bg-gray-800 rounded">{t.name}</li>))}
      </ul>
    </div>
  )
}
export default ToolsPanel
'@

  'src/components/TerminalPanel.tsx' = @'
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
'@

  'src/hooks/useSocket.ts' = @'
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
'@

  'src/state/store.ts' = @'
import create from 'zustand'

type Message = {role:'user'|'agent', text:string}
type Log = {level:string, msg:string}

interface StoreState{
  connected: boolean
  messages: Message[]
  logs: Log[]
  approvals: any[]
  tasks: any[]
  history: any[]
  tools: any[]
  terminal: string[]
  setConnected: (v:boolean)=>void
  addMessage: (m:Message)=>void
  addLog: (l:Log)=>void
  addApproval: (a:any)=>void
  addTask: (t:any)=>void
  addHistory: (h:any)=>void
  addTool: (t:any)=>void
  addTerminalLine: (l:string)=>void
}

export const useStore = create<StoreState>((set)=>({
  connected:false,
  messages:[],
  logs:[],
  approvals:[],
  tasks:[],
  history:[],
  tools:[],
  terminal:[],
  setConnected: (v)=> set({connected:v}),
  addMessage: (m)=> set(state=>({messages:[...state.messages, m]})),
  addLog: (l)=> set(state=>({logs:[...state.logs, l]})),
  addApproval: (a)=> set(state=>({approvals:[...state.approvals, a]})),
  addTask: (t)=> set(state=>({tasks:[...state.tasks, t]})),
  addHistory: (h)=> set(state=>({history:[...state.history, h]})),
  addTool: (t)=> set(state=>({tools:[...state.tools, t]})),
  addTerminalLine: (l)=> set(state=>({terminal:[...state.terminal, l]}))
}))

export { useStore as useStoreDefault }
'@

  'README.md' = @'
Frontend scaffold for Agente Desktop

Tech stack: Tauri + React + TypeScript + Vite + Tailwind

Dev:
1. npm install
2. npm run dev

Set VITE_WS_URL in .env to point to backend websocket (e.g. ws://127.0.0.1:8000/ws)
'@

  'src-tauri/tauri.conf.json' = @'
{
  "package":{
    "productName":"agente-desktop",
    "version":"0.1.0"
  },
  "tauri":{
    "windows":[],
    "bundle":{
      "active":false
    }
  }
}
'@
}

# Create directories and write files
foreach ($rel in $files.Keys) {
  $path = Join-Path $root $rel
  $dir = Split-Path $path -Parent
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  $content = $files[$rel]
  Write-Output "Writing $path"
  $content | Out-File -FilePath $path -Encoding utf8 -Force
}

Write-Host "Frontend scaffold created at $root"
Write-Host "Run: cd $root; npm install; npm run dev"