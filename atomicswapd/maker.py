"""The maker: Electrum-Mars's own swap engine, with the daemon's limits.

The swap protocol is the Electrum-Mars code that desktop wallets run
(electrum_mars/plugins/atomic_swap). This module adds what an always-on
maker needs on top:

* an exposure cap: MARS locked in pending swaps plus MARS on offer stays
  under a share of inventory, so even if every offer were taken at once
  the exposure is bounded;
* a daily limit computed from the real swap history;
* at most N swaps in progress per buyer (relay client tag);
* prices only from fresh market data, never a fallback;
* no competing against its own earlier offers;
* expiring superseded offers so they stop being polled.
"""

import json
import logging
import os
import time
from typing import Dict, List, Optional

from electrum_mars.atomic_swap_htlc import btc_address_to_scriptpubkey
from electrum_mars.plugins.atomic_swap.automaker import AutoMaker
from electrum_mars.plugins.atomic_swap.orderbook import OrderBook
from electrum_mars.plugins.atomic_swap.swap_engine import (
    SwapData, SwapEngine, SwapRole, SwapState)
from electrum_mars.plugins.atomic_swap.swap_worker import SwapWorker

from . import policy
from .config import Config, ConfigError

log = logging.getLogger(__name__)

DAY = 24 * 3600
EXPIRE_GRACE_SEC = 600

# A maker swap in these states has MARS committed to a buyer.
COMMITTED = (SwapState.MARS_LOCKED.value, SwapState.BTC_LOCKED.value)
DONE = (SwapState.BTC_CLAIMED.value, SwapState.COMPLETED.value)


def check_btc_address(address: str) -> str:
    """Raise unless ``address`` is a mainnet Bitcoin address the engine can pay."""
    address = (address or '').strip()
    low = address.lower()
    if low.startswith(('tb1', 'bcrt1')) or address[:1] in ('m', 'n', '2'):
        raise ConfigError('maker.btc_receive_address must be a mainnet Bitcoin address')
    try:
        btc_address_to_scriptpubkey(address)
    except Exception as e:
        raise ConfigError(f'maker.btc_receive_address is not a valid Bitcoin address: {e}')
    return address


def is_committed(swap: SwapData) -> bool:
    """MARS locked, or about to be locked for an accepted offer."""
    if swap.role != SwapRole.MAKER.value:
        return False
    if swap.state in COMMITTED:
        return True
    return swap.state == SwapState.CREATED.value and bool(swap.peer_pubkey)


def locked_sat(swaps: List[SwapData]) -> int:
    return sum(s.mars_amount_sat for s in swaps if is_committed(s))


def sold_today_sat(swaps: List[SwapData], now: float) -> int:
    """MARS committed to buyers in the last 24 hours."""
    return sum(s.mars_amount_sat for s in swaps
               if s.role == SwapRole.MAKER.value and s.mars_funding_txid
               and s.created_at >= now - DAY)


def stale_offers(swaps: List[SwapData], offer_seconds: float, now: float) -> List[SwapData]:
    """Maker swaps for offers that expired without a buyer."""
    cutoff = now - offer_seconds - EXPIRE_GRACE_SEC
    return [s for s in swaps
            if s.role == SwapRole.MAKER.value and s.state == SwapState.CREATED.value
            and not s.peer_pubkey and s.created_at < cutoff]


class TakerTags:
    """Which buyer (relay client tag) each of our swaps belongs to."""

    def __init__(self, path: str):
        self.path = path
        self.tags: Dict[str, str] = {}
        try:
            with open(path) as f:
                self.tags = json.load(f)
        except (FileNotFoundError, ValueError):
            pass

    def set(self, swap_id: str, tag: str):
        if self.tags.get(swap_id) != tag:
            self.tags[swap_id] = tag
            tmp = self.path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(self.tags, f)
            os.replace(tmp, self.path)

    def pending(self, tag: str, swaps: List[SwapData], exclude: str = '') -> int:
        by_id = {s.swap_id: s for s in swaps}
        return sum(1 for sid, t in self.tags.items()
                   if t == tag and sid != exclude and sid in by_id and is_committed(by_id[sid]))


