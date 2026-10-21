import asyncio
import json
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from atomicswapd import book, config, electrumx
from atomicswapd.api import make_app

NOW = time.time()


def raw(oid='o1', mars=1000, btc=90_000, expires=600, seen=1.0, locktime=3_000_234):
    return {'offer_id': oid, 'mars_amount_sat': mars * 10**8, 'btc_amount_sat': btc,
            'expires_at': NOW + expires, 'last_seen': seen, 'mars_locktime': locktime,
            'maker_address': 'MaBcDeFgHiJkLmNoP', 'maker_pubkey': '02' + 'aa' * 32}


# ---------------------------------------------------------------- book

def test_merge_dedupes_and_drops_bad_offers():
    merged = book.merge_offers({
        'r1': [raw('a', seen=1), raw('b', expires=-1), {'offer_id': 'junk'}],
        'r2': [raw('a', btc=95_000, seen=2), raw('c')],
    }, NOW)
    assert sorted(merged) == ['a', 'c']
    assert merged['a']['relays'] == {'r1', 'r2'}
    assert merged['a']['raw']['btc_amount_sat'] == 95_000   # newest copy wins


def test_public_offer_and_quote():
    merged = book.merge_offers({'r': [raw('cheap', btc=80_000), raw('dear', btc=99_000),
                                      raw('unsafe', btc=10_000, locktime=3_000_100)]}, NOW)
    offers = book.sorted_book([
        book.public_offer(e, now=NOW, market_rate=8e-7, mars_height=3_000_000,
                          btc_height=900_000, taken=False, ours=False)
        for e in merged.values()])
    assert [o['id'] for o in offers] == ['unsafe', 'cheap', 'dear']
    assert offers[1]['premium_percent'] == 0.0
    assert offers[0]['safety']['safe'] is False
    assert book.quote(offers, 0.001)['id'] == 'cheap'        # never the unsafe one
    assert book.quote(offers, 0.0001) is None
    assert [d['cumulative_mars'] for d in book.depth(offers)] == [1000, 2000, 3000]


# ---------------------------------------------------------------- config

def test_defaults_are_watch_mode():
    cfg = config.load(env={})
    assert cfg.mode == 'watch' and len(cfg.servers) == 3


def test_live_needs_a_payout_address():
    with pytest.raises(config.ConfigError, match='btc_receive_address'):
        config.load(env={'ATOMICSWAPD_MODE': 'live'})
    cfg = config.load(env={'ATOMICSWAPD_MODE': 'live', 'ATOMICSWAPD_BTC_ADDRESS': 'bc1qx'})
    assert cfg.maker.btc_receive_address == 'bc1qx'


def test_fee_from_environment():
    assert config.load(env={'ATOMICSWAPD_FEE_PERCENT': '8'}).maker.fee_percent == 8.0
    with pytest.raises(config.ConfigError):
        config.load(env={'ATOMICSWAPD_FEE_PERCENT': 'eight'})
    with pytest.raises(config.ConfigError):
        config.load(env={'ATOMICSWAPD_FEE_PERCENT': '-1'})


def test_rejects_unknown_and_unsafe_settings():
    with pytest.raises(config.ConfigError, match='unknown'):
        config.from_dict({'maker': {'inventroy_cap_percent': 30}})
    with pytest.raises(config.ConfigError):
        config.from_dict({'maker': {'inventory_cap_percent': 0}}).validate()
    with pytest.raises(config.ConfigError):
        config.from_dict({'maker': {'refresh_seconds': 900, 'offer_minutes': 15}}).validate()


def test_header_time():
    header = bytes(68) + (1_792_548_542).to_bytes(4, 'little') + bytes(8)
    assert electrumx.header_time(header.hex()) == 1_792_548_542
    assert electrumx.header_time('zz') is None


# ---------------------------------------------------------------- api

class FakeService:
    revision = 1
    history = {'mars_btc': [[1, 2e-7]], 'mars_usd': []}

    def snapshot(self):
        return {'revision': self.revision, 'maker': {'swaps': []}}


def test_api_is_read_only():
    async def go():
        async with TestClient(TestServer(make_app(FakeService()))) as client:
            r = await client.get('/api/snapshot')
            assert r.status == 200 and (await r.json())['revision'] == 1
            assert r.headers['Access-Control-Allow-Origin'] == '*'
            assert "frame-ancestors 'none'" in r.headers['Content-Security-Policy']
            assert (await (await client.get('/healthz')).json())['ok'] is True
            for path in ('/api/snapshot', '/api/stream', '/api/history'):
                assert (await client.post(path)).status == 405
    asyncio.run(go())


def test_stream_sends_a_snapshot():
    async def go():
        async with TestClient(TestServer(make_app(FakeService()))) as client:
            r = await client.get('/api/stream')
            line = await asyncio.wait_for(r.content.readline(), 5)
            assert line == b'event: snapshot\n'
            data = await r.content.readline()
            assert json.loads(data[len(b'data: '):])['revision'] == 1
    asyncio.run(go())


def test_example_config_matches_the_defaults():
    from pathlib import Path
    example = config.load(str(Path(__file__).parent.parent / 'atomicswapd.example.toml'), env={})
    default = config.load(env={})
    assert example == default


def test_empty_history_is_never_cached():
    class Svc(FakeService):
        history = {'mars_btc': [], 'mars_usd': []}

    async def go():
        async with TestClient(TestServer(make_app(Svc()))) as client:
            r = await client.get('/api/history')
            assert r.headers['Cache-Control'] == 'no-store'
        async with TestClient(TestServer(make_app(FakeService()))) as client:
            r = await client.get('/api/history')
            assert r.headers['Cache-Control'] == 'max-age=300'
    asyncio.run(go())
