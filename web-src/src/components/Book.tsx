import { useMemo } from 'react'
import type { Offer, Snapshot } from '../types'
import { btc, duration, fmt } from '../format'
import { Card, Pill } from './ui'

export function OrderBook({ snap, selected, onSelect }:
  { snap: Snapshot | null; selected: string | null; onSelect: (id: string) => void }) {
  const offers = snap?.book ?? []
  const market = snap?.market?.sats_per_mars ?? null
  const maxCum = useMemo(() => offers.reduce((a, o) => a + o.mars, 0) || 1, [offers])
  let cum = 0

  return (
    <Card className="book-card" title="Order book"
      right={<span className="muted">{offers.length} offer{offers.length === 1 ? '' : 's'} · all relays</span>}>
      <div className="book-cols"><span>Price <i>sats</i></span><span>Size <i>MARS</i></span><span>Total <i>BTC</i></span><span>Expires</span></div>
      <div className="book-rows">
        {offers.length === 0 && (
          <div className="empty">
            <div className="empty-title">No open offers right now</div>
            <div className="muted">Makers post offers to the Marscoin relays; they appear here the moment they land.
              Each one fills completely in a single atomic swap.</div>
          </div>
        )}
        {offers.map((o) => {
          cum += o.mars
          return <Row key={o.id} o={o} depth={cum / maxCum} selected={selected === o.id}
            onClick={() => !o.taken && onSelect(o.id)} />
        })}
      </div>
      <div className="book-mid">
        <span>Market</span>
        <span className="num">{market ? fmt(market, 2) : '—'}</span>
        <span className="muted">sats/MARS · offers are priced as a premium over it</span>
      </div>
    </Card>
  )
}

function Row({ o, depth, selected, onClick }: { o: Offer; depth: number; selected: boolean; onClick: () => void }) {
  const unsafe = o.safety.safe === false
  return (
    <button className={`book-row ${selected ? 'sel' : ''} ${o.taken ? 'taken' : ''} ${unsafe ? 'unsafe' : ''}`}
      onClick={onClick} disabled={o.taken}
      title={unsafe ? `A 4.4.2 wallet refuses this offer: ${o.safety.reason}` : `Offer ${o.id} on ${o.relays} relay(s)`}>
      <span className="depth" style={{ width: `${Math.max(4, depth * 100)}%` }} />
      <span className="ask num">{fmt(o.sats_per_mars, 2)}
        {o.premium_percent != null && <em> +{fmt(o.premium_percent, 1)}%</em>}</span>
      <span className="num">{fmt(o.mars, 0)}</span>
      <span className="num">{btc(o.btc)}</span>
      <span className="exp">
        {o.taken ? <Pill tone="btc">TAKEN</Pill>
          : unsafe ? <Pill tone="bad">UNSAFE</Pill>
          : <span className="num muted">{duration(o.expires_in, true)}</span>}
        {o.ours && <Pill tone="mars">OURS</Pill>}
      </span>
    </button>
  )
}

export function DepthChart({ snap }: { snap: Snapshot | null }) {
  const depth = snap?.depth ?? []
  const market = snap?.market?.sats_per_mars ?? null
  const W = 600, H = 150, P = 6
  const view = useMemo(() => {
    if (!depth.length || !market) return null
    const prices = depth.map((d) => d.sats_per_mars)
    const lo = Math.min(market, ...prices) * 0.98
    const hi = Math.max(market * 1.12, ...prices) * 1.02
    const top = depth[depth.length - 1].cumulative_mars * 1.15
    const x = (p: number) => P + ((p - lo) / (hi - lo)) * (W - 2 * P)
    const y = (q: number) => H - P - (q / top) * (H - 2 * P)
    let d = `M ${x(depth[0].sats_per_mars)} ${y(0)}`
    let prev = 0
    for (const pt of depth) { d += ` L ${x(pt.sats_per_mars)} ${y(prev)} L ${x(pt.sats_per_mars)} ${y(pt.cumulative_mars)}`; prev = pt.cumulative_mars }
    d += ` L ${W - P} ${y(prev)}`
    return { line: d, area: d + ` L ${W - P} ${y(0)} Z`, mx: x(market), top }
  }, [depth, market])

  return (
    <Card className="depth-card" title="Depth" right={<span className="muted">MARS available up to a price</span>}>
      {view ? (
        <svg viewBox={`0 0 ${W} ${H}`} className="depth-svg" preserveAspectRatio="none">
          <defs>
            <linearGradient id="depth-g" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0" stopColor="#ff6a3d" stopOpacity="0.45" />
              <stop offset="1" stopColor="#ff6a3d" stopOpacity="0.02" />
            </linearGradient>
          </defs>
          <path d={view.area} fill="url(#depth-g)" />
          <path d={view.line} fill="none" stroke="#ff6a3d" strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
          <line x1={view.mx} x2={view.mx} y1={P} y2={H - P} stroke="#9a99b0" strokeDasharray="3 4" vectorEffect="non-scaling-stroke" />
        </svg>
      ) : <div className="empty small muted">The depth chart fills in as offers arrive.</div>}
    </Card>
  )
}
