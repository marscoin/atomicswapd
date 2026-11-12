"""The reference client (agent/marsswap_agent) against a running node API."""

import ast
import asyncio
import contextlib
import io
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from aiohttp import web

AGENT_DIR = Path(__file__).parent.parent / 'agent'
sys.path.insert(0, str(AGENT_DIR))

from marsswap_agent import cli, htlc, mcp, safety          # noqa: E402
from marsswap_agent.client import ApiError, Client, NetworkError, http_get   # noqa: E402

from atomicswapd import policy                                      # noqa: E402
from atomicswapd.api import make_app                                # noqa: E402
from test_agent_api import A, B, C, D, BTC_H, MARS_H, FakeService   # noqa: E402

COIN = 100_000_000


@contextlib.contextmanager
def node(service=None):
    """The real HTTP app with a fake service, on a local port."""
    loop = asyncio.new_event_loop()
    runner = web.AppRunner(make_app(service or FakeService()), access_log=None)
    started, where = threading.Event(), {}

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(runner.setup())
        loop.run_until_complete(web.TCPSite(runner, '127.0.0.1', 0).start())
        where['url'] = f'http://127.0.0.1:{runner.addresses[0][1]}'
        started.set()
        loop.run_forever()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert started.wait(5)
    try:
        yield where['url']
    finally:
        asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result(5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)


def run_cli(url, *argv, fetch=http_get):
    out = io.StringIO()
    code = cli.main([*argv, '--url', url], fetch=fetch, out=out)
    return code, out.getvalue()


def tampering(change):
    """A fetch that lets ``change(path, data)`` rewrite the server's JSON."""
    def fetch(url, timeout=15):
        status, body = http_get(url, timeout)
        data = json.loads(body)
        change(url, data)
        return status, json.dumps(data).encode()
    return fetch


# ---------------------------------------------------------------- reading

def test_client_reads_every_endpoint():
    with node() as url:
        c = Client(url)
        assert c.market()['chains']['marscoin']['height'] == MARS_H
        assert [o['id'] for o in c.offers()['offers']] == [C, A, B, D]
        assert [o['id'] for o in c.offers(min_mars='200', safe_only=True, sort='size')['offers']] \
            == [D, B]
        assert c.offer(A[:12])['offer']['id'] == A
        assert c.quote(mars='300')['fully_filled'] is True
        assert c.quote(btc_sats=30000)['unspent_btc_sats'] == 1290
        assert c.recent_swaps(limit=5)['count'] == 1
        assert c.protocol()['timelocks']['bitcoin_refund_blocks'] == 24
        assert len(c.tools()['tools']) == 6
        with pytest.raises(ApiError) as e:
            c.offer('ff' * 16)
        assert e.value.code == 'offer_not_found' and e.value.status == 404
        with pytest.raises(ApiError) as e:
            c.quote()
        assert e.value.code == 'invalid_parameter'


def test_client_reports_an_unreachable_node():
    with pytest.raises(NetworkError):
        Client('http://127.0.0.1:1').market()
    with pytest.raises(NetworkError):
        Client('file:///etc/passwd').market()
    not_json = lambda url, timeout=15: (200, b'<html>')        # noqa: E731
    with pytest.raises(NetworkError):
        Client('http://x', fetch=not_json).market()


# ---------------------------------------------------------------- command line

def test_cli_market_offers_quote():
    with node() as url:
        code, text = run_cli(url, 'market')
        assert code == 0 and '90.0 sats/MARS' in text and f'block {MARS_H}' in text
        assert 'maker: live' in text and '3 safe to take' in text
        code, text = run_cli(url, 'offers', '--safe-only')
        assert code == 0 and A in text and C not in text and 'taken whole' in text
        code, text = run_cli(url, 'offers')
        assert 'UNSAFE' in text
        code, text = run_cli(url, 'offers', '--min-mars', '9999')
        assert code == 0 and 'no offers match' in text
        code, text = run_cli(url, 'quote', '--mars', '350')
        assert code == 0 and f'take {A}' in text and f'take {B}' in text
        assert 'unfilled: 50 MARS' in text and D in text
        code, text = run_cli(url, 'quote', '--btc-sats', '100')
        assert code == 1 and 'nothing fits' in text
        code, text = run_cli(url, 'quote', '--mars', '300', '--json')
        assert code == 0 and json.loads(text)['total_btc_sats'] == 28_710
        code, text = run_cli(url, 'protocol')
        assert code == 0 and 'taker_checks' in json.loads(text)


