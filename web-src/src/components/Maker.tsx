import { useState } from 'react'
import type { Snapshot } from '../types'
import { ago, btc, BTC_TX, fmt, int, MARS_TX, short, STATE_LABEL, duration } from '../format'
import { Card, Pill } from './ui'

export function MakerPanel({ snap }: { snap: Snapshot | null }) {
  const mk = snap?.maker
  const b = mk?.balance
  const node = mk?.node
  const live = mk?.mode === 'live'
  const setup = snap?.setup
  return (
    <Card className="maker-card" title="This maker"
      right={<Pill tone={live ? 'live' : 'warn'}>{live ? 'LIVE' : 'WATCH'}</Pill>}>
      {setup && setup.stage !== 'live' && (
        <div className="next-step">
          <span className="next-stage">{setup.stage.replace(/_/g, ' ')}</span>
          <span>{setup.next}</span>
        </div>
      )}
      {!node?.wallet_ready ? (
        <div className="maker-setup">
          <div className="setup-title">{snap ? 'Wallet not ready' : 'Loading…'}</div>
          <div className="muted">{node?.wallet_error ?? 'Starting the Electrum-Mars node.'}</div>
          <Sync node={node} />
        </div>
      ) : (
        <>
          <Gauge snap={snap!} />
          <div className="maker-stats">
            <MiniStat k="Inventory" v={`${fmt(b?.inventory, 0)} MARS`} />
            <MiniStat k="In swaps" v={`${fmt(b?.locked, 0)} MARS`} />
            <MiniStat k="On offer" v={`${fmt(b?.on_offer, 0)} MARS`} />
            <MiniStat k="Sold today" v={`${fmt(b?.sold_today, 0)} MARS`} />
          </div>
          <Sync node={node} />
          <Offers snap={snap!} />
          {mk?.totals && (
            <div className="totals">
              <div><b className="num">{int(mk.totals.completed)}</b><span>swaps settled</span></div>
              <div><b className="num">{fmt(mk.totals.mars_sold, 0)}</b><span>MARS sold</span></div>
              <div><b className="num">{btc(mk.totals.btc_earned)}</b><span>BTC received</span></div>
              <div><b className="num">{int(mk.totals.refunded)}</b><span>refunded</span></div>
            </div>
          )}
          {b?.deposit_address && <Deposit address={b.deposit_address} />}
        </>
      )}
      <div className="rules">
        <span>Spread from <b>{mk?.fee_percent ?? '—'}%</b></span>
        <span>Exposure cap <b>{mk?.cap_percent ?? '—'}%</b></span>
        <span>Reserve <b>{mk?.reserve_percent ?? '—'}%</b></span>
        <span><b>{mk?.max_pending_per_taker ?? 1}</b> swap per buyer</span>
      </div>
      {mk?.error && <div className="warnbox">{mk.error}</div>}
    </Card>
  )
}

function MiniStat({ k, v }: { k: string; v: string }) {
  return <div className="mini"><span>{k}</span><b className="num">{v}</b></div>
}

function Sync({ node }: { node: Snapshot['maker']['node'] | undefined }) {
  if (!node || node.local_height == null || !node.server_height) return null
  const pct = Math.min(100, (node.local_height / node.server_height) * 100)
  if (pct >= 99.99 && node.wallet_synced !== false) return null
  return (
    <div className="sync">
      <div className="sync-label"><span>Syncing Marscoin headers</span><span className="num">{int(node.local_height)} / {int(node.server_height)}</span></div>
      <div className="bar"><div style={{ width: `${pct}%` }} /></div>
    </div>
  )
}

