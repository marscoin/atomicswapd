"""The API for AI agents: /api/v1 and the discovery documents."""

import asyncio
import json
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from atomicswapd import agentapi, agentdocs, agentprotocol, config, policy
from atomicswapd.api import make_app
from atomicswapd.market import Market

COIN = policy.COIN
MARS_H, BTC_H = 3_700_000, 900_000
SAFE_LOCK = MARS_H + 470          # this maker's 480-block lock, ten blocks old
UNSAFE_LOCK = MARS_H + 150        # an old 234-block lock, part used up

A, B, C, D = 'a1' * 16, 'b2' * 16, 'c3' * 16, 'd4' * 16


def raw(oid, mars, btc, locktime=SAFE_LOCK, expires=600, **extra):
    return {'raw': {'offer_id': oid, 'mars_amount_sat': mars * COIN, 'btc_amount_sat': btc,
                    'maker_pubkey': '02' + oid[:2] * 32, 'payment_hash160': oid[:2] * 20,
                    'mars_locktime': locktime, 'expires_at': time.time() + expires,
                    'maker_address': 'MsecretAddressNotShown', **extra},
            'relays': {'r1', 'r2'}}


def state(**over):
    now = time.time()
    st = {
        'version': '0.1.0', 'mode': 'live',
        'market': Market(mars_usd=0.0612, btc_usd=68_000.0, prices_at=now - 20,
                         btc_height=BTC_H, btc_tip_time=int(now) - 300, btc_at=now - 5,
                         btc_fee_fast=4, btc_fee_hour=2),
        'max_price_age_seconds': 600, 'mars_height': MARS_H, 'mars_tip_time': int(now) - 90,
        'offers': {
            A: raw(A, 100, 9_450),                      # 94.5 sats/MARS
            B: raw(B, 200, 19_260),                     # 96.3
            C: raw(C, 300, 26_000, UNSAFE_LOCK),        # cheapest, but unsafe
            D: raw(D, 500, 49_000),                     # 98.0
        },
        'taken': set(), 'ours': {A, B}, 'maker_running': True,
        'maker_swaps': [
            {'id': 'aaaaaaaa', 'state': 'btc_claimed', 'mars_amount_sat': 150 * COIN,
             'btc_amount_sat': 14_200, 'created_at': now - 7200, 'updated_at': now - 4100,
             'accepted': True},
            {'id': 'bbbbbbbb', 'state': 'mars_refunded', 'mars_amount_sat': 50 * COIN,
             'btc_amount_sat': 5_000, 'created_at': now - 90_000, 'updated_at': now - 30_000,
             'accepted': True},
            {'id': 'cccccccc', 'state': 'created', 'mars_amount_sat': 100 * COIN,
             'btc_amount_sat': 9_450, 'created_at': now - 100, 'updated_at': now - 100,
             'accepted': False},
        ],
        'relays': [{'host': 'electrum.marscoin.org', 'port': 50002, 'tls_verify': True},
                   {'host': '147.182.177.23', 'port': 50002, 'tls_verify': False}],
        'relays_connected': 2,
        'maker_config': {'offer_minutes': 15, 'refresh_seconds': 300, 'max_pending_per_taker': 1,
                         'min_offer_mars': 10, 'max_offer_mars': 1000},
        'poll_seconds': 10, 'public_url': '',
    }
    st.update(over)
    return st


class FakeService:
    revision = 1
    history = {'mars_btc': [[1, 2e-7]], 'mars_usd': []}

    def __init__(self, **over):
        self.st = state(**over)

    def snapshot(self):
        return {'revision': self.revision}

    def agent_state(self):
        return self.st


def fetch(*paths, service=None, method='get', headers=None):
    """GET each path; returns [(status, headers, body)]. JSON bodies are parsed."""
    async def go():
        out = []
        async with TestClient(TestServer(make_app(service or FakeService()))) as client:
            for path in paths:
                r = await getattr(client, method)(path, headers=headers or {})
                text = await r.text()
                body = json.loads(text) if 'json' in r.headers.get('Content-Type', '') else text
                out.append((r.status, r.headers, body))
        return out
    return asyncio.run(go())


def get(path, **kw):
    return fetch(path, **kw)[0]


# ---------------------------------------------------------------- a small JSON Schema checker

