import { useCallback, useLayoutEffect, useRef, useState } from 'react'

const NEAR_BOTTOM_PX = 96

function nearBottom(el: HTMLElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR_BOTTOM_PX
}

/**
 * Keeps a scroll container pinned to the bottom while the user is already
 * following the stream. If they scroll up to read history, auto-scroll pauses
 * until they click "scroll to bottom" or return near the end.
 */
export function useStickToBottom(deps: unknown[], options?: { enabled?: boolean }) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const endRef = useRef<HTMLDivElement | null>(null)
  const stickRef = useRef(true)
  const [showJumpButton, setShowJumpButton] = useState(false)

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    const viewport = containerRef.current
    if (!viewport) return
    stickRef.current = true
    setShowJumpButton(false)
    if (behavior === 'smooth' && endRef.current) {
      endRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' })
      return
    }
    viewport.scrollTop = viewport.scrollHeight
  }, [])

  const handleScroll = useCallback(() => {
    const viewport = containerRef.current
    if (!viewport) return
    const pinned = nearBottom(viewport)
    stickRef.current = pinned
    setShowJumpButton(!pinned)
  }, [])

  useLayoutEffect(() => {
    if (options?.enabled === false) return
    const viewport = containerRef.current
    if (!viewport) return
    if (stickRef.current || nearBottom(viewport)) {
      viewport.scrollTop = viewport.scrollHeight
      stickRef.current = true
      setShowJumpButton(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- deps array is caller-controlled
  }, deps)

  return {
    containerRef,
    endRef,
    showJumpButton,
    scrollToBottom,
    handleScroll,
  }
}
