import { create } from 'zustand'

export type Message = { role: 'user' | 'agent'; text: string }
export type RuntimeEvent = { type: string; payload: any }
export type ViewMode = 'dashboard' | 'settings'
export type RuntimeMode = 'manual_assist' | 'agentic' | 'heuristic'
export type RuntimeTask = {
  id: string
  title: string
  tool: string
  action: string
  status: string
  requires_approval?: boolean
  approval_id?: string | null
  result?: any
  error?: string | null
  reflection?: any
}
export type RuntimeApproval = {
  approval_id: string
  request_id: string
  task_id?: string | null
  title: string
  reason?: string
  status: string
  risk?: any
}
export type RuntimeHandoffOption = {
  id: string
  label: string
  value?: any
}
export type RuntimeHandoff = {
  handoff_id: string
  request_id: string
  task_id?: string | null
  kind: string
  title: string
  prompt: string
  reason?: string | null
  status: string
  allow_free_text?: boolean
  options?: RuntimeHandoffOption[]
  response?: any
}
export type RunRecord = {
  request_id: string
  status: string
  runtime_mode?: RuntimeMode
  provider?: string | null
  model?: string | null
  created_at?: string
  last_updated?: string
  inputs?: any
  outputs?: any
  current_node?: string | null
  current_task_id?: string | null
  tasks: RuntimeTask[]
  approvals: RuntimeApproval[]
  handoffs: RuntimeHandoff[]
  pending_handoff?: RuntimeHandoff | null
  events: RuntimeEvent[]
  recovery?: any
  error?: string | null
  reflection?: any
}
export type ProviderSetting = {
  provider: string
  display_name: string
  configured: boolean
  last4?: string | null
  updated_at?: string | null
  default_model?: string | null
  base_url?: string | null
  requires_base_url?: boolean
  models: string[]
}
export type SupportedToolDefinition = {
  type: string
  function: {
    name: string
    description: string
    parameters: any
  }
}

interface StoreState {
  connected: boolean
  messages: Message[]
  activeView: ViewMode
  currentRunId: string | null
  runs: Record<string, RunRecord>
  providers: ProviderSetting[]
  supportedTools: SupportedToolDefinition[]
  selectedRuntimeMode: RuntimeMode
  selectedProvider: string | null
  selectedModel: string
  setConnected: (value: boolean) => void
  addMessage: (message: Message) => void
  setActiveView: (view: ViewMode) => void
  setCurrentRun: (requestId: string | null) => void
  setProviderSettings: (providers: ProviderSetting[]) => void
  setSupportedTools: (tools: SupportedToolDefinition[]) => void
  upsertProviderSetting: (provider: ProviderSetting) => void
  selectRuntimeMode: (mode: RuntimeMode) => void
  selectProvider: (provider: string | null) => void
  selectModel: (model: string) => void
  hydrateRun: (runState: any) => void
  applyEvent: (event: RuntimeEvent) => void
}

function ensureRun(
  runs: Record<string, RunRecord>,
  requestId: string,
  patch?: Partial<RunRecord>,
): Record<string, RunRecord> {
  const existing = runs[requestId] ?? {
    request_id: requestId,
    status: 'queued',
    tasks: [],
    approvals: [],
    handoffs: [],
    events: [],
  }
  return {
    ...runs,
    [requestId]: {
      ...existing,
      ...patch,
      tasks: patch?.tasks ?? existing.tasks,
      approvals: patch?.approvals ?? existing.approvals,
      handoffs: patch?.handoffs ?? existing.handoffs,
      events: patch?.events ?? existing.events,
    },
  }
}

function upsertTask(tasks: RuntimeTask[], task: RuntimeTask): RuntimeTask[] {
  const idx = tasks.findIndex(existing => existing.id === task.id)
  if (idx === -1) {
    return [...tasks, task]
  }
  return tasks.map(existing => (existing.id === task.id ? { ...existing, ...task } : existing))
}

function upsertApproval(approvals: RuntimeApproval[], approval: RuntimeApproval): RuntimeApproval[] {
  const idx = approvals.findIndex(existing => existing.approval_id === approval.approval_id)
  if (idx === -1) {
    return [...approvals, approval]
  }
  return approvals.map(existing =>
    existing.approval_id === approval.approval_id ? { ...existing, ...approval } : existing,
  )
}

