import React, { useEffect, useState } from 'react'
import ProviderCredentialsForm from './ProviderCredentialsForm'
import { getApiBase } from '../lib/runtimeConfig'
import { useStore } from '../state/store'

const SettingsPanel: React.FC = () => {
  const providers = useStore(state => state.providers)
  const [doctor, setDoctor] = useState<any | null>(null)
  const [onboarding, setOnboarding] = useState<any | null>(null)
  const API_BASE = getApiBase()

  useEffect(() => {
    const loadDiagnostics = async () => {
      try {
        const [doctorResponse, onboardingResponse] = await Promise.all([
          fetch(`${API_BASE}/diagnostics/doctor`),
          fetch(`${API_BASE}/onboarding/capabilities`),
        ])
        if (doctorResponse.ok) {
          setDoctor(await doctorResponse.json())
        }
        if (onboardingResponse.ok) {
          setOnboarding(await onboardingResponse.json())
        }
      } catch (error) {
        console.error(error)
      }
    }
    void loadDiagnostics()
  }, [API_BASE])

  return (
    <div className="space-y-4">
      <div className="rounded border border-gray-700 bg-gray-800 p-4">
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="mt-2 text-sm text-gray-400">
          As credenciais sao digitadas nesta UI, enviadas ao backend local e armazenadas com protecao do sistema.
          Depois de salvar, a chave nao retorna ao frontend.
        </p>
      </div>
      {doctor ? (
        <div className="rounded border border-gray-700 bg-gray-800 p-4">
          <h2 className="text-lg font-semibold text-white">Doctor</h2>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <div className="rounded border border-gray-700 bg-gray-900/40 p-3 text-sm text-gray-300">
              <div>Python: {doctor.capabilities?.python}</div>
              <div>Plataforma: {doctor.capabilities?.platform}</div>
              <div>Admin: {doctor.capabilities?.is_admin ? 'sim' : 'nao'}</div>
              <div>Provedores configurados: {doctor.capabilities?.providers_configured ?? 0}</div>
            </div>
            <div className="rounded border border-gray-700 bg-gray-900/40 p-3 text-sm text-gray-300">
              <div>Playwright: {doctor.capabilities?.dependencies?.playwright ? 'ok' : 'faltando'}</div>
              <div>UIA (`pywinauto`): {doctor.capabilities?.dependencies?.pywinauto ? 'ok' : 'faltando'}</div>
              <div>Office COM: {doctor.capabilities?.dependencies?.win32com ? 'ok' : 'faltando'}</div>
              <div>PDF parser: {doctor.capabilities?.dependencies?.pypdf ? 'ok' : 'faltando'}</div>
            </div>
          </div>
          {doctor.warnings?.length ? (
            <ul className="mt-3 space-y-2 text-sm text-amber-200">
              {doctor.warnings.map((warning: string) => (
                <li key={warning} className="rounded border border-amber-800 bg-amber-950/30 p-2">
                  {warning}
                </li>
              ))}
            </ul>
          ) : (
            <div className="mt-3 rounded border border-emerald-800 bg-emerald-950/20 p-3 text-sm text-emerald-200">
              Ambiente pronto para operacao agentic mais robusta.
            </div>
          )}
        </div>
      ) : null}
      {onboarding?.steps?.length ? (
        <div className="rounded border border-gray-700 bg-gray-800 p-4">
          <h2 className="text-lg font-semibold text-white">Onboarding de capacidades</h2>
          <div className="mt-3 grid gap-2">
            {onboarding.steps.map((step: any) => (
              <div
                key={step.id}
                className={`rounded border p-3 text-sm ${step.ready ? 'border-emerald-700 bg-emerald-950/20 text-emerald-200' : 'border-gray-700 bg-gray-900/40 text-gray-300'}`}
              >
                {step.ready ? 'Pronto' : 'Pendente'}: {step.label}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {providers.map(provider => (
        <ProviderCredentialsForm key={provider.provider} provider={provider} />
      ))}
    </div>
  )
}

export default SettingsPanel
