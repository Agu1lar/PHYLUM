declare global {
  interface Window {
    __AGENTE_API_BASE__?: string
    __AGENTE_WS_URL__?: string
    __TAURI__?: unknown
    __TAURI_INTERNALS__?: unknown
  }
}

const DEFAULT_API_BASE = 'http://127.0.0.1:8000'
const STORAGE_KEY = 'agente.runtime.config'
const CONFIG_EVENT = 'agente:runtime-config-changed'

export type DesktopConnectionMode = 'local-sidecar' | 'remote-backend'

export type RuntimeConnectionConfig = {
  apiBase: string
  wsUrl?: string | null
  desktopMode?: DesktopConnectionMode
}

export function isDesktopApp(): boolean {
  return typeof window !== 'undefined' && Boolean(window.__TAURI__ || window.__TAURI_INTERNALS__)
}

function normalizeApiBase(value: string | null | undefined): string {
  const text = (value || '').trim()
  if (!text) {
    return DEFAULT_API_BASE
  }
  const withScheme = /^https?:\/\//i.test(text) ? text : `http://${text}`
  try {
    const url = new URL(withScheme)
    return url.toString().replace(/\/$/, '')
  } catch (_error) {
    return DEFAULT_API_BASE
  }
}

function readStoredConfig(): RuntimeConnectionConfig {
  if (typeof window === 'undefined') {
    return {
      apiBase: DEFAULT_API_BASE,
      wsUrl: null,
      desktopMode: 'local-sidecar',
    }
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) {
      return {
        apiBase: DEFAULT_API_BASE,
        wsUrl: null,
        desktopMode: isDesktopApp() ? 'local-sidecar' : 'remote-backend',
      }
    }
    const parsed = JSON.parse(raw)
    return {
      apiBase: normalizeApiBase(parsed?.apiBase),
      wsUrl: typeof parsed?.wsUrl === 'string' && parsed.wsUrl.trim() ? parsed.wsUrl.trim() : null,
      desktopMode: parsed?.desktopMode === 'remote-backend' ? 'remote-backend' : 'local-sidecar',
    }
  } catch (_error) {
    return {
      apiBase: DEFAULT_API_BASE,
      wsUrl: null,
      desktopMode: isDesktopApp() ? 'local-sidecar' : 'remote-backend',
    }
  }
}

export function getRuntimeConnectionConfig(): RuntimeConnectionConfig {
  return readStoredConfig()
}

export function setRuntimeConnectionConfig(nextConfig: Partial<RuntimeConnectionConfig>) {
  if (typeof window === 'undefined') {
    return
  }
  window.__AGENTE_API_BASE__ = undefined
  window.__AGENTE_WS_URL__ = undefined
  const current = readStoredConfig()
  const merged: RuntimeConnectionConfig = {
    apiBase: normalizeApiBase(nextConfig.apiBase ?? current.apiBase),
    wsUrl:
      nextConfig.wsUrl === undefined
        ? current.wsUrl ?? null
        : typeof nextConfig.wsUrl === 'string' && nextConfig.wsUrl.trim()
          ? nextConfig.wsUrl.trim()
          : null,
    desktopMode: nextConfig.desktopMode ?? current.desktopMode ?? (isDesktopApp() ? 'local-sidecar' : 'remote-backend'),
  }
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(merged))
  window.dispatchEvent(new CustomEvent(CONFIG_EVENT, { detail: merged }))
}

export function resetRuntimeConnectionConfig() {
  if (typeof window === 'undefined') {
    return
  }
  window.__AGENTE_API_BASE__ = undefined
  window.__AGENTE_WS_URL__ = undefined
  window.localStorage.removeItem(STORAGE_KEY)
  const resetConfig = readStoredConfig()
  window.dispatchEvent(new CustomEvent(CONFIG_EVENT, { detail: resetConfig }))
}

export function subscribeRuntimeConnectionConfig(listener: () => void): () => void {
  if (typeof window === 'undefined') {
    return () => {}
  }
  const handler = () => listener()
  window.addEventListener(CONFIG_EVENT, handler)
  return () => window.removeEventListener(CONFIG_EVENT, handler)
}

export function getApiBase(): string {
  return (
    window.__AGENTE_API_BASE__ ||
    (import.meta as any).env?.VITE_API_URL ||
    readStoredConfig().apiBase ||
    DEFAULT_API_BASE
  )
}

export function getWsUrl(): string {
  if (window.__AGENTE_WS_URL__) {
    return window.__AGENTE_WS_URL__
  }
  const fromEnv = (import.meta as any).env?.VITE_WS_URL
  if (fromEnv) {
    return fromEnv
  }
  const stored = readStoredConfig()
  if (stored.wsUrl) {
    return stored.wsUrl
  }
  return `${getApiBase().replace(/^http/i, 'ws')}/ws`
}

export function getDesktopConnectionMode(): DesktopConnectionMode {
  return readStoredConfig().desktopMode ?? (isDesktopApp() ? 'local-sidecar' : 'remote-backend')
}