def test_cli_errors_exit_2(capsys):
    with node() as url:
        assert run_cli(url, 'check-offer', 'ff' * 16)[0] == 2
        assert 'offer_not_found' in capsys.readouterr().err
        assert run_cli(url, 'quote', '--mars', 'lots')[0] == 2
        assert run_cli(url, 'check-offer', A, '--taker-pubkey', '11' * 32)[0] == 2
        assert 'never pass a private key' in capsys.readouterr().err
    assert run_cli('http://127.0.0.1:1', 'market')[0] == 2
    assert cli.main(['quote']) == 2                      # neither --mars nor --btc-sats
    assert cli.main(['frobnicate']) == 2


def test_module_entry_point():
    with node() as url:
        done = subprocess.run([sys.executable, '-m', 'marsswap_agent', 'quote', '--btc-sats',
                               '10', '--url', url], cwd=AGENT_DIR, capture_output=True,
                              text=True, timeout=30)
        assert done.returncode == 1 and 'nothing fits' in done.stdout
        done = subprocess.run([sys.executable, '-m', 'marsswap_agent', 'check-offer', A,
                               '--no-independent', '--url', url], cwd=AGENT_DIR, capture_output=True, text=True,
                              timeout=30)
        assert done.returncode == 0 and 'SAFE TO ACCEPT' in done.stdout


# ---------------------------------------------------------------- check-offer

def test_check_offer_safe_and_unsafe():
    with node() as url:
        code, text = run_cli(url, 'check-offer', A, '--no-independent')
        assert code == 0 and 'verdict: SAFE TO ACCEPT' in text
        assert '--no-independent' in text and 'verify the Marscoin lock' in text
        code, text = run_cli(url, 'check-offer', C, '--no-independent')
        assert code == 1 and 'verdict: NOT SAFE' in text and '[FAIL] refund_order' in text
        code, text = run_cli(url, 'check-offer', A, '--no-independent', '--json')
        r = json.loads(text)
        assert r['safe'] is True and r['exit_code'] == 0 and r['disagreements'] == []
        assert r['margin_seconds'] == r['server_safety']['margin_seconds'] == 10_905
        assert [c['name'] for c in r['checks']] == [
            'offer_form', 'open', 'refund_order', 'bitcoin_window', 'fee_viability']


def test_check_offer_does_not_trust_the_server():
    def says_safe(url, data):                # the server calls an unsafe offer safe
        if '/offers/' in url:
            data['offer']['safety'].update(safe=True, margin_seconds=99_999, reason=None)

    def wrong_margin(url, data):
        if '/offers/' in url:
            data['offer']['safety']['margin_seconds'] += 600

    def other_rules(url, data):
        if url.endswith('/protocol'):
            data['taker_checks']['refund_order']['mars_fast'] = 0.9

    def stale_height(url, data):             # an old Marscoin height makes the lock look long
        if '/offers/' in url:
            data['marscoin_height'] -= 400
            data['offer']['safety'].update(safe=True, margin_seconds=15_825, reason=None)

    with node() as url:
        code, text = run_cli(url, 'check-offer', C, '--no-independent',
                             fetch=tampering(says_safe))
        assert code == 3 and 'DISAGREEMENT' in text and 'verdict: NOT SAFE' in text
        code, text = run_cli(url, 'check-offer', A, '--no-independent',
                             fetch=tampering(wrong_margin))
        assert code == 3 and 'DISAGREEMENT' in text and 'Do not act' in text
        code, text = run_cli(url, 'check-offer', A, '--no-independent',
                             fetch=tampering(other_rules))
        assert code == 3 and 'DIFFERENT RULES: mars_fast' in text
        # Fooled without a second source...
        code, _ = run_cli(url, 'check-offer', C, '--no-independent',
                          fetch=tampering(stale_height))
        assert code == 0
        # ...and not fooled with one.
        both = second_sources(tampering(stale_height), mars=MARS_H)
        code, text = run_cli(url, 'check-offer', C, fetch=both)
        assert code == 1 and 'the sources disagree' in text and 'verdict: UNKNOWN' in text


def second_sources(inner, mars=None, btc=(BTC_H, BTC_H)):
    def fetch(url, timeout=15):
        if 'explore.marscoin.org' in url:
            if mars is None:
                raise NetworkError('explorer down')
            return 200, json.dumps({'info': {'blocks': mars}}).encode()
        if 'blocks/tip/height' in url:
            return 200, str(btc[0 if 'mempool' in url else 1]).encode()
        return inner(url, timeout)
    return fetch


