import { useMemo } from 'react'
import type { Snapshot } from '../types'
import { ago, duration, fmt, int, PAPER_URL } from '../format'
import { orbits } from '../orbits'
import { Card, Dot, Pill } from './ui'

export function Timeline({ snap, now }: { snap: Snapshot | null; now: number }) {
  const t = snap?.timeline
  const light = orbits(now).lightSec
  if (!t) return <Card title="How a swap settles"><div className="empty small muted">Waiting for both chains…</div></Card>
  const end = t.mars_refund_sec * 1.04
  const x = (s: number) => `${(s / end) * 100}%`
  const steps = [
    { at: 4 * 60, label: 'Seller locks MARS', sub: '2 Marscoin blocks' },
    { at: 15 * 60, label: 'You pay BTC', sub: 'wallet verified the lock' },
    { at: 30 * 60, label: 'Seller claims BTC', sub: 'reveals the secret' },
    { at: 34 * 60, label: 'You claim MARS', sub: 'with that secret' },
  ]
  return (
    <Card className="timeline-card" title="How a swap settles"
      right={<span className="muted">a swap starting now · real block heights</span>}>
      <div className="tl">
        <div className="tl-steps">
          {steps.map((s, i) => (
            <div key={i} className="tl-step">
              <span className="tl-n">{i + 1}</span>
              <div><b>{s.label}</b><span>{s.sub}</span><em>~{Math.round(s.at / 60)} min</em></div>
            </div>))}
        </div>
        <div className="tl-caption muted">A normal swap finishes in about half an hour. The refund clocks below only matter if someone stops halfway.</div>
        <div className="tl-track">
          <div className="tl-pay" style={{ left: 0, width: x(t.pay_window_sec) }} title="Pay within this window" />
          <div className="tl-btc-slow" style={{ left: x(t.btc_refund_sec), width: x(t.btc_refund_sec_slow - t.btc_refund_sec) }} />
          <div className="tl-margin" style={{ left: x(t.btc_refund_sec_slow), width: x(t.margin_sec) }}>
            <span>safety margin {duration(t.margin_sec, true)}</span>
          </div>
          <div className="tl-mars-fast" style={{ left: x(t.mars_refund_sec_fast), width: x(t.mars_refund_sec - t.mars_refund_sec_fast) }} />
          <div className="tl-mark btc" style={{ left: x(t.btc_refund_sec) }} />
          <div className="tl-mark mars" style={{ left: x(t.mars_refund_sec) }} />
          <div className="tl-light" style={{ width: x(light) }} title="Earth–Mars light time, one way" />
          <div className="tl-now" />
        </div>
        <div className="tl-labels">
          <div style={{ left: 0 }}><b>Now</b><span className="num">BTC {int(t.btc_height)} · MARS {int(t.mars_height)}</span></div>
          <div style={{ left: x(t.btc_refund_sec) }} className="btc"><b>Buyer can refund BTC</b><span className="num">block {int(t.btc_refund_height)} · ≈ {duration(t.btc_refund_sec, true)}</span></div>
          <div style={{ left: x(t.mars_refund_sec) }} className="mars right"><b>Seller can refund MARS</b><span className="num">block {int(t.mars_refund_height)} · ≈ {duration(t.mars_refund_sec, true)}</span></div>
        </div>
      </div>
      <p className="tl-note muted">
        The buyer pays second, so the buyer's refund opens first. Even if Bitcoin blocks run 25% slow and Marscoin blocks
        15% fast, the seller can't take the MARS back until <b>{duration(t.margin_sec, true)}</b> after the buyer could
        recover the BTC (wallets demand at least {duration(t.min_margin_sec, true)}). The thin bar at the start is
        today's light time from Earth to Mars: {duration(light, true)}.
      </p>
    </Card>
  )
}

