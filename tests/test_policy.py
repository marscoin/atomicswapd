import pytest

from atomicswapd import policy
from atomicswapd.config import Maker

COIN = policy.COIN
BTC_H, MARS_H = 900_000, 3_000_000


# ---------------------------------------------------------------- safety

def test_fresh_auto_maker_offer_is_safe():
    s = policy.offer_safety(MARS_H + policy.MARS_TIMELOCK_BLOCKS, MARS_H, BTC_H)
    assert s['safe'] is True and s['margin_sec'] > policy.MIN_REFUND_GAP_SEC


def test_aged_offer_is_unsafe():
    # ~29 Marscoin blocks per hour; an hour-old offer no longer leaves the margin.
    s = policy.offer_safety(MARS_H + policy.MARS_TIMELOCK_BLOCKS - 30, MARS_H, BTC_H)
    assert s['safe'] is False and 'too soon' in s['reason']


def test_unknown_heights():
    assert policy.offer_safety(MARS_H + 234, None, BTC_H)['safe'] is None


def test_safety_matches_wallet_rule():
    # Same numbers as electrum_mars.plugins.atomic_swap.safety.refund_order_problem
    from electrum_mars.plugins.atomic_swap import safety as wallet
    for mars_lock in range(MARS_H + 150, MARS_H + 300, 7):
        ours = policy.offer_safety(mars_lock, MARS_H, BTC_H)['safe']
        theirs = wallet.refund_order_problem(mars_lock, MARS_H, BTC_H + 24, BTC_H) is None
        assert ours == theirs, mars_lock


def test_timeline():
    t = policy.timeline(MARS_H, BTC_H)
    assert t['btc_refund_height'] == BTC_H + 24
    assert t['mars_refund_height'] == MARS_H + 234
    assert t['margin_sec'] == t['mars_refund_sec_fast'] - t['btc_refund_sec_slow'] > 3600
    assert policy.timeline(None, BTC_H) is None


# ---------------------------------------------------------------- budget

def test_cap_limits_offers():
    inv = 10_000 * COIN
    assert policy.offer_budget_sat(inv, 0, reserve_percent=20, cap_percent=30,
                                   daily_remaining_sat=inv) == 3_000 * COIN


def test_locked_mars_counts_against_the_cap():
    inv = 10_000 * COIN
    assert policy.offer_budget_sat(inv, 2_000 * COIN, reserve_percent=20, cap_percent=30,
                                   daily_remaining_sat=inv) == 1_000 * COIN
    assert policy.offer_budget_sat(inv, 3_500 * COIN, reserve_percent=20, cap_percent=30,
                                   daily_remaining_sat=inv) == 0


def test_reserve_and_daily_limit():
    inv = 10_000 * COIN
    assert policy.offer_budget_sat(inv, 0, reserve_percent=90, cap_percent=100,
                                   daily_remaining_sat=inv) == 1_000 * COIN
    assert policy.offer_budget_sat(inv, 0, reserve_percent=0, cap_percent=100,
                                   daily_remaining_sat=500 * COIN) == 500 * COIN


# ---------------------------------------------------------------- tiers

def test_tiers_ascend_1_2_3():
    assert policy.tier_sizes(3_000 * COIN, 3, 10 * COIN, 10_000 * COIN) == \
        [500 * COIN, 1_000 * COIN, 1_500 * COIN]


def test_tiers_respect_max_and_threshold():
    assert policy.tier_sizes(3_000 * COIN, 3, 10 * COIN, 800 * COIN) == \
        [500 * COIN, 800 * COIN, 800 * COIN]
    assert policy.tier_sizes(150 * COIN, 3, 10 * COIN, 1_000 * COIN) == []   # under 200 MARS


def test_tier_rates_and_competition():
    assert policy.tier_rate(1.0, 0, 3, 5) == pytest.approx(1.05)
    assert policy.tier_rate(1.0, 2, 3, 5) == pytest.approx(1.09)
    # undercut half the gap, but never below the floor
    assert policy.compete(1.09, [1.07], 1.0, 5) == pytest.approx(1.08)
    assert policy.compete(1.09, [1.00], 1.0, 5) == pytest.approx(1.05)
    assert policy.compete(1.05, [1.07], 1.0, 5) == pytest.approx(1.05)


def test_plan_needs_a_price():
    assert policy.plan_offers(market_rate=None, inventory_sat=10_000 * COIN, locked_sat=0,
                              daily_remaining_sat=10_000 * COIN, competing_rates=[],
                              maker=Maker()) == []


def test_plan():
    plan = policy.plan_offers(market_rate=8.6e-7, inventory_sat=10_000 * COIN, locked_sat=0,
                              daily_remaining_sat=10_000 * COIN, competing_rates=[],
                              maker=Maker(max_offer_mars=10_000))
    assert [p.mars_sat for p in plan] == [500 * COIN, 1_000 * COIN, 1_500 * COIN]
    assert [p.spread_percent for p in plan] == [5.0, 7.0, 9.0]
    assert all(p.btc_sat == int(p.mars_sat * p.rate) for p in plan)