def test_check_offer_with_independent_heights():
    with node() as url:
        code, text = run_cli(url, 'check-offer', A,
                             fetch=second_sources(http_get, mars=MARS_H + 2))
        assert code == 0 and f'second source {MARS_H + 2}' in text
        assert f'Marscoin {MARS_H + 2}' in text            # the higher tip is the one used
        code, text = run_cli(url, 'check-offer', A,
                             fetch=second_sources(http_get, mars=None))
        assert code == 1 and 'verdict: UNKNOWN' in text    # no second source: not "safe"


def test_check_offer_prints_the_contracts_for_a_public_key():
    taker = '03' + '5c' * 32
    with node() as url:
        code, text = run_cli(url, 'check-offer', A, '--no-independent', '--taker-pubkey',
                             taker, '--json')
    r = json.loads(text)
    expect = htlc.contracts(payment_hash160='a1' * 20, maker_pubkey='02' + 'a1' * 32,
                            taker_pubkey=taker, mars_locktime=MARS_H + 470,
                            btc_locktime=BTC_H + 24)
    assert code == 0 and r['contracts']['marscoin'] == expect['marscoin']
    assert r['contracts']['bitcoin'] == expect['bitcoin']
    assert r['contracts']['marscoin']['address'].startswith('mars1q')
    assert r['contracts']['bitcoin']['address'].startswith('bc1q')


# ---------------------------------------------------------------- the rules themselves

def offer(**over):
    o = {'id': A, 'status': 'open', 'mars_amount_zubrins': 100 * COIN, 'btc_amount_sats': 9_450,
         'maker_pubkey': '02' + 'a1' * 32, 'payment_hash160': 'a1' * 20,
         'mars_locktime_height': MARS_H + 470, 'expires_in_seconds': 500}
    o.update(over)
    return o


def test_local_refund_rule_is_the_wallets_rule():
    assert (safety.MARS_FAST, safety.BTC_SLOW, safety.MIN_REFUND_GAP_SECONDS) == (
        policy.MARS_FAST, policy.BTC_SLOW, policy.MIN_REFUND_GAP_SEC)
    for blocks in (0, 1, 100, 234, 351, 352, 353, 400, 480, 5000):
        local = safety.check_offer(offer(mars_locktime_height=MARS_H + blocks),
                                   mars_height=MARS_H, btc_height=BTC_H, fee_rate_sat_per_vb=5)
        server = policy.offer_safety(MARS_H + blocks, MARS_H, BTC_H)
        refund = next(c for c in local['checks'] if c['name'] == 'refund_order')
        assert refund['ok'] is server['safe'], blocks
        assert local['margin_seconds'] == server['margin_sec'], blocks
    # The boundary the wallets document: 352 blocks left passes, 351 does not.
    ok = lambda b: safety.check_offer(offer(mars_locktime_height=MARS_H + b),        # noqa: E731
                                      mars_height=MARS_H, btc_height=BTC_H,
                                      fee_rate_sat_per_vb=5)['safe']
    assert ok(352) is True and ok(351) is False and ok(234) is False and ok(480) is True


def test_local_rules_match_electrum_mars():
    em = pytest.importorskip('electrum_mars.plugins.atomic_swap.safety')
    assert (em.MARS_FAST, em.BTC_SLOW, em.MIN_REFUND_GAP_SEC, em.MIN_BTC_WINDOW_SEC,
            em.MARS_MIN_CONFIRMATIONS, em.BTC_BLOCK_SEC, em.MARS_BLOCK_SEC) == (
        safety.MARS_FAST, safety.BTC_SLOW, safety.MIN_REFUND_GAP_SECONDS,
        safety.MIN_BTC_WINDOW_SECONDS, safety.MARS_MIN_CONFIRMATIONS,
        safety.BTC_BLOCK_SECONDS, safety.MARS_BLOCK_SECONDS)
    for blocks in (100, 351, 352, 480):
        problem = em.refund_order_problem(MARS_H + blocks, MARS_H, BTC_H + 24, BTC_H)
        local = safety.check_offer(offer(mars_locktime_height=MARS_H + blocks),
                                   mars_height=MARS_H, btc_height=BTC_H, fee_rate_sat_per_vb=5)
        assert (problem is None) is local['safe']