function Gauge({ snap }: { snap: Snapshot }) {
  const b = snap.maker.balance!
  const inv = b.inventory || 1
  const R = 52, C = 2 * Math.PI * R
  const locked = (b.locked / inv) * C
  const offered = (b.on_offer / inv) * C
  const cap = snap.maker.cap_percent / 100
  const capAngle = cap * 2 * Math.PI - Math.PI / 2
  return (
    <div className="gauge">
      <svg viewBox="0 0 140 140" width="140" height="140">
        <circle cx="70" cy="70" r={R} className="g-track" />
        {locked > 0.5 && <circle cx="70" cy="70" r={R} className="g-locked" strokeDasharray={`${locked} ${C}`} transform="rotate(-90 70 70)" />}
        {offered > 0.5 && <circle cx="70" cy="70" r={R} className="g-offer" strokeDasharray={`${offered} ${C}`}
          strokeDashoffset={-locked} transform="rotate(-90 70 70)" />}
        <line x1={70 + (R - 11) * Math.cos(capAngle)} y1={70 + (R - 11) * Math.sin(capAngle)}
          x2={70 + (R + 11) * Math.cos(capAngle)} y2={70 + (R + 11) * Math.sin(capAngle)} className="g-cap" />
      </svg>
      <div className="g-center">
        <b className="num">{fmt(b.exposure_percent, 0)}%</b>
        <span>exposed</span>
        <span className="muted">cap {snap.maker.cap_percent}%</span>
      </div>
      <div className="g-legend">
        <span><i className="lg-locked" />In swaps</span>
        <span><i className="lg-offer" />On offer</span>
        <span><i className="lg-free" />Idle</span>
      </div>
    </div>
  )
}

function Offers({ snap }: { snap: Snapshot }) {
  const mk = snap.maker
  const live = mk.mode === 'live'
  const rows = live ? mk.offers.map((o) => ({ key: o.id, mars: o.mars, btc: o.btc, rate: o.rate, note: duration(o.expires_in, true) }))
    : mk.plan.map((p) => ({ key: `t${p.tier}`, mars: p.mars, btc: p.btc, rate: p.rate, note: `+${fmt(p.spread_percent, 1)}%` }))
  return (
    <div className="maker-offers">
      <div className="sub-head">{live ? 'Posted offers' : 'Would post now'}</div>
      {rows.length === 0 ? <div className="muted small">
        {live ? 'No offers posted right now.' : 'Nothing to offer: fund the wallet or wait for a fresh price.'}</div>
        : rows.map((r) => (
          <div className="mo-row" key={r.key}>
            <span className="num">{fmt(r.mars, 0)} MARS</span>
            <span className="num muted">{btc(r.btc)} BTC</span>
            <span className="num">{fmt(r.rate * 1e8, 2)}</span>
            <span className="muted num">{r.note}</span>
          </div>))}
    </div>
  )
}

function Deposit({ address }: { address: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button className="deposit" onClick={() => { navigator.clipboard?.writeText(address); setCopied(true); setTimeout(() => setCopied(false), 1500) }}
      title="The maker's Marscoin address">
      <span className="muted">Maker wallet</span>
      <code>{short(address, 12, 8)}</code>
      <span className="muted">{copied ? 'copied' : 'copy'}</span>
    </button>
  )
}

export function SwapsTable({ snap, now }: { snap: Snapshot | null; now: number }) {
  const swaps = snap?.maker.swaps ?? []
  return (
    <Card className="swaps-card" title="Swaps by this maker" right={<span className="muted">each one is two on-chain contracts</span>}>
      {swaps.length === 0 ? <div className="empty small muted">No swaps yet. When a buyer takes an offer, it appears here with links to every transaction on both chains.</div> : (
        <table className="swaps">
          <thead><tr><th>Swap</th><th>State</th><th>MARS</th><th>BTC</th><th>Proof</th><th>Updated</th></tr></thead>
          <tbody>
            {swaps.map((s) => (
              <tr key={s.id}>
                <td className="num">{s.id}</td>
                <td><span className={`state st-${s.state}`}>{STATE_LABEL[s.state] ?? s.state}</span></td>
                <td className="num">{fmt(s.mars, 0)}</td>
                <td className="num">{btc(s.btc)}</td>
                <td><div className="proof">
                  {s.mars_funding_txid && <a href={MARS_TX(s.mars_funding_txid)} target="_blank" rel="noreferrer" title="MARS locked">MARS lock</a>}
                  {s.btc_funding_txid && <a href={BTC_TX(s.btc_funding_txid)} target="_blank" rel="noreferrer" title="BTC locked">BTC lock</a>}
                  {s.btc_claim_txid && <a href={BTC_TX(s.btc_claim_txid)} target="_blank" rel="noreferrer" title="BTC claimed, secret revealed">BTC claim</a>}
                  {s.mars_claim_txid && <a href={MARS_TX(s.mars_claim_txid)} target="_blank" rel="noreferrer" title="MARS claimed by the buyer">MARS claim</a>}
                  {!s.mars_funding_txid && <span className="muted small">pending</span>}
                </div></td>
                <td className="muted">{ago(s.updated_at, now)}</td>
              </tr>))}
          </tbody>
        </table>
      )}
    </Card>
  )
}