def check(value, schema, root, where='$'):
    """Enough of JSON Schema for the documents here: $ref, type, enum,
    properties, required, additionalProperties, items, anyOf."""
    if '$ref' in schema:
        node = root
        for part in schema['$ref'].lstrip('#/').split('/'):
            node = node[part]
        return check(value, node, root, where)
    if 'anyOf' in schema:
        errors = []
        for option in schema['anyOf']:
            try:
                return check(value, option, root, where)
            except AssertionError as e:
                errors.append(str(e))
        raise AssertionError(f'{where}: no anyOf branch matched: {errors}')
    types = schema.get('type')
    if types:
        types = [types] if isinstance(types, str) else types
        ok = {'object': isinstance(value, dict), 'array': isinstance(value, list),
              'string': isinstance(value, str), 'boolean': isinstance(value, bool),
              'integer': isinstance(value, int) and not isinstance(value, bool),
              'number': isinstance(value, (int, float)) and not isinstance(value, bool),
              'null': value is None}
        assert any(ok[t] for t in types), f'{where}: {value!r} is not {types}'
    if 'enum' in schema:
        assert value in schema['enum'], f'{where}: {value!r} not in {schema["enum"]}'
    if isinstance(value, dict):
        for key in schema.get('required', []):
            assert key in value, f'{where}: missing {key}'
        props = schema.get('properties', {})
        for key, item in value.items():
            if key in props:
                check(item, props[key], root, f'{where}.{key}')
            else:
                assert schema.get('additionalProperties', True) is not False, \
                    f'{where}: unexpected field {key}'
    if isinstance(value, list) and 'items' in schema:
        for i, item in enumerate(value):
            check(item, schema['items'], root, f'{where}[{i}]')


def conforms(value, name):
    doc = agentdocs.openapi('https://example.org')
    check(value, {'$ref': f'#/components/schemas/{name}'}, doc)


def assert_json_schema(schema, where):
    """A tool's input schema must itself be a JSON Schema object."""
    assert isinstance(schema, dict) and schema.get('type') == 'object', where
    assert isinstance(schema.get('properties'), dict), where
    assert isinstance(schema.get('required'), list), where
    assert set(schema['required']) <= set(schema['properties']), where
    for name, prop in schema['properties'].items():
        assert isinstance(prop, dict), f'{where}.{name}'
        assert prop.get('type') in ('string', 'integer', 'number', 'boolean'), f'{where}.{name}'
        assert isinstance(prop.get('description'), str) and prop['description'], f'{where}.{name}'
        if 'enum' in prop:
            assert isinstance(prop['enum'], list) and prop['enum']


# ---------------------------------------------------------------- market

def test_market_shape_units_and_headers():
    status, headers, m = get('/api/v1/market')
    assert status == 200
    assert headers['Access-Control-Allow-Origin'] == '*'
    assert headers['Cache-Control'] == 'public, max-age=5'
    assert headers['Content-Type'].startswith('application/json')
    conforms(m, 'Market')
    assert m['api_version'] == 'v1'
    assert m['generated_at'].endswith('Z') and abs(m['generated_at_unix'] - time.time()) < 5
    price = m['price']
    assert price['btc_per_mars'] == pytest.approx(0.0612 / 68_000)
    assert price['sats_per_mars'] == pytest.approx(90.0)
    assert price['usd_per_mars'] == 0.0612 and price['fresh'] is True
    assert 19 <= price['age_seconds'] <= 25
    btc, mars = m['chains']['bitcoin'], m['chains']['marscoin']
    assert (mars['height'], btc['height']) == (MARS_H, BTC_H)
    assert 295 <= btc['tip_age_seconds'] <= 310 and 85 <= mars['tip_age_seconds'] <= 100
    assert btc['fee_rate_fast_sat_per_vb'] == 4 and btc['fee_rate_hour_sat_per_vb'] == 2
    assert m['maker'] == {'mode': 'live', 'live': True, 'open_offers': 2}
    assert m['book'] == {'open_offers': 4, 'open_mars_zubrins': 1100 * COIN, 'safe_open_offers': 3}


def test_market_without_data_says_so():
    _, _, m = get('/api/v1/market', service=FakeService(market=None, mars_height=None,
                                                         mars_tip_time=None, mode='watch'))
    conforms(m, 'Market')
    assert m['price']['btc_per_mars'] is None and m['price']['fresh'] is False
    assert m['chains']['bitcoin']['height'] is None and m['chains']['marscoin']['height'] is None
    assert m['maker']['live'] is False
    assert m['book']['safe_open_offers'] == 0          # no heights: nothing is known to be safe


def test_stale_price_is_not_fresh():
    st = state()
    st['market'].prices_at = time.time() - 601
    assert agentapi.market_view(st, time.time())['price']['fresh'] is False


# ---------------------------------------------------------------- offers