function upsertHandoff(handoffs: RuntimeHandoff[], handoff: RuntimeHandoff): RuntimeHandoff[] {
  const idx = handoffs.findIndex(existing => existing.handoff_id === handoff.handoff_id)
  if (idx === -1) {
    return [...handoffs, handoff]
  }
  return handoffs.map(existing =>
    existing.handoff_id === handoff.handoff_id ? { ...existing, ...handoff } : existing,
  )
}

function resolveModel(providers: ProviderSetting[], providerId: string | null, currentModel: string): string {
  const provider = providers.find(item => item.provider === providerId)
  if (!provider) {
    return currentModel
  }
  if (currentModel) {
    return currentModel
  }
  return provider.default_model || provider.models[0] || ''
}

export const useStore = create<StoreState>((set, get) => ({
  connected: false,
  messages: [],
  activeView: 'dashboard',
  currentRunId: null,
  runs: {},
  providers: [],
  supportedTools: [],
  selectedRuntimeMode: 'agentic',
  selectedProvider: null,
  selectedModel: '',
  setConnected: value => set({ connected: value }),
  addMessage: message => set(state => ({ messages: [...state.messages, message] })),
  setActiveView: view => set({ activeView: view }),
  setCurrentRun: requestId => set({ currentRunId: requestId }),
  setProviderSettings: providers =>
    set(state => {
      const selectedProvider =
        providers.find(provider => provider.provider === state.selectedProvider)?.provider ??
        providers.find(provider => provider.configured)?.provider ??
        providers[0]?.provider ??
        null
      return {
        providers,
        selectedProvider,
        selectedModel: resolveModel(providers, selectedProvider, state.selectedModel),
      }
    }),
  setSupportedTools: tools => set({ supportedTools: tools }),
  upsertProviderSetting: provider =>
    set(state => {
      const existing = state.providers.find(item => item.provider === provider.provider)
      const providers = existing
        ? state.providers.map(item => (item.provider === provider.provider ? provider : item))
        : [...state.providers, provider]
      const selectedProvider = state.selectedProvider ?? provider.provider
      return {
        providers,
        selectedProvider,
        selectedModel: resolveModel(providers, selectedProvider, state.selectedModel),
      }
    }),
  selectRuntimeMode: mode => set({ selectedRuntimeMode: mode }),
  selectProvider: provider =>
    set(state => ({
      selectedProvider: provider,
      selectedModel: resolveModel(state.providers, provider, ''),
    })),
  selectModel: model => set({ selectedModel: model }),
  hydrateRun: runState =>
    set(state => {
      const requestId = runState.request_id
      const runs = ensureRun(state.runs, requestId, {
        ...runState,
        events: runState.history ?? [],
        tasks: runState.tasks ?? [],
        approvals: runState.approvals ?? [],
        handoffs: runState.handoffs ?? [],
        pending_handoff: runState.pending_handoff ?? null,
        recovery: runState.recovery ?? null,
      })
      return {
        currentRunId: requestId,
        runs,
      }
    }),
  applyEvent: event =>
    set(state => {
      const requestId = event.payload?.request_id
      if (!requestId) {
        return state
      }
      const existing = state.runs[requestId] ?? {
        request_id: requestId,
        status: 'queued',
        tasks: [],
        approvals: [],
        handoffs: [],
        events: [],
      }
      let nextRun: RunRecord = {
        ...existing,
        events: [...existing.events, event],
      }

      switch (event.type) {
        case 'run_started':
          nextRun = {
            ...nextRun,
            status: event.payload.status ?? 'planning',
            runtime_mode: event.payload.runtime_mode,
            provider: event.payload.provider,
            model: event.payload.model,
            inputs: event.payload.inputs,
            created_at: event.payload.created_at,
          }
          break
        case 'run_resumed':
          nextRun = {
            ...nextRun,
            status: event.payload.status ?? 'resuming',
          }
          break
        case 'task_planned':
          nextRun = {
            ...nextRun,
            tasks: upsertTask(nextRun.tasks, event.payload.task),
          }
          break
        case 'approval_requested':
          nextRun = {
            ...nextRun,
            approvals: upsertApproval(nextRun.approvals, event.payload.approval),
            tasks: nextRun.tasks.map(task =>
              task.id === event.payload.approval.task_id
                ? {
                    ...task,
                    status: 'waiting_approval',
                    requires_approval: true,
                    approval_id: event.payload.approval.approval_id,
                  }
                : task,
            ),
          }
          break
        case 'approval_resolved':
          nextRun = {
            ...nextRun,
            approvals: nextRun.approvals.map(approval =>
              approval.approval_id === event.payload.approval_id
                ? { ...approval, status: event.payload.status }
                : approval,
            ),
            tasks: nextRun.tasks.map(task =>
              task.id === event.payload.task_id
                ? { ...task, status: event.payload.status === 'approved' ? 'approved' : 'rejected' }
                : task,
            ),
          }
          break
        case 'user_input_requested':
          nextRun = {
            ...nextRun,
            status: 'awaiting_input',
            pending_handoff: event.payload.handoff,
            handoffs: upsertHandoff(nextRun.handoffs, event.payload.handoff),
          }
          break
        case 'user_input_received':
          nextRun = {
            ...nextRun,
            status: 'paused',
            pending_handoff: nextRun.pending_handoff
              ? { ...nextRun.pending_handoff, response: event.payload.response, status: 'answered' }
              : nextRun.pending_handoff,
            handoffs: nextRun.handoffs.map(handoff =>
              handoff.handoff_id === event.payload.handoff_id
                ? { ...handoff, response: event.payload.response, status: 'answered' }
                : handoff,
            ),
          }
          break
        case 'run_paused':
          nextRun = {
            ...nextRun,
            status: event.payload.status ?? 'paused',
          }
          break
        case 'task_started':
          nextRun = {
            ...nextRun,
            current_task_id: event.payload.task.id,
            status: 'running',
            tasks: upsertTask(nextRun.tasks, { ...event.payload.task, status: 'running' }),
          }
          break
        case 'task_retry_scheduled':
          nextRun = {
            ...nextRun,
            status: 'recovering',
            recovery: event.payload.classification,
            tasks: nextRun.tasks.map(task =>
              task.id === event.payload.task_id
                ? {
                    ...task,
                    status: 'retry_scheduled',
                  }
                : task,
            ),
          }
          break
        case 'task_finished':
          nextRun = {
            ...nextRun,
            tasks: nextRun.tasks.map(task =>
              task.id === event.payload.task_id
                ? {
                    ...task,
                    status: 'completed',
                    result: event.payload.result,
                    reflection: event.payload.reflection,
                  }
                : task,
            ),
            pending_handoff: nextRun.pending_handoff?.task_id === event.payload.task_id ? null : nextRun.pending_handoff,
          }
          break
        case 'task_cancelled':
          nextRun = {
            ...nextRun,
            status: nextRun.status === 'cancelled' ? nextRun.status : 'cancelling',
            tasks: nextRun.tasks.map(task =>
              task.id === event.payload.task_id
                ? {
                    ...task,
                    status: 'cancelled',
                    error: event.payload.error ?? 'cancelled',
                  }
                : task,
            ),
          }
          break
        case 'task_failed':
          nextRun = {
            ...nextRun,
            status: 'failed',
            error: event.payload.error,
            recovery: event.payload.reflection?.recommended_action ?? nextRun.recovery,
            tasks: nextRun.tasks.map(task =>
              task.id === event.payload.task_id
                ? {
                    ...task,
                    status: 'failed',
                    error: event.payload.error,
                    reflection: event.payload.reflection,
                  }
                : task,
            ),
          }
          break
        case 'run_finished':
          nextRun = {
            ...nextRun,
            status: event.payload.status ?? 'completed',
            reflection: event.payload.reflection,
            pending_handoff: null,
          }
          break
        case 'run_cancellation_requested':
          nextRun = {
            ...nextRun,
            status: 'cancelling',
          }
          break
        case 'run_cancelled':
          nextRun = {
            ...nextRun,
            status: event.payload.status ?? 'cancelled',
            error: event.payload.error ?? 'cancelled',
          }
          break
        case 'run_failed':
          nextRun = {
            ...nextRun,
            status: event.payload.status ?? 'failed',
            error: event.payload.error,
          }
          break
        default:
          break
      }

      return {
        currentRunId: get().currentRunId ?? requestId,
        runs: {
          ...state.runs,
          [requestId]: nextRun,
        },
      }
    }),
}))

export { useStore as useStoreDefault }