def test_other_local_checks():
    def result(o=None, **kw):
        args = {'mars_height': MARS_H, 'btc_height': BTC_H, 'fee_rate_sat_per_vb': 5, **kw}
        r = safety.check_offer(o or offer(), **args)
        return r['safe'], {c['name']: c['ok'] for c in r['checks']}
    assert result()[0] is True
    assert result(offer(maker_pubkey=None))[1]['offer_form'] is False
    assert result(offer(payment_hash160='zz' * 20))[0] is False
    assert result(offer(mars_amount_zubrins=1.5))[1]['offer_form'] is False
    assert result(offer(status='taken'))[1]['open'] is False
    assert result(offer(expires_in_seconds=30))[1]['open'] is False
    assert result(mars_height=None) == (None, {'offer_form': True, 'open': True,
                                               'refund_order': None, 'bitcoin_window': True,
                                               'fee_viability': True})
    assert result(fee_rate_sat_per_vb=None)[0] is None
    # 1000 sats at 5 sat/vB: a 750 sat refund leaves less than dust.
    assert result(offer(btc_amount_sats=1_000))[1]['fee_viability'] is False
    assert result(offer(btc_amount_sats=1_296))[1]['fee_viability'] is True
    # A failed check outweighs an unknown one.
    assert result(offer(status='taken'), mars_height=None)[0] is False


def test_agreed_marscoin_height():
    assert safety.agreed_marscoin_height(100, 102)[0] == 102
    assert safety.agreed_marscoin_height(103, 100)[0] == 103
    assert safety.agreed_marscoin_height(100, 104)[0] is None
    assert safety.agreed_marscoin_height(100, None)[0] is None
    assert safety.agreed_marscoin_height(None, 100)[0] is None


# ---------------------------------------------------------------- contracts

def test_contracts_are_electrum_mars_contracts():
    em = pytest.importorskip('electrum_mars.atomic_swap_htlc')
    em_safety = pytest.importorskip('electrum_mars.plugins.atomic_swap.safety')
    from electrum_mars import bitcoin
    h, maker, taker = 'a1' * 20, '02' + 'a1' * 32, '03' + '5c' * 32
    for mars_lock, btc_lock in ((3_700_480, 900_024), (127, 128), (32_768, 8_388_608),
                                (499_999_999, 1)):
        c = htlc.contracts(payment_hash160=h, maker_pubkey=maker, taker_pubkey=taker,
                           mars_locktime=mars_lock, btc_locktime=btc_lock)
        script, address = em_safety.expected_mars_htlc(h, taker, maker, mars_lock)
        assert c['marscoin']['script_hex'] == script.hex()
        assert c['marscoin']['address'] == address
        assert c['marscoin']['electrum_scripthash'] == bitcoin.address_to_scripthash(address)
        btc = em.create_htlc_script(bytes.fromhex(h), bytes.fromhex(maker), bytes.fromhex(taker),
                                    btc_lock)
        assert c['bitcoin']['script_hex'] == btc.hex()
        assert c['bitcoin']['address'] == em.htlc_to_p2wsh_address(btc, em.Chain.BTC)


def test_contract_inputs_are_validated():
    good = dict(payment_hash160='a1' * 20, maker_pubkey='02' + 'a1' * 32,
                taker_pubkey='03' + '5c' * 32, mars_locktime=3_700_480, btc_locktime=900_024)
    for bad in ({'payment_hash160': 'a1' * 19}, {'maker_pubkey': '04' + 'a1' * 32},
                {'taker_pubkey': 'a1' * 32}, {'mars_locktime': 0},
                {'btc_locktime': 500_000_000}):
        with pytest.raises(ValueError):
            htlc.contracts(**{**good, **bad})
    # BIP173 test vector: a known P2WSH address.
    script = bytes.fromhex('21' + '0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798'
                           + 'ac')
    assert htlc.p2wsh_address(script, 'bc') == \
        'bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3'


# ---------------------------------------------------------------- what the client is not

def test_client_is_standard_library_only_and_handles_no_keys():
    allowed = set(sys.stdlib_module_names)
    for path in (AGENT_DIR / 'marsswap_agent').glob('*.py'):
        source = path.read_text()
        for item in ast.walk(ast.parse(source)):
            if isinstance(item, ast.Import):
                names = [a.name for a in item.names]
            elif isinstance(item, ast.ImportFrom) and item.level == 0:
                names = [item.module]
            else:
                continue
            for name in names:
                assert name.split('.')[0] in allowed, f'{path.name} imports {name}'
        for word in ('privkey', 'private_key', 'sign(', 'seed', 'mnemonic', 'broadcast',
                     'urlopen(request, data', 'method=\'POST\''):
            assert word not in source.lower(), f'{path.name} mentions {word}'