def test_offers_shape_and_units():
    _, headers, body = get('/api/v1/offers')
    conforms(body, 'OfferList')
    assert headers['Cache-Control'] == 'public, max-age=5'
    assert body['count'] == 4 and body['open_offers_total'] == 4
    assert (body['marscoin_height'], body['bitcoin_height']) == (MARS_H, BTC_H)
    assert 'whole' in body['note']
    ids = [o['id'] for o in body['offers']]
    assert ids == [C, A, B, D]                                   # cheapest first
    a = body['offers'][1]
    assert a['mars_amount_zubrins'] == 100 * COIN and isinstance(a['mars_amount_zubrins'], int)
    assert a['btc_amount_sats'] == 9_450 and isinstance(a['btc_amount_sats'], int)
    assert a['price_btc_per_mars'] == pytest.approx(9_450 / (100 * COIN))
    assert a['price_sats_per_mars'] == pytest.approx(94.5)
    assert a['premium_percent'] == pytest.approx(5.0)            # 94.5 against 90.0
    assert a['mars_locktime_height'] == SAFE_LOCK and a['mars_locktime_blocks_left'] == 470
    assert 590 <= a['expires_in_seconds'] <= 600
    assert a['expires_at_unix'] - body['generated_at_unix'] in range(590, 602)
    assert a['maker_pubkey'] == '02' + 'a1' * 32 and a['payment_hash160'] == 'a1' * 20
    assert a['from_this_maker'] is True and a['relays_seen'] == 2 and a['status'] == 'open'
    # Nothing that identifies or exposes a party beyond what a taker needs.
    assert 'MsecretAddressNotShown' not in json.dumps(body)
    assert not {'ours', 'taken', 'maker', 'relays'} & set(a)


def test_safety_is_policy():
    _, _, body = get('/api/v1/offers')
    for o in body['offers']:
        expect = policy.offer_safety(o['mars_locktime_height'], MARS_H, BTC_H)
        assert o['safety'] == {'safe': expect['safe'], 'margin_seconds': expect['margin_sec'],
                               'min_margin_seconds': policy.MIN_REFUND_GAP_SEC,
                               'reason': expect['reason']}
    by_id = {o['id']: o for o in body['offers']}
    assert by_id[A]['safety']['safe'] is True
    assert by_id[A]['safety']['margin_seconds'] == round(470 * 123 * 0.5 - 24 * 600 * 1.25)
    assert by_id[C]['safety']['safe'] is False and by_id[C]['safety']['reason']


def test_safety_unknown_without_heights():
    _, _, body = get('/api/v1/offers?safe_only=true', service=FakeService(mars_height=None))
    assert body['count'] == 0 and body['open_offers_total'] == 4
    _, _, body = get('/api/v1/offers', service=FakeService(mars_height=None))
    assert all(o['safety']['safe'] is None and o['mars_locktime_blocks_left'] is None
               for o in body['offers'])


def test_offer_filters_and_sorting():
    def ids(query):
        return [o['id'] for o in get('/api/v1/offers?' + query)[2]['offers']]
    assert ids('min_mars=200') == [C, B, D]
    assert ids('max_mars=200') == [A, B]
    assert ids('min_mars=150.5&max_mars=300') == [C, B]
    assert ids('safe_only=true') == [A, B, D]
    assert ids('safe_only=1&sort=size') == [D, B, A]
    assert ids('sort=size') == [D, C, B, A]
    assert ids('min_mars=501') == []


def test_taken_and_malformed_offers():
    svc = FakeService(taken={A})
    svc.st['offers'][B]['raw']['maker_pubkey'] = 'not-a-key'
    svc.st['offers']['junk'] = {'raw': {'offer_id': 'junk'}, 'relays': set()}
    svc.st['offers']['e5' * 16] = raw('e5' * 16, 100, 9_000, expires=-5)
    (_, _, listing), (_, _, safe), (_, _, taken) = fetch(
        '/api/v1/offers', '/api/v1/offers?safe_only=true', f'/api/v1/offers/{A}', service=svc)
    assert [o['id'] for o in listing['offers']] == [C, B, D]     # taken, junk and expired are gone
    b = listing['offers'][1]
    assert b['maker_pubkey'] is None and b['well_formed'] is False
    assert [o['id'] for o in safe['offers']] == [D]
    assert taken['offer']['status'] == 'taken'


def test_offer_by_id_and_prefix():
    (s1, _, full), (s2, _, prefix), (s3, _, missing), (s4, _, bad) = fetch(
        f'/api/v1/offers/{B}', f'/api/v1/offers/{B[:12]}', '/api/v1/offers/' + 'ff' * 16,
        '/api/v1/offers/xyz')
    assert s1 == s2 == 200 and full['offer']['id'] == prefix['offer']['id'] == B
    conforms(full, 'OfferDetail')
    assert s3 == 404 and missing['error']['code'] == 'offer_not_found'
    assert s4 == 400 and bad['error']['code'] == 'invalid_parameter'
    svc = FakeService()
    svc.st['offers'][A[:10] + 'f' * 22] = raw(A[:10] + 'f' * 22, 10, 950)
    status, _, body = get(f'/api/v1/offers/{A[:8]}', service=svc)
    assert status == 400 and body['error']['code'] == 'ambiguous_id'
    assert get(f'/api/v1/offers/{A}', service=svc)[2]['offer']['id'] == A   # exact id still works


