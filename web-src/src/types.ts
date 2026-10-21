export interface Safety { safe: boolean | null; margin_sec: number | null; reason: string | null }

export interface Offer {
  id: string; mars: number; btc: number; rate: number; sats_per_mars: number
  premium_percent: number | null; expires_in: number; maker: string; relays: number
  taken: boolean; ours: boolean; mars_locktime: number; safety: Safety
}

export interface Relay {
  host: string; tls_verified: boolean; connected: boolean; version: string | null
  tip_height: number | null; latency_ms: number | null; abuse_controls: boolean | null
  last_error: string | null
}

export interface Market {
  mars_usd: number | null; btc_usd: number | null; prices_at: number | null
  btc_height: number | null; btc_fee_fast: number | null; btc_fee_hour: number | null
  mars_btc: number | null; sats_per_mars: number | null; error: string | null
}

export interface Timeline {
  btc_height: number; mars_height: number; btc_refund_height: number; mars_refund_height: number
  btc_refund_sec: number; mars_refund_sec: number; btc_refund_sec_slow: number
  mars_refund_sec_fast: number; margin_sec: number; min_margin_sec: number; pay_window_sec: number
}

export interface PublicSwap {
  id: string; state: string; mars: number; btc: number; rate: number
  created_at: number; updated_at: number; mars_funding_txid: string | null
  btc_funding_txid: string | null; btc_claim_txid: string | null; mars_claim_txid: string | null
}

export interface PlannedOffer { tier: number; mars: number; btc: number; rate: number; spread_percent: number }

export interface MakerStatus {
  mode: 'watch' | 'live'
  node: {
    connected?: boolean; server?: string | null; local_height?: number | null
    server_height?: number | null; wallet_ready: boolean; wallet_synced?: boolean
    wallet_error: string | null
  }
  error: string | null; cap_percent: number; reserve_percent: number; fee_percent: number
  max_pending_per_taker: number
  balance: null | {
    confirmed: number; unconfirmed: number; locked: number; on_offer: number; inventory: number
    exposure_percent: number; sold_today: number; deposit_address: string
  }
  offers: { id: string; mars: number; btc: number; rate: number; expires_in: number }[]
  swaps: PublicSwap[]
  plan: PlannedOffer[]
  totals?: { completed: number; refunded: number; mars_sold: number; btc_earned: number }
  last_refresh?: number | null
}

export interface ActivityEvent { t: number; kind: string; text: string; [k: string]: unknown }

export interface Snapshot {
  version: string; revision: number; now: number; started_at: number; mode: 'watch' | 'live'
  chains: {
    mars: { height: number | null; tip_time: number | null; block_sec: number }
    btc: { height: number | null; tip_time: number | null; fee_fast: number | null; fee_hour: number | null; block_sec: number }
  }
  market: Market | null
  relays: Relay[]
  book: Offer[]
  depth: { sats_per_mars: number; cumulative_mars: number }[]
  timeline: Timeline | null
  maker: MakerStatus
  setup?: { stage: string; next: string; min_inventory_mars: number; progress?: number }
  events: ActivityEvent[]
}

export interface History { mars_btc: [number, number][]; mars_usd: [number, number][] }
