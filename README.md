# atomicswapd

A headless market maker for **BTC ↔ MARS atomic swaps**.

`atomicswapd` is the first maker on an open exchange, not the exchange itself. The exchange is the protocol: the order book relayed by Marscoin's ElectrumX servers, plus hash time-locked contracts (HTLCs) on both chains. Anyone with an [Electrum-Mars](https://github.com/marscoin/electrum-mars) wallet can take an offer, and anyone can run this daemon to make their own.

> **Status: first release.** Runs on mainnet: Marscoin has no public test network to swap against. The daemon starts in **watch** mode, with a real wallet and real data, posting and locking nothing. Switching to **live** is one setting. Keep the hot wallet small.

## How a swap works

1. The maker posts an offer to the ElectrumX order book: so much MARS for so much BTC.
2. A buyer's wallet accepts it. Nothing is paid yet.
3. **The maker locks the MARS first**, in a lockbox only the buyer can open with a secret. The maker can take it back after about 8 hours (234 Marscoin blocks).
4. The buyer's wallet checks that lock against its own copy of the Marscoin chain. Only then does it show where to send BTC. That lockbox refunds the buyer after about 4 hours (24 Bitcoin blocks).
5. The maker claims the BTC, which reveals the secret on the Bitcoin chain.
6. The buyer's wallet reads the secret and claims the MARS.

Either both sides happen or neither does. The shorter Bitcoin refund protects the buyer, who pays second. Electrum-Mars 4.4.2 and later enforce every check above.

## What the daemon does

- Runs an Electrum-Mars wallet without a GUI and drives its swap engine: posting offers, locking MARS, claiming BTC, refunding stalled swaps.
- Watches Bitcoin through mempool.space's public API, so no Bitcoin node is needed. A second source checked before each claim is planned.
- Publishes a read-only JSON API and a public dashboard: the live order book, inventory, and every completed swap with links to both transactions, plus success and refund rates.
- Planned: a chat agent that answers questions about the market and explains why a swap is waiting. **The public agent will be read-only.** It cannot change limits or touch the wallet.

## Safety rules

These are requirements, not settings:

- **Maker locks first, always.** The maker locks MARS only after seeing a valid acceptance, and claims BTC only after the buyer's lockbox is funded. If a Bitcoin API is down or lies, the worst case is a stalled swap and a refund. Swapping the order (buyer locks first) would let a lying API cost the maker real MARS, so it stays this way while there's no Bitcoin node.
- **No late claims.** The maker never claims BTC within 3 blocks of the buyer's refund.
- **Bounded exposure.**
  - At most 30% of inventory locked in pending swaps at once.
  - One pending swap per buyer (per ElectrumX client tag).
  - Small offers by default, with bigger limits for buyers who have completed swaps before.
- **No raw IP addresses.** Abuse limits use the keyed client tags the relay servers provide.
- **Hot wallet only.** The daemon holds only the inventory it is selling. Profits are swept out.
- **The operator interface is private** and separate from anything the public can reach.

## Architecture

```
          ElectrumX order book (offers, acceptances)
                         │
   ┌─────────────────────┴─────────────────────┐
   │                atomicswapd                │
   │  Electrum-Mars wallet + swap engine       │
   │  inventory and abuse limits               │
   │  Bitcoin watcher: mempool.space + Blockstream
   │  read-only JSON API ──► dashboard, chat agent
   └───────────────────────────────────────────┘
        │                                │
   Marscoin chain                   Bitcoin chain
```

The swap logic lives in Electrum-Mars (`electrum_mars/plugins/atomic_swap`). This daemon packages it for servers and adds operations: limits, monitoring, the API and the dashboard. Protocol changes go to Electrum-Mars first, so desktop wallets and the daemon always agree.

## Running it

The daemon needs Electrum-Mars 4.4.2 or later and the libsecp256k1 version it pins; the Docker image builds both.

```sh
docker build -t atomicswapd .
docker run -p 8080:8080 -v swapdata:/data -e ATOMICSWAPD_WALLET_PASSWORD=... atomicswapd
```

Create the hot wallet once. The seed is printed once; write it down offline:

```sh
docker exec -it <container> atomicswapd wallet create
```

Then restart, send MARS to the deposit address shown on the dashboard, set a Bitcoin payout address, and switch to live:

```sh
-e ATOMICSWAPD_BTC_ADDRESS=bc1... -e ATOMICSWAPD_MODE=live
```

Settings and their defaults are in [`atomicswapd.example.toml`](atomicswapd.example.toml). `fly.toml` deploys a single machine with a volume to fly.io. Run exactly one instance per wallet.

### API

All read-only:
- `GET /api/snapshot`: everything the dashboard shows.
- `GET /api/stream`: the same snapshot as server-sent events, on every change.
- `GET /api/history`: seven days of MARS/BTC and MARS/USD market prices.
- `GET /healthz`

No keys, secrets or swap preimages are ever part of a response.

### Development

```sh
pip install -e '.[dev]'     # plus Electrum-Mars, e.g. pip install -e ../electrum-mars
pytest
atomicswapd --no-node       # dashboard on real relay and market data, no wallet
cd web-src && npm install && npm run dev
```

## Milestones

1. **First maker (this release).**
   - Headless daemon on mainnet, built on the Electrum-Mars swap engine.
   - Exposure cap, a daily limit from real history, one swap per buyer.
   - Live read-only API and dashboard.
   - Docker image and fly.io deployment.
2. **Hardening.**
   - A second Bitcoin data source (Blockstream) checked before each claim.
   - A written threat model.
   - Alerts to the operator.
   - Better limits for repeat buyers.
3. **Two-way market**: buying MARS for BTC as well as selling it.

## Related

- [Electrum-Mars](https://github.com/marscoin/electrum-mars): the wallet and swap engine
- [Marscoin ElectrumX](https://github.com/marscoin/electrumx): the servers that relay the order book
- [Atomic swaps on Marscoin](https://marscoin.org/academy/atomic-swaps): how swaps work, for users
- [InterPlanetary Atomic Swaps](https://marscoin.org/academy/ipas): the research paper behind this work

## License

MIT
