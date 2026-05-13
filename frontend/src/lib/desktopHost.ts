import { getApiBase, getDesktopConnectionMode, getWsUrl, isDesktopApp } from './runtimeConfig'

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

function resolveSidecarLaunch() {
  const apiBase = getApiBase()
  const url = new URL(apiBase)
  const port = Number(url.port || (url.protocol === 'https:' ? '443' : '80'))
  const host = url.hostname
  const bindHost = host === 'localhost' || host === '127.0.0.1' ? host : '0.0.0.0'
  const allowLan = bindHost === '0.0.0.0'
  return {
    apiBase,
    wsUrl: getWsUrl(),
    port,
    bindHost,
    allowLan,
  }
}

export async function ensureDesktopBackend() {
  if (!isDesktopApp()) {
    return
  }

  const mode = getDesktopConnectionMode()
  if (mode === 'remote-backend') {
    if (window.__AGENTE_BACKEND_CHILD__?.kill) {
      try {
        await window.__AGENTE_BACKEND_CHILD__.kill()
      } catch (error) {
        console.error('failed to stop local backend child', error)
      }
      window.__AGENTE_BACKEND_CHILD__ = null
      window.__AGENTE_BACKEND_BOOT__ = undefined
    }
    window.__AGENTE_API_BASE__ = getApiBase()
    window.__AGENTE_WS_URL__ = getWsUrl()
    await waitForBackend(20, 250)
    return
  }

  const launch = resolveSidecarLaunch()

  try {
    await waitForBackend(2, 50)
    return
  } catch (error) {
    // Sidecar is not up yet, continue with startup below.
  }

  if (window.__AGENTE_BACKEND_BOOT__) {
    return window.__AGENTE_BACKEND_BOOT__
  }

  window.__AGENTE_API_BASE__ = launch.apiBase
  window.__AGENTE_WS_URL__ = launch.wsUrl

  window.__AGENTE_BACKEND_BOOT__ = (async () => {
    const { Command } = await import('@tauri-apps/plugin-shell')
    const args = ['--host', launch.bindHost, '--port', String(launch.port)]
    if (launch.allowLan) {
      args.push('--allow-lan', '--public-base-url', launch.apiBase)
    }
    const command = Command.sidecar('agente-backend', args)
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
