"""check-offer: fetch an offer, recompute its safety, compare with the server."""

from . import htlc, safety
from .client import http_get, second_bitcoin_heights, second_marscoin_height

EXIT_OK = 0             # the answer is yes: safe, or data was returned
EXIT_NO = 1             # the answer is no: unsafe, unknown, or nothing to fill
EXIT_ERROR = 2          # no answer: network, API or usage error
EXIT_DISAGREE = 3       # this client and the server disagree about safety


def check_offer(client, offer_id: str, *, independent: bool = True, taker_pubkey: str = None,
                fetch=http_get) -> dict:
    """Everything check-offer reports, as a dict with an ``exit_code``.

    With ``independent`` (the default) the Marscoin height must agree with a
    public block explorer: a server reporting an old height would otherwise
    make a nearly expired lock look safe.

    Raises client.ApiError / client.NetworkError when the server cannot be read.
    """
    detail = client.offer(offer_id)
    market = client.market()
    protocol = client.protocol()
    offer = detail['offer']
    server_mars, server_btc = detail.get('marscoin_height'), detail.get('bitcoin_height')
    fee_rate = market['chains']['bitcoin'].get('fee_rate_fast_sat_per_vb')

    mars_height, heights = server_mars, {
        'marscoin': server_mars, 'bitcoin': server_btc,
        'source': 'the server alone (--no-independent): a stale or dishonest server could '
                  'make an expiring lock look safe'}
    if independent:
        second = second_marscoin_height(fetch)
        mars_height, why = safety.agreed_marscoin_height(server_mars, second)
        btc_seen = second_bitcoin_heights(fetch)
        heights = {'marscoin': mars_height, 'bitcoin': server_btc,
                   'source': f'Marscoin: {why}. Bitcoin: server {server_btc}, '
                             f'public sources {btc_seen or "unreachable"}'}

    local = safety.check_offer(offer, mars_height=mars_height, btc_height=server_btc,
                               fee_rate_sat_per_vb=fee_rate)
    disagreements = safety.compare_with_server(
        offer, local, same_heights=mars_height == server_mars)
    constants = safety.compare_constants(protocol)

    result = {
        'offer_id': offer.get('id'),
        'mars_amount_zubrins': offer.get('mars_amount_zubrins'),
        'btc_amount_sats': offer.get('btc_amount_sats'),
        'price_sats_per_mars': offer.get('price_sats_per_mars'),
        'premium_percent': offer.get('premium_percent'),
        'expires_in_seconds': offer.get('expires_in_seconds'),
        'heights': heights,
        'safe': local['safe'],
        'margin_seconds': local['margin_seconds'],
        'checks': local['checks'],
        'server_safety': offer.get('safety'),
        'disagreements': disagreements,
        'constant_differences': constants,
        'contracts': None,
        'next': ('Accepting is reasonable. Before paying any Bitcoin, verify the Marscoin lock '
                 'on chain as /api/v1/protocol describes (taker_checks.mars_lock).'),
    }
    if disagreements or constants:
        result['exit_code'] = EXIT_DISAGREE
        result['next'] = ('Do not act: this client and the server disagree. One of them is out '
                          'of date or wrong.')
    elif local['safe'] is True:
        result['exit_code'] = EXIT_OK
    else:
        result['exit_code'] = EXIT_NO
        result['next'] = 'Do not accept this offer.'

    if taker_pubkey and local['checks'][0]['ok'] and server_btc:
        btc_locktime = server_btc + safety.BTC_TIMELOCK_BLOCKS
        result['contracts'] = {
            **htlc.contracts(payment_hash160=offer['payment_hash160'],
                             maker_pubkey=offer['maker_pubkey'], taker_pubkey=taker_pubkey,
                             mars_locktime=offer['mars_locktime_height'],
                             btc_locktime=btc_locktime),
            'note': ('The Marscoin contract is fixed by the offer and your key. The Bitcoin '
                     f'contract is for an acceptance at Bitcoin height {server_btc}; it changes '
                     'with the height you accept at.'),
        }
    return result


def format_check(r: dict) -> str:
    verdict = {True: 'SAFE TO ACCEPT', False: 'NOT SAFE', None: 'UNKNOWN'}[r['safe']]
    mark = {True: 'ok  ', False: 'FAIL', None: '??  '}
    lines = [
        f'offer {r["offer_id"]}',
        f'  {mars(r["mars_amount_zubrins"])} MARS for {r["btc_amount_sats"]} sats '
        f'({r["price_sats_per_mars"]} sats/MARS, premium {pct(r["premium_percent"])}), '
        f'expires in {r["expires_in_seconds"]} s',
        f'  heights: Marscoin {r["heights"]["marscoin"]}, Bitcoin {r["heights"]["bitcoin"]}',
        f'           from {r["heights"]["source"]}',
        'local checks:',
    ]
    lines += [f'  [{mark[c["ok"]]}] {c["name"]}: {c["detail"]}' for c in r['checks']]
    server = r['server_safety'] or {}
    lines.append(f'server says: safe={server.get("safe")} margin={server.get("margin_seconds")} s'
                 + (f' ({server["reason"]})' if server.get('reason') else ''))
    for line in r['disagreements']:
        lines.append(f'DISAGREEMENT: {line}')
    for line in r['constant_differences']:
        lines.append(f'DIFFERENT RULES: {line}')
    if r['contracts']:
        c = r['contracts']
        lines += ['contracts for your key:',
                  f'  Marscoin lock must appear at {c["marscoin"]["address"]}',
                  f'    scripthash {c["marscoin"]["electrum_scripthash"]}',
                  f'    script {c["marscoin"]["script_hex"]}',
                  f'  Bitcoin contract (refund height {c["bitcoin"]["locktime"]}): '
                  f'{c["bitcoin"]["address"]}',
                  f'  {c["note"]}']
    lines += [f'verdict: {verdict}', f'next: {r["next"]}']
    return '\n'.join(lines)


def mars(zubrins) -> str:
    if zubrins is None:
        return '?'
    whole, frac = divmod(int(zubrins), 100_000_000)
    return f'{whole}.{frac:08d}'.rstrip('0').rstrip('.')


def pct(value) -> str:
    return 'unknown' if value is None else f'{value:+.2f}%'
