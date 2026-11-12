"""The taker's checks before accepting an offer, recomputed locally.

These are the rules of the wallets that take offers (Electrum-Mars
plugins/atomic_swap/safety.py and the Martian Republic app's
blue_modules/swap/safety.js). The numbers are written out here on purpose:
a client that asked the server for them would be trusting the server.
`compare_constants` reports when the server's protocol document disagrees.

Nothing in this module touches keys or sends anything.
"""

import math
import re

BTC_TIMELOCK_BLOCKS = 24            # the taker's Bitcoin refund opens this many blocks after accepting
MARS_BLOCK_SECONDS = 123
BTC_BLOCK_SECONDS = 600
MARS_FAST = 0.5                     # assume Marscoin blocks arrive twice as fast as the target
BTC_SLOW = 1.25                     # assume Bitcoin blocks arrive a quarter slower
MIN_REFUND_GAP_SECONDS = 3600       # the MARS refund must open at least this long after the BTC refund
MIN_BTC_WINDOW_SECONDS = 2 * 3600
MARS_MIN_CONFIRMATIONS = 2
MARS_SPEND_FEE_ZUBRINS = 100_000
BTC_SPEND_VBYTES = 150
DUST_SATS = 546
MIN_SECONDS_TO_ACCEPT = 60          # this client's own caution: do not start on an offer about to expire

_PUBKEY = re.compile(r'^0[23][0-9a-f]{64}$')
_HASH160 = re.compile(r'^[0-9a-f]{40}$')
_OFFER_ID = re.compile(r'^[0-9a-f]{8,64}$')


def refund_margin_seconds(mars_locktime: int, mars_height: int,
                          btc_locktime: int, btc_height: int) -> float:
    """Pessimistic time between the Bitcoin refund and the Marscoin refund."""
    mars = max(0, mars_locktime - mars_height) * MARS_BLOCK_SECONDS * MARS_FAST
    btc = max(0, btc_locktime - btc_height) * BTC_BLOCK_SECONDS * BTC_SLOW
    return mars - btc


def _check(name: str, ok, detail: str) -> dict:
    return {'name': name, 'ok': ok, 'detail': detail}


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def check_offer(offer: dict, *, mars_height, btc_height, fee_rate_sat_per_vb=None) -> dict:
    """Would it be safe to accept this offer now?

    ``offer`` is an offer object from /api/v1/offers. Returns
    {'safe': True | False | None, 'margin_seconds', 'checks': [...]}.
    ``safe`` is None when something needed for the decision is missing.
    Passing these checks allows accepting. It does not allow paying: that
    needs the Marscoin lock verified on chain (see /api/v1/protocol).
    """
    checks = []

    # 1. The fields a taker builds the contracts from.
    problems = []
    if not (isinstance(offer.get('id'), str) and _OFFER_ID.match(offer['id'])):
        problems.append('id')
    if not (isinstance(offer.get('maker_pubkey'), str) and _PUBKEY.match(offer['maker_pubkey'])):
        problems.append('maker_pubkey')
    if not (isinstance(offer.get('payment_hash160'), str)
            and _HASH160.match(offer['payment_hash160'])):
        problems.append('payment_hash160')
    mars, btc = offer.get('mars_amount_zubrins'), offer.get('btc_amount_sats')
    if not (_is_int(mars) and mars > MARS_SPEND_FEE_ZUBRINS + DUST_SATS):
        problems.append('mars_amount_zubrins')
    if not (_is_int(btc) and btc > DUST_SATS):
        problems.append('btc_amount_sats')
    locktime = offer.get('mars_locktime_height')
    if not (_is_int(locktime) and 0 < locktime < 500_000_000):
        problems.append('mars_locktime_height')
    checks.append(_check('offer_form', not problems,
                         'all fields are well formed' if not problems
                         else 'malformed or missing: ' + ', '.join(problems)))

    # 2. Still open, with time to act.
    status, left = offer.get('status'), offer.get('expires_in_seconds')
    if status != 'open':
        checks.append(_check('open', False, f'the offer is {status}'))
    elif not _is_int(left) or left < MIN_SECONDS_TO_ACCEPT:
        checks.append(_check('open', False, f'expires in {left} seconds'))
    else:
        checks.append(_check('open', True, f'open, expires in {left} seconds'))

    # 3. Refund order: the Bitcoin refund must open well before the Marscoin refund.
    margin = None
    if 'mars_locktime_height' in problems or not (_is_int(mars_height) and mars_height > 0
                                                  and _is_int(btc_height) and btc_height > 0):
        checks.append(_check('refund_order', None, 'block heights are missing'))
    else:
        margin = round(refund_margin_seconds(locktime, mars_height,
                                             btc_height + BTC_TIMELOCK_BLOCKS, btc_height))
        blocks = locktime - mars_height
        ok = margin >= MIN_REFUND_GAP_SECONDS
        checks.append(_check(
            'refund_order', ok,
            f'{blocks} Marscoin blocks left; margin {margin} s, need {MIN_REFUND_GAP_SECONDS} s'
            + ('' if ok else ': the seller could reclaim the MARS too soon after the BTC refund')))

    # 4. The Bitcoin window. Always true at acceptance; kept so a change in the
    #    constants cannot go unnoticed.
    window = BTC_TIMELOCK_BLOCKS * BTC_BLOCK_SECONDS
    checks.append(_check('bitcoin_window', window >= MIN_BTC_WINDOW_SECONDS,
                         f'{window} s to pay and be claimed, need {MIN_BTC_WINDOW_SECONDS} s'))

    # 5. The locked Bitcoin must be worth refunding at today's fees.
    if 'btc_amount_sats' in problems:
        checks.append(_check('fee_viability', False, 'no valid BTC amount'))
    elif not isinstance(fee_rate_sat_per_vb, (int, float)) or isinstance(fee_rate_sat_per_vb, bool) \
            or fee_rate_sat_per_vb <= 0:
        checks.append(_check('fee_viability', None, 'the Bitcoin fee rate is unknown'))
    else:
        fee = math.ceil(fee_rate_sat_per_vb * BTC_SPEND_VBYTES)
        ok = btc - fee >= DUST_SATS
        checks.append(_check('fee_viability', ok,
                             f'a refund costs about {fee} sats of {btc}'
                             + ('' if ok else ': too small to refund')))

    results = [c['ok'] for c in checks]
    safe = False if False in results else (None if None in results else True)
    return {'safe': safe, 'margin_seconds': margin, 'checks': checks}


