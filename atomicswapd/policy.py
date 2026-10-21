"""Pure decision logic: what to offer, and whether a swap is safe.

Pricing and sizing mirror the Electrum-Mars Auto-Maker
(electrum_mars/plugins/atomic_swap/automaker.py). The safety rules mirror
the wallet's checks (electrum_mars/plugins/atomic_swap/safety.py), so the
dashboard says exactly what a 4.4.2 wallet would decide.
"""

from dataclasses import dataclass, asdict
from typing import Iterable, List, Optional

COIN = 100_000_000

# Swap protocol (electrum_mars/atomic_swap_htlc.py)
BTC_TIMELOCK_BLOCKS = 24
MARS_TIMELOCK_BLOCKS = 234
BTC_BLOCK_SEC = 600
MARS_BLOCK_SEC = 123

# Wallet safety margins (electrum_mars/plugins/atomic_swap/safety.py)
BTC_SLOW = 1.25
MARS_FAST = 0.85
MIN_REFUND_GAP_SEC = 3600
MIN_BTC_WINDOW_SEC = 2 * 3600

# Auto-Maker
MIN_BALANCE_THRESHOLD_MARS = 200
MAX_TIER_PREMIUM = 0.04


# ---------------------------------------------------------------- refund clocks

def btc_refund_seconds(btc_locktime: int, btc_height: int) -> float:
    return max(0, btc_locktime - btc_height) * BTC_BLOCK_SEC * BTC_SLOW


def mars_refund_seconds(mars_locktime: int, mars_height: int) -> float:
    return max(0, mars_locktime - mars_height) * MARS_BLOCK_SEC * MARS_FAST


def refund_margin_seconds(mars_locktime: int, mars_height: int,
                          btc_locktime: int, btc_height: int) -> float:
    """Pessimistic time between the BTC refund and the MARS refund."""
    return (mars_refund_seconds(mars_locktime, mars_height)
            - btc_refund_seconds(btc_locktime, btc_height))


def offer_safety(mars_locktime: int, mars_height: Optional[int],
                 btc_height: Optional[int]) -> dict:
    """Would a 4.4.2 wallet accept this offer right now?

    The taker's BTC refund would open BTC_TIMELOCK_BLOCKS from now; the
    MARS refund must open at least an hour later, pessimistically.
    """
    if not (mars_locktime and mars_height and btc_height):
        return {'safe': None, 'margin_sec': None, 'reason': 'waiting for block heights'}
    margin = refund_margin_seconds(mars_locktime, mars_height,
                                   btc_height + BTC_TIMELOCK_BLOCKS, btc_height)
    if margin < MIN_REFUND_GAP_SEC:
        return {'safe': False, 'margin_sec': round(margin),
                'reason': 'the seller could reclaim the MARS too soon after the BTC refund'}
    return {'safe': True, 'margin_sec': round(margin), 'reason': None}


def timeline(mars_height: Optional[int], btc_height: Optional[int]) -> Optional[dict]:
    """The refund schedule of a swap that started now."""
    if not (mars_height and btc_height):
        return None
    btc_refund = btc_height + BTC_TIMELOCK_BLOCKS
    mars_refund = mars_height + MARS_TIMELOCK_BLOCKS
    return {
        'btc_height': btc_height,
        'mars_height': mars_height,
        'btc_refund_height': btc_refund,
        'mars_refund_height': mars_refund,
        'btc_refund_sec': BTC_TIMELOCK_BLOCKS * BTC_BLOCK_SEC,
        'mars_refund_sec': MARS_TIMELOCK_BLOCKS * MARS_BLOCK_SEC,
        'btc_refund_sec_slow': round(btc_refund_seconds(btc_refund, btc_height)),
        'mars_refund_sec_fast': round(mars_refund_seconds(mars_refund, mars_height)),
        'margin_sec': round(refund_margin_seconds(mars_refund, mars_height, btc_refund, btc_height)),
        'min_margin_sec': MIN_REFUND_GAP_SEC,
        'pay_window_sec': BTC_TIMELOCK_BLOCKS * BTC_BLOCK_SEC - MIN_BTC_WINDOW_SEC,
    }