# ---------------------------------------------------------------- MCP

def mcp_session(url, *messages, fetch=http_get):
    stdin = io.StringIO('\n'.join(m if isinstance(m, str) else json.dumps(m) for m in messages) + '\n')
    stdout = io.StringIO()
    assert mcp.serve(Client(url, fetch=fetch), fetch=fetch, stdin=stdin, stdout=stdout) == 0
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def rpc(msg_id, method, **params):
    return {'jsonrpc': '2.0', 'id': msg_id, 'method': method, 'params': params}


def test_mcp_server():
    with node() as url:
        answers = mcp_session(
            url,
            rpc(1, 'initialize', protocolVersion='2025-03-26', capabilities={},
                clientInfo={'name': 'test', 'version': '0'}),
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            rpc(2, 'tools/list'),
            rpc(3, 'tools/call', name='get_quote', arguments={'mars': '300'}),
            rpc(4, 'tools/call', name='get_offer', arguments={'id': B[:10]}),
            rpc(5, 'tools/call', name='list_offers', arguments={'safe_only': True, 'sort': 'size'}),
            rpc(6, 'tools/call', name='check_offer', arguments={'id': C, 'independent': False}),
            rpc(7, 'tools/call', name='get_offer', arguments={'id': 'ff' * 16}),
            rpc(8, 'tools/call', name='send_bitcoin', arguments={}),
            rpc(9, 'tools/call', name='get_quote', arguments={'mars': '1', 'url': 'http://evil'}),
            rpc(10, 'resources/list'),
            rpc(11, 'ping'),
            'this is not json',
        )
    by_id = {a['id']: a for a in answers}
    assert len(answers) == 12                               # the notification gets no answer
    init = by_id[1]['result']
    assert init['protocolVersion'] == '2025-03-26' and init['capabilities'] == {'tools': {}}
    assert init['serverInfo']['name'] == 'marsswap-agent'
    tools = by_id[2]['result']['tools']
    names = [t['name'] for t in tools]
    assert names == ['get_market', 'list_offers', 'get_offer', 'get_quote', 'list_recent_swaps',
                     'get_protocol', 'check_offer'] and len(set(names)) == 7
    for t in tools:
        assert set(t) == {'name', 'description', 'inputSchema'}
        assert t['inputSchema']['type'] == 'object' and 'properties' in t['inputSchema']

    def content(msg_id):
        r = by_id[msg_id]['result']
        assert r['content'][0]['type'] == 'text'
        return r['isError'], json.loads(r['content'][0]['text'])
    assert content(3) == (False, content(3)[1]) and content(3)[1]['total_btc_sats'] == 28_710
    assert content(4)[1]['offer']['id'] == B
    assert [o['id'] for o in content(5)[1]['offers']] == [D, B, A]
    is_error, checked = content(6)
    assert is_error is False and checked['safe'] is False and checked['exit_code'] == 1
    assert content(7) == (True, {'error': {'code': 'offer_not_found',
                                           'message': content(7)[1]['error']['message']}})
    assert content(8)[0] is True and 'unknown tool' in content(8)[1]['error']['message']
    assert content(9)[0] is True and 'unknown argument' in content(9)[1]['error']['message']
    assert by_id[10]['error']['code'] == -32601
    assert by_id[11]['result'] == {}
    assert by_id[None]['error']['code'] == -32700


def test_mcp_calls_only_the_node_it_was_started_for():
    seen = []

    def spying(url, timeout=15):
        seen.append(url)
        status, body = http_get(url, timeout)
        if url.endswith('/api/tools'):                     # a document pointing elsewhere
            data = json.loads(body)
            for how in data['execution'].values():
                how['url'] = how['url'].replace('127.0.0.1', 'evil.example')
            body = json.dumps(data).encode()
        return status, body
    with node() as url:
        answers = mcp_session(url, rpc(1, 'tools/call', name='get_market', arguments={}),
                              fetch=spying)
        assert answers[0]['result']['isError'] is False
        assert all(u.startswith(url) for u in seen) and len(seen) == 2


def test_mcp_reports_an_unreachable_node():
    answers = mcp_session('http://127.0.0.1:1', rpc(1, 'tools/list'))
    assert answers[0]['error']['code'] == -32000
