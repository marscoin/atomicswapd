"""The daemon: relays, market data, the maker, and the public snapshot."""

import asyncio
import collections
import logging
import time
from typing import Dict, List, Optional

import aiohttp

from . import __version__, book, policy
from .config import Config
from .electrumx import Relay
from .market import MarketFeed

log = logging.getLogger(__name__)

MAX_TRACKED_OFFERS = 40
HEADER_SLACK = 12          # blocks behind the relays that still count as synced


def setup_stage(maker: dict, *, mode: str, payout_set: bool, cap_percent: float) -> dict:
    """The setup stage of a maker, from its public status, plus the next step."""
    node = maker.get('node') or {}
    bal = maker.get('balance')
    min_inventory = round(policy.MIN_BALANCE_THRESHOLD_MARS / (cap_percent / 100))

    def stage(name, next_step, **extra):
        return {'stage': name, 'next': next_step, 'min_inventory_mars': min_inventory, **extra}

    if not node.get('wallet_ready'):
        err = node.get('wallet_error') or 'the wallet is not loaded'
        if err.startswith('no wallet'):
            return stage('needs_wallet', 'Create the hot wallet: atomicswapd wallet create '
                         '--seed-file /data/seed-backup.txt, then restart.')
        return stage('wallet_error', f'Fix the wallet: {err}')
    local, server = node.get('local_height'), node.get('server_height')
    if not (local and server) or local < server - HEADER_SLACK:
        pct = round(local / server * 100, 1) if local and server else 0
        return stage('syncing', f'Wait: downloading Marscoin headers ({pct}%).', progress=pct)
    if not node.get('wallet_synced'):
        return stage('syncing', 'Wait: the wallet is catching up with its history.', progress=100)
    if maker.get('error'):
        return stage('maker_error', f'Fix the maker: {maker["error"]}')
    inventory = (bal or {}).get('inventory') or 0
    if inventory <= 0:
        return stage('needs_funds', f'Send MARS to {(bal or {}).get("deposit_address")} '
                     f'(at least {min_inventory} MARS before any offer is posted).')
    if inventory < min_inventory:
        return stage('needs_funds', f'Inventory is {inventory:,.0f} MARS; offers start at '
                     f'{min_inventory} MARS. Send more to {bal.get("deposit_address")}.')
    if not payout_set:
        return stage('needs_payout_address', 'Set ATOMICSWAPD_BTC_ADDRESS to a Bitcoin '
                     'address you control.')
    if mode != 'live':
        if not maker.get('plan'):
            return stage('waiting_for_price', 'Wait for a fresh market price.')
        return stage('ready', 'Review "Would post now" on the dashboard, then set '
                     'ATOMICSWAPD_MODE=live.')
    if not maker.get('offers'):
        return stage('live', 'Live. The first offers are posted at the next refresh '
                     '(every few minutes).')
    return stage('live', 'Live and posting offers.')