export function LightTime({ now }: { now: number }) {
  const o = useMemo(() => orbits(now), [Math.floor(now / 60)])
  const S = 74 / 1.7   // px per AU
  const p = (v: [number, number, number]) => [90 + v[0] * S, 90 - v[1] * S]
  const [ex, ey] = p(o.earth)
  const [mx, my] = p(o.mars)
  return (
    <Card className="light-card" title="Earth ⇄ Mars" right={<a className="muted" href={PAPER_URL} target="_blank" rel="noreferrer">why it matters</a>}>
      <div className="light">
        <svg viewBox="0 0 180 180" width="180" height="180">
          <defs>
            <radialGradient id="sun-g"><stop offset="0" stopColor="#fff3c4" /><stop offset="0.5" stopColor="#ffc24b" /><stop offset="1" stopColor="#ff8a1a" stopOpacity="0" /></radialGradient>
          </defs>
          <circle cx="90" cy="90" r={1.0 * S} className="orbit" />
          <circle cx="90" cy="90" r={1.524 * S} className="orbit" />
          <circle cx="90" cy="90" r="9" fill="url(#sun-g)" />
          <line x1={ex} y1={ey} x2={mx} y2={my} className="beam" />
          <circle cx={ex} cy={ey} r="4.5" fill="#4aa8ff" />
          <circle cx={mx} cy={my} r="4" fill="#ff6a3d" />
          <text x={ex + 7} y={ey + 3} className="pl">Earth</text>
          <text x={mx + 7} y={my + 3} className="pl">Mars</text>
        </svg>
        <div className="light-stats">
          <div><b className="num">{duration(o.lightSec)}</b><span>one way, at light speed</span></div>
          <div><b className="num">{fmt(o.km / 1e6, 1)}M km</b><span>{fmt(o.au, 3)} AU apart</span></div>
          <div><b className="num">{duration(2 * o.lightSec, true)}</b><span>round trip</span></div>
        </div>
      </div>
      <p className="muted small">A Mars settlement can't ask Earth and wait for an answer. Atomic swaps don't need to: each side checks its own chain, and the refund windows are hours, not minutes.</p>
    </Card>
  )
}

export function Relays({ snap }: { snap: Snapshot | null }) {
  return (
    <Card className="relays-card" title="Order-book relays" right={<span className="muted">Marscoin ElectrumX</span>}>
      <div className="relays">
        {(snap?.relays ?? []).map((r) => (
          <div key={r.host} className={`relay ${r.connected ? '' : 'down'}`}>
            <Dot tone={r.connected ? 'good' : 'bad'} />
            <div className="relay-main">
              <div className="relay-top">
                <span className="relay-host">{r.host}</span>
                {r.connected && r.latency_ms != null && <span className="num muted small">{Math.round(r.latency_ms)} ms</span>}
              </div>
              <div className="muted small">{r.connected ? `${r.version ?? ''} · block ${int(r.tip_height)}` : r.last_error ?? 'offline'}</div>
            </div>
            <div className="relay-tags">
              <Pill tone={r.tls_verified ? 'good' : 'dim'} title={r.tls_verified ? 'TLS certificate verified' : 'Self-signed certificate'}>
                {r.tls_verified ? 'TLS ✓' : 'self-signed'}</Pill>
              {r.connected && <Pill tone={r.abuse_controls ? 'good' : 'dim'}
                title={r.abuse_controls ? 'First accept wins, per-client limits, abandoned-swap reports' : 'Older relay without abuse controls'}>
                {r.abuse_controls ? 'abuse controls' : 'older relay'}</Pill>}
            </div>
          </div>))}
      </div>
    </Card>
  )
}

const ICON: Record<string, string> = {
  mars_block: '◉', btc_block: '₿', offer: '+', offer_gone: '−', taken: '⚡', swap: '⇄', maker: '◆',
}

export function Activity({ snap, now }: { snap: Snapshot | null; now: number }) {
  const events = (snap?.events ?? []).slice().reverse().slice(0, 30)
  return (
    <Card className="activity-card" title="Live activity" right={<span className="muted">since {snap ? ago(snap.started_at, now) : '—'}</span>}>
      <div className="activity">
        {events.length === 0 && <div className="muted small">Listening to both chains and the relays…</div>}
        {events.map((e) => (
          <div key={`${e.t}-${e.text}`} className={`ev ev-${e.kind}`}>
            <span className="ev-i">{ICON[e.kind] ?? '•'}</span>
            <span className="ev-t">{e.text}</span>
            <span className="ev-ago muted">{ago(e.t, now)}</span>
          </div>))}
      </div>
    </Card>
  )
}
