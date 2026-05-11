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