# ---------------------------------------------------------------- offers

def offer_budget_sat(inventory_sat: int, locked_sat: int, *, reserve_percent: float,
                     cap_percent: float, daily_remaining_sat: int) -> int:
    """How much MARS may be on offer right now.

    The Auto-Maker keeps a reserve. On top of that, MARS already locked in
    pending swaps plus everything on offer never exceeds the cap, so even
    if every offer were taken at once the exposure stays bounded.
    """
    # Same arithmetic as AutoMaker.get_available_balance_sat
    after_reserve = inventory_sat - int(inventory_sat * reserve_percent / 100.0)
    under_cap = int(inventory_sat * cap_percent / 100) - locked_sat
    return max(0, min(after_reserve - locked_sat, under_cap, daily_remaining_sat))


def tier_sizes(total_sat: int, num_offers: int, min_sat: int, max_sat: int) -> List[int]:
    """Ascending tiers in the ratio 1:2:3..., as the Auto-Maker posts them."""
    if total_sat < MIN_BALANCE_THRESHOLD_MARS * COIN or total_sat < min_sat:
        return []
    weights = range(1, num_offers + 1)
    weight_sum = sum(weights)
    tiers = [size for size in (min(int(total_sat * w / weight_sum), max_sat) for w in weights)
             if size >= min_sat]
    if not tiers:
        size = min(total_sat, max_sat)
        return [size] if size >= min_sat else []
    return tiers


def tier_rate(market_rate: float, tier_index: int, num_tiers: int, fee_percent: float) -> float:
    """The smallest tier pays the floor fee; larger tiers up to +4 points."""
    floor = fee_percent / 100
    if num_tiers <= 1:
        return market_rate * (1 + floor)
    return market_rate * (1 + floor + MAX_TIER_PREMIUM * tier_index / (num_tiers - 1))


def compete(rate: float, competing_rates: Iterable[float], market_rate: float,
            fee_percent: float) -> float:
    """Undercut the best competitor by half the gap, never below the floor."""
    competing = [r for r in competing_rates if r and r > 0]
    if not competing:
        return rate
    best = min(competing)
    if rate <= best:
        return rate
    floor = market_rate * (1 + fee_percent / 100)
    return max(rate - (rate - best) / 2, floor)


@dataclass
class PlannedOffer:
    tier: int
    mars_sat: int
    btc_sat: int
    rate: float          # BTC per MARS
    spread_percent: float

    def to_dict(self) -> dict:
        d = asdict(self)
        d['mars'] = self.mars_sat / COIN
        d['btc'] = self.btc_sat / COIN
        return d


def plan_offers(*, market_rate: Optional[float], inventory_sat: int, locked_sat: int,
                daily_remaining_sat: int, competing_rates: Iterable[float],
                maker) -> List[PlannedOffer]:
    """The offers the maker posts now, given a fresh market rate."""
    if not market_rate or market_rate <= 0:
        return []
    budget = offer_budget_sat(
        inventory_sat, locked_sat,
        reserve_percent=maker.reserve_percent,
        cap_percent=maker.inventory_cap_percent,
        daily_remaining_sat=daily_remaining_sat)
    sizes = tier_sizes(budget, maker.num_offers,
                       int(maker.min_offer_mars * COIN), int(maker.max_offer_mars * COIN))
    competing_rates = list(competing_rates)
    plan = []
    for i, mars_sat in enumerate(sizes):
        rate = tier_rate(market_rate, i, len(sizes), maker.fee_percent)
        rate = compete(rate, competing_rates, market_rate, maker.fee_percent)
        btc_sat = int(mars_sat * rate)
        if btc_sat <= 0:
            continue
        plan.append(PlannedOffer(i + 1, mars_sat, btc_sat, rate,
                                 round((rate / market_rate - 1) * 100, 2)))
    return plan