# ---------------------------------------------------------------- quote

def quote(query, **kw):
    status, _, body = get('/api/v1/quote?' + query, **kw)
    assert status == 200, body
    conforms(body, 'Quote')
    return body


def test_quote_for_mars_takes_whole_offers():
    q = quote('mars=350')
    # Cheapest safe first: A (100) and B (200) fit; D (500) is too big and is never cut.
    assert [f['offer_id'] for f in q['fills']] == [A, B]
    assert q['total_mars_zubrins'] == 300 * COIN and q['total_btc_sats'] == 9_450 + 19_260
    assert q['unfilled_mars_zubrins'] == 50 * COIN and q['unspent_btc_sats'] is None
    assert q['fully_filled'] is False
    assert q['average_price_sats_per_mars'] == pytest.approx(28_710 / 300)
    assert q['average_price_btc_per_mars'] == pytest.approx(28_710 / (300 * COIN))
    assert q['mars_claim_fee_zubrins'] == 200_000
    assert q['net_mars_zubrins'] == 300 * COIN - 200_000
    # The whole amount in one swap: only D covers it, with 150 MARS more than asked.
    alt = q['alternative_single_offer']
    assert alt['offer_id'] == D and alt['excess_mars_zubrins'] == 150 * COIN
    assert q['offers_considered'] == 3 and q['offers_excluded'] == 1     # C is unsafe
    assert any('whole' in n for n in q['notes'])


def test_quote_exact_and_too_small():
    q = quote('mars=300')
    assert [f['offer_id'] for f in q['fills']] == [A, B] and q['fully_filled'] is True
    assert q['unfilled_mars_zubrins'] == 0 and q['alternative_single_offer'] is None
    q = quote('mars=99.99999999')                    # smaller than every offer: nothing is cut
    assert q['fills'] == [] and q['total_btc_sats'] == 0 and q['fully_filled'] is False
    assert q['unfilled_mars_zubrins'] == 9_999_999_999
    assert q['average_price_btc_per_mars'] is None
    assert q['alternative_single_offer']['offer_id'] == A
    q = quote('mars=5000')                           # more than the whole book
    assert q['total_mars_zubrins'] == 800 * COIN and q['unfilled_mars_zubrins'] == 4200 * COIN
    assert q['alternative_single_offer'] is None


def test_quote_for_a_btc_budget():
    q = quote('btc_sats=30000')
    assert [f['offer_id'] for f in q['fills']] == [A, B]
    assert q['total_btc_sats'] == 28_710 and q['unspent_btc_sats'] == 1_290
    assert q['unfilled_mars_zubrins'] is None and q['request'] == {'btc_sats': 30_000}
    q = quote('btc_sats=9449')                       # one satoshi short of the cheapest
    assert q['fills'] == [] and q['unspent_btc_sats'] == 9_449 and q['total_mars_zubrins'] == 0
    q = quote('btc_sats=60000')                      # skips B after A? No: A, B, then D does not fit
    assert [f['offer_id'] for f in q['fills']] == [A, B] and q['unspent_btc_sats'] == 31_290
    q = quote('btc_sats=78000')
    assert [f['offer_id'] for f in q['fills']] == [A, B, D] and q['unspent_btc_sats'] == 290


def test_quote_never_invents_liquidity():
    for svc in (FakeService(offers={}), FakeService(mars_height=None),
                FakeService(taken={A, B, C, D})):
        for query in ('mars=100', 'btc_sats=1000000'):
            q = quote(query, service=svc)
            assert q['fills'] == [] and q['total_mars_zubrins'] == 0 and q['total_btc_sats'] == 0
            assert q['fully_filled'] is False and q['offers_considered'] == 0
            assert 'nothing can be filled' in q['notes'][0]


def test_quote_parameter_errors():
    for query in ('', 'mars=1&btc_sats=1', 'mars=abc', 'mars=0', 'mars=-5', 'mars=1.123456789',
                  'btc_sats=1.5', 'btc_sats=0', 'btc_sats=-3', 'mars=NaN', 'mars=1e400'):
        status, headers, body = get('/api/v1/quote?' + query)
        assert status == 400, query
        assert set(body) == {'error'} and set(body['error']) == {'code', 'message'}
        assert body['error']['code'] == 'invalid_parameter' and body['error']['message']
        assert headers['Access-Control-Allow-Origin'] == '*'
        assert headers['Cache-Control'] == 'no-store'
        conforms(body, 'Error')


