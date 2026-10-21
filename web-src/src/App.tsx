import { useState } from 'react'
import { useHistory, useNow, useSnapshot } from './live'
import { PAPER_URL, REPO_URL, WALLET_URL } from './format'
import { PriceChart, TopBar } from './components/Market'
import { DepthChart, OrderBook } from './components/Book'
import { SwapBox } from './components/Swap'
import { MakerPanel, SwapsTable } from './components/Maker'
import { Activity, LightTime, Relays, Timeline } from './components/Network'

export default function App() {
  const { snap, link, skew } = useSnapshot()
  const history = useHistory()
  const now = useNow(1000, skew.current)
  const [selected, setSelected] = useState<string | null>(null)

  return (
    <div className="app">
      <TopBar snap={snap} link={link} now={now} />
      <main className="grid">
        <div className="col-chart">
          <PriceChart history={history} snap={snap} />
          <Timeline snap={snap} now={now} />
        </div>
        <div className="col-book">
          <OrderBook snap={snap} selected={selected} onSelect={setSelected} />
          <DepthChart snap={snap} />
        </div>
        <div className="col-swap">
          <SwapBox snap={snap} selected={selected} onSelect={setSelected} />
          <MakerPanel snap={snap} />
        </div>
        <div className="row-bottom">
          <div className="stack">
            <SwapsTable snap={snap} now={now} />
            <Relays snap={snap} />
          </div>
          <Activity snap={snap} now={now} />
          <LightTime now={now} />
        </div>
      </main>
      <footer className="foot">
        <span>atomicswapd {snap?.version ?? ''} · read-only dashboard · no keys reachable from this page</span>
        <span>
          <a href={REPO_URL} target="_blank" rel="noreferrer">Source</a>
          <a href={WALLET_URL} target="_blank" rel="noreferrer">Electrum-Mars</a>
          <a href={PAPER_URL} target="_blank" rel="noreferrer">InterPlanetary Atomic Swaps</a>
        </span>
      </footer>
    </div>
  )
}