def compare_with_server(offer: dict, local: dict, same_heights: bool) -> list:
    """Differences between the server's safety object and ours. Empty: they agree.

    The server's verdict covers the refund order only, so it is compared
    with our refund-order check, not with the overall result.
    """
    server = offer.get('safety') or {}
    mine = next(c for c in local['checks'] if c['name'] == 'refund_order')
    out = []
    if mine['ok'] is None:
        return out               # nothing to compare: we could not decide (the result says why)
    if server.get('safe') is not mine['ok']:
        out.append(f'the server says safe={server.get("safe")!r}, '
                   f'the local refund-order check says {mine["ok"]!r}')
    elif same_heights and server.get('margin_seconds') != local['margin_seconds']:
        out.append(f'the server reports a margin of {server.get("margin_seconds")} s, '
                   f'locally it is {local["margin_seconds"]} s')
    if server.get('min_margin_seconds') not in (None, MIN_REFUND_GAP_SECONDS):
        out.append(f'the server requires a margin of {server.get("min_margin_seconds")} s, '
                   f'this client {MIN_REFUND_GAP_SECONDS} s')
    return out


def compare_constants(protocol: dict) -> list:
    """Where the server's protocol document differs from this client's numbers."""
    def dig(*path):
        node = protocol
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        return node
    pairs = [
        ('bitcoin refund blocks', dig('timelocks', 'bitcoin_refund_blocks'), BTC_TIMELOCK_BLOCKS),
        ('mars_fast', dig('taker_checks', 'refund_order', 'mars_fast'), MARS_FAST),
        ('btc_slow', dig('taker_checks', 'refund_order', 'btc_slow'), BTC_SLOW),
        ('minimum refund gap', dig('taker_checks', 'refund_order', 'min_gap_seconds'),
         MIN_REFUND_GAP_SECONDS),
        ('Marscoin block seconds', dig('taker_checks', 'refund_order', 'marscoin_block_seconds'),
         MARS_BLOCK_SECONDS),
        ('Bitcoin block seconds', dig('taker_checks', 'refund_order', 'bitcoin_block_seconds'),
         BTC_BLOCK_SECONDS),
        ('minimum Bitcoin window', dig('taker_checks', 'bitcoin_window', 'min_window_seconds'),
         MIN_BTC_WINDOW_SECONDS),
        ('Marscoin confirmations', dig('confirmations', 'marscoin_lock_before_taker_pays'),
         MARS_MIN_CONFIRMATIONS),
        ('Marscoin claim fee', dig('fees', 'marscoin_claim_or_refund_zubrins'),
         MARS_SPEND_FEE_ZUBRINS),
    ]
    return [f'{name}: the server documents {theirs!r}, this client uses {ours!r}'
            for name, theirs, ours in pairs if theirs != ours]


def agreed_marscoin_height(server_height, explorer_height, tolerance: int = 3):
    """The height to judge a Marscoin lock by, from two sources.

    A stale height would make a nearly expired lock look safe, so the
    sources must agree within a few blocks and the higher one is used
    (less time left). Returns (height or None, explanation).
    """
    if not (_is_int(server_height) and server_height > 0):
        return None, 'the server has no Marscoin height'
    if not (_is_int(explorer_height) and explorer_height > 0):
        return None, 'the second source has no Marscoin height'
    if abs(server_height - explorer_height) > tolerance:
        return None, (f'the sources disagree: server {server_height}, '
                      f'second source {explorer_height}')
    return max(server_height, explorer_height), (
        f'server {server_height}, second source {explorer_height}')
