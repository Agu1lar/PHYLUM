import React from 'react'
import ProviderCredentialsForm from './ProviderCredentialsForm'
import { useStore } from '../state/store'

const SettingsPanel: React.FC = () => {
  const providers = useStore(state => state.providers)

  return (
    <div className="space-y-4">
      <div className="rounded border border-gray-700 bg-gray-800 p-4">
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="mt-2 text-sm text-gray-400">
          As credenciais sao digitadas nesta UI, enviadas ao backend local e armazenadas com protecao do sistema.
          Depois de salvar, a chave nao retorna ao frontend.
        </p>
      </div>
      {providers.map(provider => (
        <ProviderCredentialsForm key={provider.provider} provider={provider} />
      ))}
    </div>
  )
}

export default SettingsPanel
