import asyncio
import time
from types import SimpleNamespace

import pytest

from electrum_mars.plugins.atomic_swap.automaker import AutoMaker
from electrum_mars.plugins.atomic_swap.orderbook import OrderBook, SwapOffer
from electrum_mars.plugins.atomic_swap.swap_engine import SwapData
from electrum_mars.plugins.atomic_swap.swap_worker import SwapWorker

from atomicswapd import maker, policy
from atomicswapd.config import Config, ConfigError, Maker
from atomicswapd.market import Market

COIN = policy.COIN
NOW = time.time()


def swap(swap_id='s1', state='created', mars=1000, peer=None, funded=False,
         created=NOW, role='maker', pubkey='02aa'):
    return SwapData(swap_id=swap_id, role=role, state=state, mars_amount_sat=mars * COIN,
                    btc_amount_sat=1000, rate=0, peer_pubkey=peer, created_at=created,
                    mars_funding_txid='ff' * 32 if funded else None, my_pubkey=pubkey,
                    my_privkey='11' * 32, preimage='22' * 32)


class FakeEngine:
    def __init__(self, confirmed=10_000, swaps=()):
        self.swaps = list(swaps)
        self.wallet = SimpleNamespace(get_balance=lambda: (confirmed * COIN, 0, 0),
                                      get_receiving_address=lambda: 'MmineAddress')
        self.db = SimpleNamespace(save=lambda s: None)

    def get_all_swaps(self):
        return list(self.swaps)


def make_cfg(tmp_path, **maker_kw):
    cfg = Config(data_dir=str(tmp_path), maker=Maker(**maker_kw))
    (tmp_path / 'electrum' / 'atomic_swaps').mkdir(parents=True)
    return cfg


def fresh_market(mars_btc=8.6e-7, age=0):
    m = Market(mars_usd=mars_btc * 80_000, btc_usd=80_000, prices_at=time.time() - age)
    return SimpleNamespace(market=m)


# ---------------------------------------------------------------- bookkeeping

def test_committed_states():
    assert maker.is_committed(swap(state='mars_locked'))
    assert maker.is_committed(swap(state='btc_locked'))
    assert maker.is_committed(swap(state='created', peer='03bb'))
    assert not maker.is_committed(swap(state='created'))
    assert not maker.is_committed(swap(state='completed'))
    assert not maker.is_committed(swap(state='mars_locked', role='taker'))


def test_sold_today_counts_funded_swaps_in_24h():
    swaps = [swap('a', 'completed', 100, funded=True),
             swap('b', 'mars_locked', 200, funded=True),
             swap('c', 'created', 400),
             swap('d', 'completed', 800, funded=True, created=NOW - 2 * 86400)]
    assert maker.sold_today_sat(swaps, NOW) == 300 * COIN


def test_stale_offers():
    old = NOW - 3600
    swaps = [swap('old', created=old), swap('fresh'), swap('taken', created=old, peer='03'),
             swap('locked', 'mars_locked', created=old)]
    assert [s.swap_id for s in maker.stale_offers(swaps, 900, NOW)] == ['old']


def test_public_swap_never_exposes_keys_or_secret():
    shown = maker.public_swap(swap())
    text = repr(shown)
    assert '11' * 32 not in text and '22' * 32 not in text
    assert not {'my_privkey', 'preimage', 'payment_hash160'} & set(shown)


def test_btc_address_check():
    assert maker.check_btc_address('bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq')
    for bad in ('', 'tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx', 'bc1qnotanaddress',
                'MRandomMarscoinAddr'):
        with pytest.raises(ConfigError):
            maker.check_btc_address(bad)


# ---------------------------------------------------------------- auto-maker

def test_planner_matches_the_real_auto_maker(tmp_path):
    """policy.tier_sizes must produce what Electrum-Mars itself would post."""
    for balance in (150, 260, 1_000, 5_000, 12_345, 80_000):
        for num in (1, 3, 5):
            engine = FakeEngine(confirmed=balance)
            real = AutoMaker(engine, OrderBook(), str(tmp_path))
            real.config.num_offers = num
            available = min(real.get_available_balance_sat(), real.get_remaining_daily_limit_sat())
            ours = policy.tier_sizes(available, num, real.config.min_single_swap_sat,
                                     real.config.max_single_swap_sat)
            assert real.calculate_offer_sizes() == ours, (balance, num)