class Service:

    def __init__(self, cfg: Config, node=None):
        self.cfg = cfg
        self.node = node
        self.started_at = time.time()
        self.relays = [Relay(s) for s in cfg.servers]
        for r in self.relays:
            r.on_tip = self._on_mars_tip
        self.session: Optional[aiohttp.ClientSession] = None
        self.market: Optional[MarketFeed] = None
        self.offers: Dict[str, dict] = {}      # merged book, by offer id
        self.taken: set = set()
        self.history: dict = {'mars_btc': [], 'mars_usd': []}
        self.events = collections.deque(maxlen=200)
        self.changed = asyncio.Event()
        self.revision = 0
        self.stop = asyncio.Event()
        self._mars_height = None
        self._swap_states: Dict[str, str] = {}
        # Electrum-Mars swap machinery (only with a wallet)
        self.engine = self.orderbook = self.worker = self.automaker = None
        self.maker_error: Optional[str] = None

    # ------------------------------------------------------------ events

    def event(self, kind: str, text: str, **data):
        self.events.append({'t': time.time(), 'kind': kind, 'text': text, **data})
        self.touch()

    def touch(self):
        self.revision += 1
        self.changed.set()

    def _on_mars_tip(self, relay: Relay):
        h = relay.tip_height
        if h and (self._mars_height is None or h > self._mars_height):
            if self._mars_height is not None:
                self.event('mars_block', f'Marscoin block {h:,}', height=h)
            self._mars_height = h

    def _on_btc_block(self, height: int):
        self.event('btc_block', f'Bitcoin block {height:,}', height=height)

    # ------------------------------------------------------------ heights

    @property
    def mars_height(self) -> Optional[int]:
        heights = [r.tip_height for r in self.relays if r.connected and r.tip_height]
        return max(heights) if heights else None

    @property
    def mars_tip_time(self) -> Optional[int]:
        best = max((r for r in self.relays if r.connected and r.tip_height),
                   key=lambda r: r.tip_height, default=None)
        return best.tip_time if best else None

    # ------------------------------------------------------------ maker

    async def start_maker(self):
        """Wire up Electrum-Mars's swap engine around the hot wallet.

        The engine, worker and Auto-Maker run on Electrum's own event loop,
        like in the desktop wallet. The dashboard runs on a separate loop so
        wallet work (header checks, signing) never stalls it.
        """
        if not (self.node and self.node.wallet):
            return

        async def on_node_loop():
            self._create_maker()
        try:
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(on_node_loop(), self.node.loop))
        except Exception as e:
            self.maker_error = str(e)
            log.exception('maker did not start')
            self.event('maker', f'Maker did not start: {e}')
            return
        if self.cfg.mode == 'live':
            self.event('maker', 'Maker is live: posting offers')
        else:
            self.event('maker', 'Maker is watching: nothing is posted or locked')

    def _create_maker(self):
        from electrum_mars.plugins.atomic_swap.orderbook import OrderBook
        from electrum_mars.plugins.atomic_swap.swap_engine import SwapEngine
        from .maker import CappedAutoMaker, GuardedSwapWorker, check_btc_address
        from .node import wallet_password
        import os
        if self.cfg.mode == 'live':
            check_btc_address(self.cfg.maker.btc_receive_address)
        os.makedirs(self.cfg.swaps_dir, exist_ok=True)
        self.engine = SwapEngine(self.node.wallet, self.node.network, self.cfg.swaps_dir)
        self.orderbook = OrderBook()
        # The worker always runs: swaps already in progress must be
        # finished or refunded even in watch mode.
        self.worker = GuardedSwapWorker(self.engine, self.cfg, wallet_password())
        self.worker.start()
        self.automaker = CappedAutoMaker(self.engine, self.orderbook, self.cfg, self.market)
        if self.cfg.mode == 'live':
            self.automaker.start()

    def _my_offers(self) -> list:
        """Our posted offers, read from the dashboard thread without
        touching the order book's own (mutating) helpers."""
        if not self.orderbook:
            return []
        now = time.time()
        for _ in range(3):
            try:
                return [o for o in list(self.orderbook._my_offers.values()) if o.expires_at > now]
            except RuntimeError:   # changed while we read it
                continue
        return []

    def setup_status(self, maker: dict) -> dict:
        """Where this maker is in its setup, and the one thing to do next.

        Meant for operators and for agents running the daemon: the same
        answer appears on the dashboard, in /api/snapshot and in
        `atomicswapd status`.
        """
        return setup_stage(maker, mode=self.cfg.mode,
                           payout_set=bool(self.cfg.maker.btc_receive_address),
                           cap_percent=self.cfg.maker.inventory_cap_percent)

    def maker_status(self) -> dict:
        node = self.node.status() if self.node else {'wallet_ready': False,
                                                      'wallet_error': 'no Electrum-Mars node'}
        out = {'mode': self.cfg.mode, 'node': node, 'error': self.maker_error,
               'cap_percent': self.cfg.maker.inventory_cap_percent,
               'reserve_percent': self.cfg.maker.reserve_percent,
               'fee_percent': self.cfg.maker.fee_percent,
               'max_pending_per_taker': self.cfg.maker.max_pending_per_taker,
               'balance': None, 'offers': [], 'swaps': [], 'plan': []}
        if not self.engine:
            return out
        from .maker import locked_sat, public_swap, sold_today_sat
        wallet = self.node.wallet
        confirmed, unconfirmed = wallet.get_balance()[:2]
        swaps = self.engine.get_all_swaps()
        maker_swaps = [s for s in swaps if s.role == 'maker']
        locked = locked_sat(maker_swaps)
        inventory = confirmed + locked
        my_offers = self._my_offers()
        on_offer = sum(o.mars_amount_sat for o in my_offers)
        out['balance'] = {
            'confirmed': confirmed / policy.COIN,
            'unconfirmed': unconfirmed / policy.COIN,
            'locked': locked / policy.COIN,
            'on_offer': on_offer / policy.COIN,
            'inventory': inventory / policy.COIN,
            'exposure_percent': round((locked + on_offer) / inventory * 100, 1) if inventory else 0,
            'sold_today': sold_today_sat(maker_swaps, time.time()) / policy.COIN,
            'deposit_address': wallet.get_receiving_address(),
        }
        now = time.time()
        out['offers'] = [{'id': o.offer_id[:12], 'mars': o.mars_amount_sat / policy.COIN,
                          'btc': o.btc_amount_sat / policy.COIN, 'rate': o.rate,
                          'expires_in': round(o.expires_at - now)}
                         for o in my_offers]
        # A posted offer is a 'created' swap with no buyer yet; those belong
        # to the order book, not to the list of swaps.
        shown = [s for s in sorted(maker_swaps, key=lambda s: s.updated_at, reverse=True)
                 if s.state != 'expired' and not (s.state == 'created' and not s.peer_pubkey)][:25]
        out['swaps'] = [public_swap(s) for s in shown]
        out['totals'] = {
            'completed': sum(1 for s in maker_swaps if s.state in ('btc_claimed', 'completed')),
            'refunded': sum(1 for s in maker_swaps if s.state == 'mars_refunded'),
            'mars_sold': sum(s.mars_amount_sat for s in maker_swaps
                             if s.state in ('btc_claimed', 'completed')) / policy.COIN,
            'btc_earned': sum(s.btc_amount_sat for s in maker_swaps
                              if s.state in ('btc_claimed', 'completed')) / policy.COIN,
        }
        if self.automaker:
            out['last_refresh'] = self.automaker.last_refresh
            m = self.market.market if self.market else None
            fresh = (m and m.mars_btc and m.price_age() is not None
                     and m.price_age() <= self.cfg.max_price_age_seconds)
            competing = [o['rate'] for o in self._public_offers() if not o['ours']]
            plan = policy.plan_offers(
                market_rate=m.mars_btc if fresh else None,
                inventory_sat=inventory, locked_sat=locked,
                daily_remaining_sat=self.automaker.get_remaining_daily_limit_sat(),
                competing_rates=competing, maker=self.cfg.maker)
            out['plan'] = [p.to_dict() for p in plan]
        return out

    def _track_swaps(self):
        if not self.engine:
            return
        for s in self.engine.get_all_swaps():
            old = self._swap_states.get(s.swap_id)
            if old != s.state:
                self._swap_states[s.swap_id] = s.state
                if old is not None and s.state != 'expired':
                    self.event('swap', f'Swap {s.swap_id[:8]}: {s.state.replace("_", " ")}',
                               swap=s.swap_id[:8], state=s.state)

    # ------------------------------------------------------------ order book

    def _our_offer_ids(self) -> set:
        return {o.offer_id for o in self._my_offers()}

    def _public_offers(self) -> List[dict]:
        m = self.market.market if self.market else None
        now = time.time()
        ours = self._our_offer_ids()
        return book.sorted_book([
            book.public_offer(e, now=now, market_rate=m.mars_btc if m else None,
                              mars_height=self.mars_height,
                              btc_height=m.btc_height if m else None,
                              taken=oid in self.taken, ours=oid in ours)
            for oid, e in self.offers.items()])

    async def _poll_book(self):
        while not self.stop.is_set():
            per_relay = {}
            for r in self.relays:
                if r.connected:
                    try:
                        per_relay[r.server.label] = await r.get_offers()
                    except Exception as e:
                        log.debug('get_offers %s: %s', r.server.label, e)
            now = time.time()
            merged = book.merge_offers(per_relay, now) if per_relay else self.offers
            for oid in merged.keys() - self.offers.keys():
                o = merged[oid]['raw']
                self.event('offer', f'New offer: {int(o["mars_amount_sat"]) / 1e8:,.0f} MARS',
                           offer=oid[:12])
            for oid in self.offers.keys() - merged.keys():
                if oid not in self.taken:
                    self.event('offer_gone', f'Offer {oid[:8]} withdrawn or expired', offer=oid[:12])
            self.offers = merged
            await self._poll_acceptances()
            if self.automaker:
                # Swap state belongs to Electrum's loop; change it there.
                self.node.loop.call_soon_threadsafe(self.automaker.expire_stale_offers)
            self._track_swaps()
            self.touch()
            try:
                await asyncio.wait_for(self.stop.wait(), self.cfg.poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def _poll_acceptances(self):
        for oid in list(self.offers)[:MAX_TRACKED_OFFERS]:
            if oid in self.taken:
                continue
            for r in self.relays:
                if not r.connected:
                    continue
                try:
                    if await r.get_acceptance(oid):
                        self.taken.add(oid)
                        self.event('taken', f'Offer {oid[:8]} accepted by a buyer', offer=oid[:12])
                        break
                except Exception:
                    pass
        self.taken &= set(self.offers)

    # ------------------------------------------------------------ history

    async def _poll_history(self):
        url = 'https://api.coingecko.com/api/v3/coins/marscoin/market_chart?vs_currency={}&days=30'
        while not self.stop.is_set():
            for key, cur in (('mars_btc', 'btc'), ('mars_usd', 'usd')):
                try:
                    async with self.session.get(url.format(cur)) as resp:
                        resp.raise_for_status()
                        data = await resp.json(content_type=None)
                    self.history[key] = [[int(t / 1000), p] for t, p in data.get('prices', [])]
                except Exception as e:
                    log.info('price history (%s): %s', cur, e)
            try:
                await asyncio.wait_for(self.stop.wait(), 1800)
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------ snapshot

    def snapshot(self) -> dict:
        m = self.market.market if self.market else None
        offers = self._public_offers()
        maker = self.maker_status()
        return {
            'version': __version__,
            'revision': self.revision,
            'now': time.time(),
            'started_at': self.started_at,
            'mode': self.cfg.mode,
            'chains': {
                'mars': {'height': self.mars_height, 'tip_time': self.mars_tip_time,
                         'block_sec': policy.MARS_BLOCK_SEC},
                'btc': {'height': m.btc_height if m else None,
                        'tip_time': m.btc_tip_time if m else None,
                        'fee_fast': m.btc_fee_fast if m else None,
                        'fee_hour': m.btc_fee_hour if m else None,
                        'block_sec': policy.BTC_BLOCK_SEC},
            },
            'market': m.to_dict() if m else None,
            'relays': [r.status() for r in self.relays],
            'book': offers,
            'depth': book.depth(offers),
            'timeline': policy.timeline(self.mars_height, m.btc_height if m else None),
            'maker': maker,
            'setup': self.setup_status(maker),
            'events': list(self.events)[-60:],
        }

    # ------------------------------------------------------------ run

    async def _wait_for_price(self, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self.stop.is_set():
            if self.market.market.mars_btc:
                return True
            await asyncio.sleep(0.5)
        log.warning('no market price after %.0f s; the maker will retry at its next refresh', timeout)
        return False

    async def run(self):
        timeout = aiohttp.ClientTimeout(total=20)
        self.session = aiohttp.ClientSession(timeout=timeout,
                                             headers={'User-Agent': f'atomicswapd/{__version__}'})
        self.market = MarketFeed(self.session)
        self.market.on_btc_block = self._on_btc_block
        tasks = [asyncio.create_task(r.run(self.stop)) for r in self.relays]
        tasks.append(asyncio.create_task(self.market.run(self.stop, self.cfg.market_seconds)))
        # The Auto-Maker posts its first offers as soon as it starts; give it
        # a price to work with, or it skips a whole refresh interval.
        await self._wait_for_price(45)
        await self.start_maker()
        tasks.append(asyncio.create_task(self._poll_book()))
        tasks.append(asyncio.create_task(self._poll_history()))
        try:
            await self.stop.wait()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.session.close()
