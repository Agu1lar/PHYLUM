import { getApiBase, getWsUrl, isDesktopApp } from './runtimeConfig'

declare global {
  interface Window {
    __AGENTE_BACKEND_BOOT__?: Promise<void>
    __AGENTE_BACKEND_CHILD__?: { kill?: () => Promise<void> | void } | null
  }
}

async function waitForBackend(maxAttempts = 40, delayMs = 250) {
  const healthUrl = `${getApiBase()}/health`
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    try {
      const response = await fetch(healthUrl)
      if (response.ok) {
        return
      }
    } catch (error) {
      // Keep waiting while the local backend is starting.
    }
    await new Promise(resolve => window.setTimeout(resolve, delayMs))
  }
  throw new Error('desktop backend did not become ready in time')
}

export async function ensureDesktopBackend() {
  if (!isDesktopApp()) {
    return
  }

  try {
    await waitForBackend(2, 50)
    return
  } catch (error) {
    // Sidecar is not up yet, continue with startup below.
  }

  if (window.__AGENTE_BACKEND_BOOT__) {
    return window.__AGENTE_BACKEND_BOOT__
  }

  window.__AGENTE_API_BASE__ = getApiBase()
  window.__AGENTE_WS_URL__ = getWsUrl()

  window.__AGENTE_BACKEND_BOOT__ = (async () => {
    const { Command } = await import('@tauri-apps/plugin-shell')
    const command = Command.sidecar('agente-backend', ['--host', '127.0.0.1', '--port', '8000'])
    command.on('close', event => {
      console.info('desktop backend sidecar closed', event)
      window.__AGENTE_BACKEND_CHILD__ = null
      window.__AGENTE_BACKEND_BOOT__ = undefined
    })
    command.on('error', error => {
      console.error('desktop backend sidecar error', error)
    })
    command.stderr.on('data', line => console.error('desktop backend stderr', line))
    command.stdout.on('data', line => console.info('desktop backend stdout', line))
    window.__AGENTE_BACKEND_CHILD__ = await command.spawn()
    await waitForBackend()
  })()

  return window.__AGENTE_BACKEND_BOOT__
}