def test_capped_balance(tmp_path):
    engine = FakeEngine(confirmed=8_000, swaps=[swap('l', 'mars_locked', 2_000, funded=True)])
    am = maker.CappedAutoMaker(engine, OrderBook(), make_cfg(tmp_path), fresh_market())
    # inventory 10k: cap 3k minus 2k locked
    assert am.get_available_balance_sat() == 1_000 * COIN


def test_daily_limit_uses_real_history(tmp_path):
    engine = FakeEngine(swaps=[swap('a', 'completed', 9_500, funded=True)])
    am = maker.CappedAutoMaker(engine, OrderBook(), make_cfg(tmp_path, daily_limit_mars=10_000),
                               fresh_market())
    assert am.get_remaining_daily_limit_sat() == 500 * COIN


def test_no_stale_or_missing_prices(tmp_path):
    engine = FakeEngine()
    cfg = make_cfg(tmp_path)
    run = lambda m: asyncio.run(maker.CappedAutoMaker(engine, OrderBook(), cfg, m).fetch_market_price())
    assert run(fresh_market())['mars_btc'] == pytest.approx(8.6e-7)
    assert run(fresh_market(age=cfg.max_price_age_seconds + 1)) is None
    assert run(SimpleNamespace(market=Market())) is None


def test_ignores_own_offers_as_competition(tmp_path):
    engine = FakeEngine(swaps=[swap('mine', pubkey='02mine')])
    book = OrderBook()
    def offer(oid, pub, addr, rate):
        return SwapOffer(offer_id=oid, mars_amount_sat=COIN, btc_amount_sat=1, rate=rate,
                         maker_pubkey=pub, payment_hash160='', mars_htlc_address='',
                         mars_htlc_script='', mars_locktime=1, expires_at=NOW + 600,
                         maker_address=addr)
    for o in (offer('1', '02mine', 'Mx', 1.0), offer('2', '02z', 'MmineAddress', 1.0),
              offer('3', '02other', 'Mother', 2.0)):
        book.add_offer(o) if hasattr(book, 'add_offer') else book._offers.__setitem__(o.offer_id, o)
    am = maker.CappedAutoMaker(engine, book, make_cfg(tmp_path), fresh_market())
    assert [o.offer_id for o in am._get_competing_offers()] == ['3']


def test_watch_mode_posts_nothing(tmp_path):
    engine = FakeEngine(swaps=[swap('old', created=NOW - 3600)])
    am = maker.CappedAutoMaker(engine, OrderBook(), make_cfg(tmp_path), fresh_market())
    assert am.cfg.mode == 'watch' and am.config.enabled is False
    asyncio.run(am.create_offers())
    assert am.orderbook.get_my_offers() == []
    assert engine.swaps[0].state == 'expired'


# ---------------------------------------------------------------- one pending per buyer

def test_one_pending_swap_per_buyer(tmp_path, monkeypatch):
    engine = FakeEngine(swaps=[swap('busy', 'mars_locked', funded=True), swap('new')])
    worker = maker.GuardedSwapWorker(engine, make_cfg(tmp_path), None)
    worker.tags.set('busy', 'tag-A')

    async def acceptance_from(tag):
        async def fake(self, s):
            return {'taker_pubkey': '03cc', 'taker_tag': tag}
        monkeypatch.setattr(SwapWorker, '_check_for_acceptance', fake)
        return await worker._check_for_acceptance(engine.swaps[1])

    assert asyncio.run(acceptance_from('tag-A')) is None
    assert asyncio.run(acceptance_from('tag-B'))['taker_tag'] == 'tag-B'
    assert worker.tags.tags['new'] == 'tag-B'


def test_posted_offers_are_not_listed_as_swaps():
    from atomicswapd.service import Service
    from atomicswapd.config import Config
    svc = Service(Config())
    swaps = [swap('offer'), swap('accepted', peer='03bb'), swap('locked', 'mars_locked', funded=True),
             swap('old', 'expired')]
    svc.engine = SimpleNamespace(get_all_swaps=lambda: swaps)
    svc.node = SimpleNamespace(wallet=SimpleNamespace(get_balance=lambda: (0, 0, 0),
                                                      get_receiving_address=lambda: 'mars1q'),
                               status=lambda: {'wallet_ready': True})
    svc.orderbook = SimpleNamespace(_my_offers={})
    shown = [s['id'] for s in svc.maker_status()['swaps']]
    assert sorted(shown) == ['accepted', 'locked']