class CappedAutoMaker(AutoMaker):
    """The Electrum-Mars Auto-Maker with the daemon's limits."""

    def __init__(self, engine: SwapEngine, orderbook: OrderBook, cfg: Config, market):
        super().__init__(engine, orderbook, cfg.swaps_dir)
        self.cfg = cfg
        self.market = market
        m = cfg.maker
        c = self.config
        c.enabled = cfg.mode == 'live'
        c.btc_receive_address = m.btc_receive_address
        c.fee_percent = m.fee_percent
        c.num_offers = m.num_offers
        c.min_single_swap_sat = int(m.min_offer_mars * policy.COIN)
        c.max_single_swap_sat = int(m.max_offer_mars * policy.COIN)
        c.daily_limit_mars_sat = int(m.daily_limit_mars * policy.COIN)
        c.reserve_percent = m.reserve_percent
        c.offer_duration_hours = m.offer_minutes / 60
        c.refresh_interval_sec = m.refresh_seconds
        c.also_buy_mars = False
        self.save_config()
        self.last_error: Optional[str] = None
        self.last_refresh: Optional[float] = None

    def swaps(self) -> List[SwapData]:
        return self.engine.get_all_swaps()

    def inventory_sat(self) -> int:
        confirmed = self.engine.wallet.get_balance()[0]
        return confirmed + locked_sat(self.swaps())

    async def fetch_market_price(self) -> Optional[dict]:
        m = self.market.market
        age = m.price_age()
        if not m.mars_btc or age is None or age > self.cfg.max_price_age_seconds:
            log.warning('no fresh market price; not posting offers')
            return None
        self.stats.last_price_mars_usd = m.mars_usd
        self.stats.last_price_btc_usd = m.btc_usd
        self.stats.last_rate_mars_btc = m.mars_btc
        return {'mars_usd': m.mars_usd, 'btc_usd': m.btc_usd, 'mars_btc': m.mars_btc}

    def get_available_balance_sat(self) -> int:
        swaps = self.swaps()
        inventory = self.engine.wallet.get_balance()[0] + locked_sat(swaps)
        return policy.offer_budget_sat(
            inventory, locked_sat(swaps),
            reserve_percent=self.cfg.maker.reserve_percent,
            cap_percent=self.cfg.maker.inventory_cap_percent,
            daily_remaining_sat=inventory)

    def get_remaining_daily_limit_sat(self) -> int:
        sold = sold_today_sat(self.swaps(), time.time())
        return max(0, self.config.daily_limit_mars_sat - sold)

    def _get_competing_offers(self) -> list:
        mine = {s.my_pubkey for s in self.swaps() if s.role == SwapRole.MAKER.value}
        address = self.engine.wallet.get_receiving_address()
        return [o for o in self.orderbook.get_offers()
                if o.maker_pubkey not in mine and o.maker_address != address]

    async def create_offers(self):
        try:
            self.expire_stale_offers()
            if self.cfg.mode == 'live':
                await super().create_offers()
                self.last_refresh = time.time()
            self.last_error = None
        except Exception as e:
            self.last_error = str(e)
            raise

    def expire_stale_offers(self) -> int:
        stale = stale_offers(self.swaps(), self.cfg.maker.offer_minutes * 60, time.time())
        for s in stale:
            s.state = SwapState.EXPIRED.value
            s.error_msg = 'offer expired without a buyer'
            self.engine.db.save(s)
        if stale:
            log.info('expired %d superseded offers', len(stale))
        return len(stale)


class GuardedSwapWorker(SwapWorker):
    """The Electrum-Mars swap worker, refusing buyers who already have a
    swap in progress with us."""

    def __init__(self, engine: SwapEngine, cfg: Config, password: Optional[str]):
        super().__init__(engine, password_getter=lambda: password)
        self.cfg = cfg
        self.tags = TakerTags(os.path.join(cfg.swaps_dir, 'taker_tags.json'))
        self._busy_logged = set()

    async def _check_for_acceptance(self, swap: SwapData) -> Optional[dict]:
        acceptance = await super()._check_for_acceptance(swap)
        if not acceptance:
            return None
        tag = acceptance.get('taker_tag')
        if tag:
            pending = self.tags.pending(tag, self.engine.get_all_swaps(), exclude=swap.swap_id)
            if pending >= self.cfg.maker.max_pending_per_taker:
                if swap.swap_id not in self._busy_logged:
                    self._busy_logged.add(swap.swap_id)
                    log.warning('offer %s: buyer already has %d swap(s) in progress; '
                                'not locking MARS', swap.swap_id[:8], pending)
                return None
            self.tags.set(swap.swap_id, tag)
        return acceptance


def public_swap(s: SwapData) -> dict:
    """What the dashboard may show about a swap. Never keys or the secret."""
    return {
        'id': s.swap_id[:8],
        'state': s.state,
        'mars': s.mars_amount_sat / policy.COIN,
        'btc': s.btc_amount_sat / policy.COIN,
        'rate': s.rate,
        'created_at': s.created_at,
        'updated_at': s.updated_at,
        'mars_funding_txid': s.mars_funding_txid,
        'btc_funding_txid': s.btc_funding_txid,
        'btc_claim_txid': s.btc_claim_txid,
        'mars_claim_txid': s.mars_claim_txid,
    }