# ---------------------------------------------------------------- swaps

def test_recent_swaps_are_amounts_and_times_only():
    _, _, body = get('/api/v1/swaps/recent')
    conforms(body, 'RecentSwaps')
    assert body['count'] == 1
    s = body['swaps'][0]
    assert s['id'] == 'aaaaaaaa' and s['mars_amount_zubrins'] == 150 * COIN
    assert s['btc_amount_sats'] == 14_200 and s['seconds_from_offer_to_btc_claim'] == 3100
    assert s['btc_claimed_at_unix'] - s['offer_created_at_unix'] in (3099, 3100, 3101)
    assert not [k for k in s if 'txid' in k or 'key' in k or 'address' in k or 'preimage' in k]
    assert body['totals'] == {'completed': 1, 'refunded': 1, 'mars_sold_zubrins': 150 * COIN,
                              'btc_received_sats': 14_200}
    assert get('/api/v1/swaps/recent?limit=1')[0] == 200
    for bad in ('0', '101', 'x'):
        status, _, err = get('/api/v1/swaps/recent?limit=' + bad)
        assert status == 400 and err['error']['code'] == 'invalid_parameter'


def test_recent_swaps_empty():
    _, _, body = get('/api/v1/swaps/recent', service=FakeService(maker_swaps=[]))
    assert body['swaps'] == [] and body['totals']['completed'] == 0


# ---------------------------------------------------------------- protocol

def test_protocol_document():
    status, headers, p = get('/api/v1/protocol')
    assert status == 200 and headers['Cache-Control'] == 'public, max-age=60'
    conforms(p, 'Protocol')
    assert p['transport']['relays'] == [
        {'host': 'electrum.marscoin.org', 'port': 50002, 'tls_certificate_verifiable': True},
        {'host': '147.182.177.23', 'port': 50002, 'tls_certificate_verifiable': False}]
    methods = {m['method'] for m in p['relay_methods']}
    assert {'atomicswap.get_offers', 'atomicswap.accept_offer', 'atomicswap.get_acceptance',
            'blockchain.scripthash.listunspent', 'blockchain.transaction.get_merkle'} <= methods
    assert set(p['acceptance_format']['fields']) == {
        'taker_pubkey', 'btc_htlc_address', 'btc_locktime', 'timestamp'}
    rule = p['taker_checks']['refund_order']
    assert (rule['mars_fast'], rule['btc_slow'], rule['min_gap_seconds']) == (
        policy.MARS_FAST, policy.BTC_SLOW, policy.MIN_REFUND_GAP_SEC)
    assert p['taker_checks']['bitcoin_window']['min_window_seconds'] == policy.MIN_BTC_WINDOW_SEC
    assert p['timelocks']['bitcoin_refund_blocks'] == policy.BTC_TIMELOCK_BLOCKS
    assert p['timelocks']['marscoin_refund_blocks_this_maker'] == policy.MARS_MAKER_TIMELOCK_BLOCKS
    assert p['confirmations']['marscoin_lock_before_taker_pays'] == 2
    assert p['htlc']['marscoin_contract']['recipient_pubkey'] == 'taker'
    assert p['htlc']['bitcoin_contract']['recipient_pubkey'] == 'maker'
    assert p['unconfirmed'] and len(p['recovery']) >= 3
    assert any('no endpoint' in line for line in p['what_this_server_does_not_do'])
    for section in ('transport', 'offer_format', 'acceptance_format', 'htlc', 'timelocks',
                    'confirmations', 'fees', 'relay_limits'):
        assert p[section]['source'], section


