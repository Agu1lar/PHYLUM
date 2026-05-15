import React, { useMemo } from 'react'
import { useStickToBottom } from '../hooks/useStickToBottom'
import { useStore } from '../state/store'
import ScrollToBottomButton from './ScrollToBottomButton'

const LogsPanel: React.FC = () => {
  const currentRun = useStore(state => (state.currentRunId ? state.runs[state.currentRunId] : null))
  const events = currentRun?.events ?? []
  const scrollKey = useMemo(() => `${events.length}:${events[events.length - 1]?.type ?? ''}`, [events])
  const { containerRef, endRef, showJumpButton, scrollToBottom, handleScroll } = useStickToBottom([scrollKey])

  return (
    <div className="flex min-h-0 flex-col rounded-xl bg-gray-900 p-4">
      <h3 className="shrink-0 font-semibold text-gray-100">Eventos da run</h3>
      <div className="relative mt-2">
        <div
          ref={containerRef}
          onScroll={handleScroll}
          className="chat-scroll h-72 overflow-y-auto scroll-smooth rounded-lg bg-black/25 p-2"
        >
          <div className="space-y-2">
            {events.length === 0 ? <div className="text-xs text-gray-500">Sem eventos ainda.</div> : null}
            {events.map((event, index) => (
              <div key={index} className="border-b border-gray-800 pb-2 text-xs text-gray-300">
                <div className="font-medium text-gray-200">{event.type}</div>
                <pre className="mt-1 whitespace-pre-wrap text-gray-400">{JSON.stringify(event.payload, null, 2)}</pre>
              </div>
            ))}
            <div ref={endRef} className="h-px" aria-hidden />
          </div>
        </div>
        <ScrollToBottomButton visible={showJumpButton} onClick={() => scrollToBottom('smooth')} label="Ver eventos recentes" />
      </div>
    </div>
  )
}
export default LogsPanel
