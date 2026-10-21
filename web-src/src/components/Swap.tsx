import { useEffect, useMemo, useState } from 'react'
import type { Offer, Snapshot } from '../types'
import { btc, duration, fmt, GUIDE_URL, int, usd, WALLET_URL } from '../format'
import { Card, Pill } from './ui'

const FUNDING_VBYTES = 150     // the buyer's BTC lock transaction, roughly
const MARS_CLAIM_FEE = 0.001   // Electrum-Mars's minimum fee on the MARS claim

function bestFit(offers: Offer[], budget: number): Offer | null {
  const ok = offers.filter((o) => !o.taken && o.safety.safe && o.btc <= budget + 1e-12)
  return ok.sort((a, b) => a.rate - b.rate || b.mars - a.mars)[0] ?? null
}

export function SwapBox({ snap, selected, onSelect }:
  { snap: Snapshot | null; selected: string | null; onSelect: (id: string | null) => void }) {
  const offers = snap?.book ?? []
  const open = offers.filter((o) => !o.taken && o.safety.safe)
  const [amount, setAmount] = useState('')
  const [touched, setTouched] = useState(false)   // until then, suggest the best offer
  const [howTo, setHowTo] = useState(false)

  const chosen = useMemo(() => {
    if (selected) return offers.find((o) => o.id === selected) ?? null
    const n = parseFloat(amount)
    if (n > 0) return bestFit(offers, n)
    return touched ? null : open.slice().sort((a, b) => a.rate - b.rate)[0] ?? null
  }, [selected, amount, offers, touched])

  // Clicking an offer in the book fills in its price.
  useEffect(() => {
    if (selected) {
      const o = offers.find((x) => x.id === selected)
      if (o) { setAmount(o.btc.toFixed(8)); setTouched(true) }
      else onSelect(null)
    }
  }, [selected])

  const m = snap?.market
  const t = snap?.timeline
  const typed = parseFloat(amount)
  const tooSmall = typed > 0 && !chosen && open.length > 0
  // Show what is actually paid: the chosen offer's exact price, unless the
  // buyer typed a budget (then a note shows the exact price within it).
  const shown = touched ? amount : chosen ? chosen.btc.toFixed(8) : ''
  const payBtc = touched ? typed : chosen?.btc
  const budgetDiffers = touched && chosen != null && Math.abs(typed - chosen.btc) > 1e-9
  const cheapest = open.slice().sort((a, b) => a.btc - b.btc)[0]
  const feeSats = snap?.chains.btc.fee_fast ? Math.round(snap.chains.btc.fee_fast * FUNDING_VBYTES) : null

  return (
    <Card className="swap-card" title="Swap" right={<Pill tone="dim">BTC → MARS</Pill>}>
      <div className="swap-field">
        <div className="swap-label"><span>You pay</span>
          {m?.btc_usd && payBtc ? <span className="muted">≈ {usd(payBtc * m.btc_usd)}</span> : null}</div>
        <div className="swap-input">
          <input inputMode="decimal" placeholder="0.00000000" value={shown}
            onChange={(e) => { onSelect(null); setTouched(true); setAmount(e.target.value.replace(/[^0-9.]/g, '')) }} />
          <span className="asset btc-asset"><i className="coin coin-btc" />BTC</span>
        </div>
        {budgetDiffers && chosen && <div className="budget-note">
          Best offer within this budget costs exactly <b className="num">{btc(chosen.btc)}</b> BTC. Offers fill completely.</div>}
        <div className="presets">
          {open.slice().sort((a, b) => a.btc - b.btc).slice(0, 4).map((o) => (
            <button key={o.id} onClick={() => onSelect(o.id)} className={chosen?.id === o.id ? 'on' : ''}>
              {fmt(o.mars, 0)} MARS
            </button>))}
        </div>
      </div>

      <div className="swap-arrow">↓</div>

      <div className="swap-field">
        <div className="swap-label"><span>You receive</span>
          {m?.mars_usd && chosen && <span className="muted">≈ {usd(chosen.mars * m.mars_usd)}</span>}</div>
        <div className="swap-input out">
          <span className="out-amount num">{chosen ? fmt(chosen.mars - MARS_CLAIM_FEE, 3) : '0.000'}</span>
          <span className="asset mars-asset"><i className="coin coin-mars" />MARS</span>
        </div>
      </div>

      {chosen ? (
        <div className="swap-details">
          <Line k="Offer"><span className="num">{chosen.id.slice(0, 8)}</span> · fills completely</Line>
          <Line k="Price"><span className="num">{fmt(chosen.sats_per_mars, 2)}</span> sats/MARS
            {chosen.premium_percent != null && <span className="muted"> · market +{fmt(chosen.premium_percent, 1)}%</span>}</Line>
          <Line k="You send exactly"><span className="num">{btc(chosen.btc)}</span> BTC</Line>
          <Line k="Network fees">{feeSats != null ? <>≈ <span className="num">{int(feeSats)}</span> sats on Bitcoin</> : '—'}
            <span className="muted"> + {MARS_CLAIM_FEE} MARS</span></Line>
          <Line k="Settles in">≈ 20–40 min <span className="muted">(2 Marscoin + 1 Bitcoin confirmations)</span></Line>
          <div className="checks">
            <Check ok>The seller locks the MARS first; your wallet verifies it before showing where to pay.</Check>
            <Check ok={!!chosen.safety.safe}>
              {chosen.safety.safe ? <>MARS refund opens <b>{duration(chosen.safety.margin_sec, true)}</b> after your BTC refund, even with slow Bitcoin blocks.</>
                : <>Unsafe: {chosen.safety.reason}</>}
            </Check>
            {t && <Check ok>If anything stalls, your BTC is refundable from Bitcoin block <b className="num">{int(t.btc_refund_height)}</b> (≈ 4 h).</Check>}
          </div>
        </div>
      ) : (
        <div className="swap-details empty-swap muted">
          {open.length === 0 ? 'No offers to take right now. They appear here as soon as a maker posts one.'
            : tooSmall ? <>The smallest offer is {btc(cheapest?.btc)} BTC; offers fill completely.</>
            : 'Enter an amount or pick an offer from the book.'}
        </div>
      )}

      <button className="cta" disabled={!chosen} onClick={() => setHowTo(true)}>
        {chosen ? `Swap for ${fmt(chosen.mars, 0)} MARS in Electrum-Mars` : 'Pick an offer'}
      </button>
      <div className="cta-note muted">Your keys stay in your wallet. This page never touches funds.</div>

      {howTo && chosen && <HowTo offer={chosen} onClose={() => setHowTo(false)} />}
    </Card>
  )
}

