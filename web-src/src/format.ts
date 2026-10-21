export const MARS_TX = (txid: string) => `https://explore.marscoin.org/tx/${txid}`
export const BTC_TX = (txid: string) => `https://mempool.space/tx/${txid}`
export const WALLET_URL = 'https://github.com/marscoin/electrum-mars/releases/latest'
export const REPO_URL = 'https://github.com/marscoin/atomicswapd'
export const GUIDE_URL = 'https://marscoin.org/academy/buying-mars-with-btc'
export const PAPER_URL = 'https://marscoin.org/academy/ipas'

export const fmt = (n: number | null | undefined, digits = 2) =>
  n == null || !isFinite(n) ? '—'
    : n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })

export const int = (n: number | null | undefined) =>
  n == null ? '—' : Math.round(n).toLocaleString('en-US')

export const btc = (n: number | null | undefined) => (n == null ? '—' : n.toFixed(8))

export const usd = (n: number | null | undefined, digits = 2) =>
  n == null ? '—' : '$' + fmt(n, digits)

export function duration(sec: number | null | undefined, short = false): string {
  if (sec == null || !isFinite(sec)) return '—'
  const s = Math.max(0, Math.round(sec))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const r = s % 60
  if (h) return short ? `${h}h ${m}m` : `${h}h ${String(m).padStart(2, '0')}m`
  if (m) return short ? `${m}m` : `${m}m ${String(r).padStart(2, '0')}s`
  return `${r}s`
}

export function ago(t: number | null | undefined, now: number): string {
  if (t == null) return '—'
  const d = Math.max(0, now - t)
  if (d < 60) return `${Math.round(d)}s ago`
  if (d < 3600) return `${Math.floor(d / 60)}m ago`
  return `${Math.floor(d / 3600)}h ${Math.floor((d % 3600) / 60)}m ago`
}

export const short = (s: string | null | undefined, head = 8, tail = 6) =>
  !s ? '—' : s.length <= head + tail + 1 ? s : `${s.slice(0, head)}…${s.slice(-tail)}`

export const STATE_LABEL: Record<string, string> = {
  created: 'Accepted', mars_locked: 'MARS locked', btc_locked: 'BTC locked',
  btc_claimed: 'Settled', completed: 'Settled', mars_refunded: 'Refunded',
  btc_refunded: 'Refunded', failed: 'Failed', expired: 'Expired',
}
