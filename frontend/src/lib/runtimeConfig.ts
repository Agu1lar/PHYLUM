declare global {
  interface Window {
    __AGENTE_API_BASE__?: string
    __AGENTE_WS_URL__?: string
    __TAURI__?: unknown
    __TAURI_INTERNALS__?: unknown
  }
}

const DEFAULT_API_BASE = 'http://127.0.0.1:8000'

export function isDesktopApp(): boolean {
  return typeof window !== 'undefined' && Boolean(window.__TAURI__ || window.__TAURI_INTERNALS__)
}

export function getApiBase(): string {
  return window.__AGENTE_API_BASE__ || (import.meta as any).env?.VITE_API_URL || DEFAULT_API_BASE
}

export function getWsUrl(): string {
  if (window.__AGENTE_WS_URL__) {
    return window.__AGENTE_WS_URL__
  }
  const fromEnv = (import.meta as any).env?.VITE_WS_URL
  if (fromEnv) {
    return fromEnv
  }
  return `${getApiBase().replace(/^http/i, 'ws')}/ws`
}