function Line({ k, children }: { k: string; children: React.ReactNode }) {
  return <div className="line"><span className="k">{k}</span><span className="v">{children}</span></div>
}

function Check({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return <div className={`check ${ok ? 'ok' : 'no'}`}><span>{ok ? '✓' : '!'}</span><div>{children}</div></div>
}

function HowTo({ offer, onClose }: { offer: Offer; onClose: () => void }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [onClose])
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <button className="modal-x" onClick={onClose} aria-label="Close">×</button>
        <h3>Take this offer in Electrum-Mars</h3>
        <p className="muted">Atomic swaps settle wallet to wallet. No account, no deposit, nobody holds your coins.</p>
        <ol className="steps">
          <li><b>Get Electrum-Mars 4.4.2 or later</b> — <a href={WALLET_URL} target="_blank" rel="noreferrer">download</a> for macOS, Windows or Linux.</li>
          <li>Open the <b>Atomic Swap</b> tab and find offer <code>{offer.id.slice(0, 8)}</code>: {fmt(offer.mars, 0)} MARS for {btc(offer.btc)} BTC. Click <b>Accept</b>.</li>
          <li>Wait a few minutes while the seller locks the MARS. Your wallet checks the lock on the Marscoin chain.</li>
          <li>Send <b>exactly {btc(offer.btc)} BTC</b> to the address your wallet shows, from any Bitcoin wallet.</li>
          <li>The seller claims the BTC, which reveals the secret; your wallet uses it to claim the MARS automatically.</li>
        </ol>
        <a className="cta small" href={GUIDE_URL} target="_blank" rel="noreferrer">Step-by-step guide</a>
      </div>
    </div>
  )
}