def test_protocol_matches_the_swap_code():
    """The numbers and the script in the document are the engine's own."""
    htlc = pytest.importorskip('electrum_mars.atomic_swap_htlc')
    safety = pytest.importorskip('electrum_mars.plugins.atomic_swap.safety')
    from electrum_mars.plugins.atomic_swap import swap_worker
    from electrum_mars.plugins.atomic_swap.orderbook import SwapOffer
    p = agentprotocol.protocol_view(state(), time.time())
    assert p['timelocks']['bitcoin_refund_blocks'] == htlc.BTC_TIMELOCK_BLOCKS
    assert p['timelocks']['marscoin_refund_blocks_this_maker'] == htlc.MARS_MAKER_TIMELOCK_BLOCKS
    assert p['timelocks']['marscoin_refund_blocks_older_makers'] == htlc.MARS_TIMELOCK_BLOCKS
    assert p['confirmations']['marscoin_lock_before_taker_pays'] == safety.MARS_MIN_CONFIRMATIONS
    assert (p['confirmations']['maker_claim_cutoff_blocks_before_bitcoin_refund']
            == safety.MAKER_CLAIM_MARGIN_BLOCKS)
    assert p['taker_checks']['bitcoin_window']['min_window_seconds'] == safety.MIN_BTC_WINDOW_SEC
    assert p['taker_checks']['refund_order']['min_gap_seconds'] == safety.MIN_REFUND_GAP_SEC
    assert p['taker_checks']['refund_order']['btc_slow'] == safety.BTC_SLOW
    assert p['this_maker']['acceptance_poll_seconds'] == swap_worker.POLL_INTERVAL_SEC
    assert set(p['offer_format']['fields']) == set(SwapOffer.__dataclass_fields__)
    # The documented byte layout is what create_htlc_script produces.
    script = htlc.create_htlc_script(bytes(range(20)), b'\x02' + b'\x11' * 32,
                                     b'\x03' + b'\x22' * 32, 3_700_480).hex()
    locktime = (3_700_480).to_bytes(3, 'little').hex()
    assert script == ('63a914' + bytes(range(20)).hex() + '8821' + '02' + '11' * 32 + 'ac67'
                      + '03' + locktime + 'b17521' + '03' + '22' * 32 + 'ac68')
    assert agentprotocol.HTLC_HEX.replace(' ', '').startswith('63a914')


# ---------------------------------------------------------------- errors, methods, caching

def test_errors_are_json_everywhere_under_v1():
    status, headers, body = get('/api/v1/nothing-here')
    assert status == 404 and body == {'error': {'code': 'not_found',
                                                'message': 'no such endpoint; see /openapi.json'}}
    assert headers['Access-Control-Allow-Origin'] == '*'
    for path in [e['path'].replace('{id}', A) for e in agentdocs.ENDPOINTS]:
        for method in ('post', 'put', 'delete', 'patch'):
            status, _, body = get(path, method=method)
            assert status == 405 and body['error']['code'] == 'method_not_allowed', (method, path)


def test_no_write_endpoints_anywhere():
    paths = ['/llms.txt', '/openapi.json', '/.well-known/agent.json', '/api/agent', '/api/tools']
    for path in paths:
        assert get(path, method='post')[0] == 405, path
    app = make_app(FakeService())
    methods = {r.method for r in app.router.routes()}
    assert methods <= {'GET', 'HEAD'}


def test_a_broken_view_answers_with_a_json_error():
    class Broken(FakeService):
        def agent_state(self):
            raise RuntimeError('database is locked')
    status, _, body = get('/api/v1/market', service=Broken())
    assert status == 500 and body['error']['code'] == 'internal_error'
    assert 'database' not in json.dumps(body)


def test_state_is_read_once_per_revision():
    class Counting(FakeService):
        calls = 0

        def agent_state(self):
            Counting.calls += 1
            return self.st
    svc = Counting()
    fetch('/api/v1/market', '/api/v1/offers', '/api/v1/quote?mars=100', service=svc)
    assert Counting.calls == 1
    from atomicswapd.agentroutes import AgentState
    cache = AgentState(svc)
    cache.get(), cache.get()
    assert Counting.calls == 2
    svc.revision = 3
    cache.get()
    assert Counting.calls == 3


def test_desktop_mode_has_no_agent_api():
    async def go():
        app = make_app(FakeService(), desktop_token='t' * 32)
        async with TestClient(TestServer(app)) as client:
            for path in ('/llms.txt', '/openapi.json', '/.well-known/agent.json', '/api/tools'):
                r = await client.get(path)
                assert r.status in (401, 404), path
                assert 'Access-Control-Allow-Origin' not in r.headers
    asyncio.run(go())


# ---------------------------------------------------------------- discovery documents

def test_llms_txt():
    status, headers, text = get('/llms.txt')
    assert status == 200 and headers['Content-Type'].startswith('text/plain')
    assert headers['Access-Control-Allow-Origin'] == '*'
    assert headers['Cache-Control'] == 'public, max-age=300'
    assert text.startswith('# ')
    for e in agentdocs.ENDPOINTS + agentdocs.OTHER_ENDPOINTS:
        assert e['path'] in text, e['path']
    for word in ('zubrins', 'satoshis', 'taken whole', 'read-only', 'own keys',
                 f'{policy.MARS_MAKER_TIMELOCK_BLOCKS} Marscoin blocks',
                 f'{policy.BTC_TIMELOCK_BLOCKS} Bitcoin blocks', 'What can go wrong'):
        assert word in text, word
    assert len(text) < 8000                       # an orientation, not a manual


