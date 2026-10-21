import { useEffect, useMemo, useRef, useState } from 'react'
import { AreaSeries, ColorType, createChart, LineStyle, type IChartApi, type ISeriesApi,
  type UTCTimestamp } from 'lightweight-charts'
import type { History, Snapshot } from '../types'
import type { LinkState } from '../live'
import { fmt, int, usd, duration } from '../format'
import { orbits } from '../orbits'
import { Card, Dot, MarsLogo, Num, Pill } from './ui'

export function TopBar({ snap, link, now }: { snap: Snapshot | null; link: LinkState; now: number }) {
  const m = snap?.market
  const light = orbits(now).lightSec
  return (
    <header className="topbar">
      <div className="brand">
        <MarsLogo />
        <div>
          <div className="brand-pair">MARS <span className="swap-glyph">⇄</span> BTC</div>
          <div className="brand-sub">Atomic swaps · no custody · no exchange</div>
        </div>
      </div>
      <div className="ticker">
        <Tick label="Price"><Num value={m?.sats_per_mars} format={(n) => fmt(n, 2)} className="big" /> <span className="unit">sats</span></Tick>
        <Tick label="MARS / USD"><Num value={m?.mars_usd} format={(n) => usd(n, 4)} /></Tick>
        <Tick label="BTC / USD"><Num value={m?.btc_usd} format={(n) => usd(n, 0)} /></Tick>
        <Tick label="Marscoin block"><Num value={snap?.chains.mars.height} format={int} className="mars" /></Tick>
        <Tick label="Bitcoin block"><Num value={snap?.chains.btc.height} format={int} className="btc" /></Tick>
        <Tick label="Earth → Mars light"><span className="num light-tick">{duration(light)}</span></Tick>
      </div>
      <div className="topbar-right">
        {snap?.setup?.stage === 'syncing' && (
          <span className="syncing" title="The maker's node is downloading and verifying Marscoin headers">
            <span className="spinner" />Syncing Marscoin {snap.setup.progress != null && snap.setup.progress < 100
              ? `${fmt(snap.setup.progress, 1)}%` : 'wallet'}
          </span>
        )}
        <Pill tone={snap?.mode === 'live' ? 'live' : 'warn'}
          title={snap?.mode === 'live' ? 'The maker posts offers and settles swaps'
            : 'Real data; the maker posts nothing yet'}>
          {snap?.mode === 'live' ? 'LIVE' : 'WATCH'}
        </Pill>
        <span className={`link link-${link}`}>
          <Dot tone={link === 'live' ? 'good' : link === 'connecting' ? 'dim' : 'warn'} />
          {link === 'live' ? 'Streaming' : link === 'connecting' ? 'Connecting' : 'Reconnecting'}
        </span>
      </div>
    </header>
  )
}

function Tick({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="tick"><div className="tick-label">{label}</div><div className="tick-value">{children}</div></div>
}

const RANGES = { '24H': 86400, '7D': 7 * 86400 } as const

export function PriceChart({ history, snap }: { history: History | null; snap: Snapshot | null }) {
  const box = useRef<HTMLDivElement>(null)
  const chart = useRef<IChartApi | null>(null)
  const series = useRef<ISeriesApi<'Area'> | null>(null)
  const [range, setRange] = useState<keyof typeof RANGES>('7D')

  const points = useMemo(() => {
    const raw = history?.mars_btc ?? []
    const out = raw.map(([t, p]) => ({ time: t as UTCTimestamp, value: p * 1e8 }))
    const live = snap?.market?.sats_per_mars
    if (live && snap) out.push({ time: Math.floor(snap.now) as UTCTimestamp, value: live })
    const dedup: typeof out = []
    for (const p of out) {
      if (dedup.length && p.time <= dedup[dedup.length - 1].time) continue
      dedup.push(p)
    }
    return dedup
  }, [history, snap?.market?.sats_per_mars, snap?.now])

  useEffect(() => {
    if (!box.current) return
    const c = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#8b8aa0',
        fontFamily: 'JetBrains Mono, ui-monospace, monospace', fontSize: 11, attributionLogo: false },
      grid: { vertLines: { color: 'rgba(255,255,255,0.03)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.2, bottom: 0.08 } },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
      crosshair: { vertLine: { color: 'rgba(255,120,80,0.4)', style: LineStyle.Dashed },
        horzLine: { color: 'rgba(255,120,80,0.4)', style: LineStyle.Dashed } },
      handleScale: false, handleScroll: false,
    })
    series.current = c.addSeries(AreaSeries, {
      lineColor: '#ff6a3d', lineWidth: 2, topColor: 'rgba(255,106,61,0.35)',
      bottomColor: 'rgba(255,106,61,0.0)', priceLineColor: '#ff6a3d',
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    })
    chart.current = c
    return () => { c.remove(); chart.current = null; series.current = null }
  }, [])

  useEffect(() => {
    if (!series.current || !chart.current || !points.length) return
    series.current.setData(points)
    const last = points[points.length - 1].time as number
    chart.current.timeScale().setVisibleRange({
      from: (last - RANGES[range]) as UTCTimestamp, to: last as UTCTimestamp })
  }, [points, range])

  const change = useMemo(() => {
    if (points.length < 2) return null
    const last = points[points.length - 1]
    const start = points.find((p) => (p.time as number) >= (last.time as number) - RANGES[range]) ?? points[0]
    return (last.value / start.value - 1) * 100
  }, [points, range])

  return (
    <Card className="chart-card" title={<>MARS / BTC <span className="muted">sats per MARS</span></>}
      right={<div className="seg">
        {(Object.keys(RANGES) as (keyof typeof RANGES)[]).map((r) =>
          <button key={r} className={r === range ? 'on' : ''} onClick={() => setRange(r)}>{r}</button>)}
      </div>}>
      <div className="chart-head">
        <Num value={snap?.market?.sats_per_mars} format={(n) => fmt(n, 2)} className="chart-price" />
        {change != null && <span className={`chg ${change >= 0 ? 'up' : 'down'}`}>
          {change >= 0 ? '▲' : '▼'} {fmt(Math.abs(change), 2)}% <span className="muted">{range}</span></span>}
        <span className="muted chart-src">market price · CoinMarketCap via price.marscoin.org, history CoinGecko</span>
      </div>
      <div ref={box} className="chart-box" />
    </Card>
  )
}
