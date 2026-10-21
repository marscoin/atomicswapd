import { useEffect, useRef, useState } from 'react'
import type { History, Snapshot } from './types'

export type LinkState = 'connecting' | 'live' | 'reconnecting'

// Desktop app: the window opens at /#token=…; trade it for a session cookie
// once, keep the token in memory for actions, and drop it from the address.
let desktopToken: string | null = null
export const getDesktopToken = () => desktopToken

let sessionReady: Promise<void> | null = null

function desktopSession(): Promise<void> {
  // One handshake per page load, however often effects run.
  sessionReady ??= (async () => {
    const m = location.hash.match(/token=([0-9a-f]{32,})/)
    if (!m) return
    desktopToken = m[1]
    history.replaceState(null, '', location.pathname)
    await fetch('/api/session', { method: 'POST', headers: { 'X-Swap-Token': desktopToken } })
  })()
  return sessionReady
}

/** The daemon's snapshot, kept current over server-sent events. */
export function useSnapshot() {
  const [snap, setSnap] = useState<Snapshot | null>(null)
  const [link, setLink] = useState<LinkState>('connecting')
  // Server clock minus ours, so countdowns agree with the daemon.
  const skew = useRef(0)

  useEffect(() => {
    let es: EventSource | null = null
    let retry: number | undefined
    let closed = false
    const open = () => {
      es = new EventSource('/api/stream')
      es.addEventListener('snapshot', (ev) => {
        const s = JSON.parse((ev as MessageEvent).data) as Snapshot
        skew.current = s.now - Date.now() / 1000
        setSnap(s)
        setLink('live')
      })
      es.onerror = () => {
        es?.close()
        if (closed) return
        setLink('reconnecting')
        retry = window.setTimeout(open, 3000)
      }
    }
    desktopSession().catch(() => {}).finally(() => {
      if (closed) return
      // First paint without waiting for the stream
      fetch('/api/snapshot').then((r) => r.json()).then((s: Snapshot) => {
        skew.current = s.now - Date.now() / 1000
        setSnap((cur) => cur ?? s)
      }).catch(() => {})
      open()
    })
    return () => { closed = true; es?.close(); window.clearTimeout(retry) }
  }, [])

  return { snap, link, skew }
}

export function useHistory() {
  const [history, setHistory] = useState<History | null>(null)
  useEffect(() => {
    let timer: number | undefined
    let stopped = false
    // Right after the engine starts (always the case in the desktop app) the
    // history isn't fetched yet: retry soon until it arrives, then every 5 min.
    const load = async () => {
      let ready = false
      try {
        await desktopSession()
        const h: History = await (await fetch('/api/history', { cache: 'no-store' })).json()
        setHistory(h)
        ready = (h.mars_btc?.length ?? 0) > 0
      } catch { /* try again */ }
      if (!stopped) timer = window.setTimeout(load, ready ? 5 * 60_000 : 5_000)
    }
    load()
    return () => { stopped = true; window.clearTimeout(timer) }
  }, [])
  return history
}

/** Re-render every `ms`, for clocks and countdowns. */
export function useNow(ms = 1000, skewSec = 0) {
  const [now, setNow] = useState(() => Date.now() / 1000 + skewSec)
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now() / 1000 + skewSec), ms)
    return () => window.clearInterval(t)
  }, [ms, skewSec])
  return now
}

/** Briefly true whenever `value` changes, to flash updated numbers. */
export function useFlash(value: unknown, ms = 900) {
  const [flash, setFlash] = useState(false)
  const first = useRef(true)
  useEffect(() => {
    if (first.current) { first.current = false; return }
    setFlash(true)
    const t = window.setTimeout(() => setFlash(false), ms)
    return () => window.clearTimeout(t)
  }, [value, ms])
  return flash
}