def test_agent_json():
    (s1, h1, a), (s2, _, b) = fetch('/.well-known/agent.json', '/api/agent')
    assert s1 == s2 == 200 and a == b
    assert h1['Access-Control-Allow-Origin'] == '*' and 'max-age=300' in h1['Cache-Control']
    conforms(a, 'AgentDescription')
    base = a['base_url']
    assert base.startswith('http://127.0.0.1:')
    assert a['openapi_url'] == base + '/openapi.json' and a['tools_url'] == base + '/api/tools'
    assert {e['path'] for e in a['endpoints']} == {
        e['path'] for e in agentdocs.ENDPOINTS + agentdocs.OTHER_ENDPOINTS}
    assert all(e['method'] == 'GET' for e in a['endpoints'])
    assert a['read_only'] is True and a['authentication'] == 'none'
    assert a['capabilities']['holds_user_funds'] is False
    assert a['capabilities']['accepts_offers'] is False
    assert a['limits']['partial_fills'] is False and a['limits']['suggested_poll_seconds'] >= 10
    assert 'zubrin' in a['units'] and a['contact']['issues'].startswith('https://')


def test_base_url_from_config_or_proxy():
    _, _, a = get('/api/agent', service=FakeService(public_url='https://swaps.example.org'))
    assert a['base_url'] == 'https://swaps.example.org'
    assert a['endpoints'][0]['url'] == 'https://swaps.example.org/api/v1/market'
    _, headers, a = get('/api/agent', headers={'X-Forwarded-Proto': 'https',
                                               'Host': 'swaps.example.net'})
    assert a['base_url'] == 'https://swaps.example.net' and 'Host' in headers['Vary']
    _, _, a = get('/api/agent', headers={'Host': 'evil.example/"><script>'})
    assert a['base_url'].endswith('://localhost')
    cfg = config.load(env={'ATOMICSWAPD_PUBLIC_URL': 'https://swaps.example.org/'})
    assert cfg.public_url == 'https://swaps.example.org'
    with pytest.raises(config.ConfigError):
        config.load(env={'ATOMICSWAPD_PUBLIC_URL': 'swaps.example.org'})


def test_openapi_is_valid_and_complete():
    status, headers, doc = get('/openapi.json')
    assert status == 200 and headers['Access-Control-Allow-Origin'] == '*'
    assert doc['openapi'].startswith('3.1') and doc['info']['title'] and doc['info']['version']
    assert doc['servers'][0]['url'].startswith('http')
    # Every route the server has is documented, and nothing else.
    app = make_app(FakeService())
    served = {r.resource.canonical for r in app.router.routes()} - {'/'}
    assert set(doc['paths']) == served
    operation_ids = []
    for path, item in doc['paths'].items():
        assert set(item) == {'get'}, path
        op = item['get']
        operation_ids.append(op['operationId'])
        assert op['summary'] and '200' in op['responses']
        for p in op['parameters']:
            assert p['in'] in ('query', 'path') and p['schema'] and p['description']
            assert p['in'] != 'path' or '{%s}' % p['name'] in path
        for code, resp in op['responses'].items():
            for media in resp['content'].values():
                schema = media['schema']
                if '$ref' in schema:
                    name = schema['$ref'].rsplit('/', 1)[1]
                    assert name in doc['components']['schemas'], name
                if 'example' in media:
                    json.loads(json.dumps(media['example']))           # plain JSON
                    check(media['example'], schema, doc, f'{path} {code} example')
    assert len(operation_ids) == len(set(operation_ids))
    # Every v1 endpoint carries an example, and every reference resolves.
    for e in agentdocs.ENDPOINTS:
        assert 'example' in doc['paths'][e['path']]['get']['responses']['200']['content'][
            'application/json'], e['path']
    for ref in _refs(doc):
        assert ref.startswith('#/components/schemas/')
        assert ref.rsplit('/', 1)[1] in doc['components']['schemas'], ref


