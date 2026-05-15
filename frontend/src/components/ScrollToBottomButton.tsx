import React from 'react'

type Props = {
  visible: boolean
  onClick: () => void
  label?: string
}

const ScrollToBottomButton: React.FC<Props> = ({ visible, onClick, label = 'Novas atualizacoes' }) => {
  if (!visible) return null
  return (
    <button
      type="button"
      onClick={onClick}
      className="absolute bottom-3 left-1/2 z-10 -translate-x-1/2 rounded-full border border-blue-500/40 bg-blue-600/90 px-4 py-2 text-xs font-medium text-white shadow-lg shadow-black/30 backdrop-blur hover:bg-blue-500"
    >
      {label} ↓
    </button>
  )
}

export default ScrollToBottomButton