def _refs(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == '$ref':
                yield value
            else:
                yield from _refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _refs(item)


def test_live_responses_match_the_openapi_schemas():
    paths = {'/api/v1/market': 'Market', '/api/v1/offers': 'OfferList',
             f'/api/v1/offers/{A}': 'OfferDetail', '/api/v1/quote?mars=250': 'Quote',
             '/api/v1/quote?btc_sats=20000': 'Quote', '/api/v1/swaps/recent': 'RecentSwaps',
             '/api/v1/protocol': 'Protocol', '/api/tools': 'ToolDefinitions',
             '/api/agent': 'AgentDescription', '/api/history': 'History', '/healthz': 'Health'}
    for (status, _, body), (path, schema) in zip(fetch(*paths), paths.items()):
        assert status == 200, path
        conforms(body, schema)


def test_tools():
    status, headers, t = get('/api/tools')
    assert status == 200 and headers['Access-Control-Allow-Origin'] == '*'
    conforms(t, 'ToolDefinitions')
    names = [tool['name'] for tool in t['tools']]
    assert names == ['get_market', 'list_offers', 'get_offer', 'get_quote', 'list_recent_swaps',
                     'get_protocol']
    assert len(set(names)) == len(names)
    assert [f['function']['name'] for f in t['functions']] == names
    for tool, fn in zip(t['tools'], t['functions']):
        assert set(tool) == {'name', 'description', 'input_schema'}
        assert set(fn) == {'type', 'function'} and fn['type'] == 'function'
        assert set(fn['function']) == {'name', 'description', 'parameters'}
        assert tool['name'].replace('_', '').isalnum() and len(tool['name']) <= 64
        assert len(tool['description']) > 80
        assert fn['function']['description'] == tool['description']
        assert fn['function']['parameters'] == tool['input_schema']
        assert_json_schema(tool['input_schema'], tool['name'])
    by_name = {tool['name']: tool for tool in t['tools']}
    assert by_name['get_offer']['input_schema']['required'] == ['id']
    assert set(by_name['list_offers']['input_schema']['properties']) == {
        'min_mars', 'max_mars', 'safe_only', 'sort'}
    assert set(by_name['get_quote']['input_schema']['properties']) == {'mars', 'btc_sats'}
    # Honest descriptions: read-only, whole offers, no promise of execution.
    assert 'taken whole' in by_name['get_quote']['description']
    assert 'never invents liquidity' in by_name['get_quote']['description']
    assert 'cannot take an offer' in by_name['get_protocol']['description']
    # Each tool says how to run it, and running it works.
    base = t['base_url']
    for name, how in t['execution'].items():
        assert how['method'] == 'GET' and how['url'].startswith(base + '/api/v1/')
        props = set(by_name[name]['input_schema']['properties'])
        assert set(how['path_parameters']) | set(how['query_parameters']) == props


def test_examples_come_from_the_real_views():
    ex = agentdocs.examples()
    assert [f['offer_id'] for f in ex['Quote']['fills']] == ['a1' * 16]
    assert ex['Quote']['unfilled_mars_zubrins'] == 150 * COIN
    assert ex['Market']['generated_at'] == '2026-11-06T21:20:00Z'
    assert ex['Market']['generated_at_unix'] == agentdocs.EXAMPLE_NOW


# ---------------------------------------------------------------- the real service

def test_service_state_feeds_the_views_and_leaks_nothing():
    from types import SimpleNamespace
    from atomicswapd.service import Service
    svc = Service(config.Config())
    now = time.time()
    # Before anything is connected every view still answers.
    st = svc.agent_state()
    for view in (agentapi.market_view(st, now), agentapi.offers_view(st, now, {}),
                 agentapi.quote_view(st, now, {'mars': '10'}),
                 agentapi.recent_swaps_view(st, now, {})):
        json.dumps(view)
    assert agentapi.market_view(st, now)['maker']['live'] is False

    secret = SimpleNamespace(
        swap_id='5e' * 16, role='maker', state='btc_claimed', mars_amount_sat=100 * COIN,
        btc_amount_sat=9_000, created_at=now - 5000, updated_at=now - 1000,
        peer_pubkey='03' + 'cd' * 32, my_privkey='PRIVKEY' * 4, preimage='PREIMAGE' * 4,
        btc_receive_address='bc1qPAYOUT', mars_funding_txid='TXID' * 16)
    svc.engine = SimpleNamespace(get_all_swaps=lambda: [secret])
    svc.offers = {A: raw(A, 100, 9_450)}
    st = svc.agent_state()
    assert st['maker_swaps'][0] == {
        'id': '5e5e5e5e', 'state': 'btc_claimed', 'mars_amount_sat': 100 * COIN,
        'btc_amount_sat': 9_000, 'created_at': now - 5000, 'updated_at': now - 1000,
        'accepted': True}
    everything = json.dumps([agentapi.recent_swaps_view(st, now, {}),
                             agentapi.offers_view(st, now, {}), agentapi.market_view(st, now),
                             agentprotocol.protocol_view(st, now)])
    for forbidden in ('PRIVKEY', 'PREIMAGE', 'bc1qPAYOUT', 'TXID', 'MsecretAddress', 'cdcdcd'):
        assert forbidden not in everything, forbidden
    assert [r['host'] for r in st['relays']] == [s.host for s in config.DEFAULT_SERVERS]
