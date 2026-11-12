"""The read API for AI agents: /api/v1.

Pure functions from the service's public state (Service.agent_state) to
JSON documents. Field names carry their units, atomic amounts are
integers, and every time is given both as ISO 8601 (UTC) and as unix
seconds. Nothing here writes, signs, or holds anyone's coins.

Units:
  zubrin   1e-8 MARS, the smallest Marscoin unit
  satoshi  1e-8 BTC
"""

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from . import policy

API_VERSION = 'v1'
COIN = policy.COIN

# Fixed by the swap code: both wallets deduct this from the Marscoin contract
# when they claim or refund it (electrum_mars/atomic_swap_htlc.py
# create_claim_tx fee_sat; MarsWallet blue_modules/swap/safety.js MARS_SPEND_FEE).
MARS_SPEND_FEE_ZUBRINS = 100_000

MAX_RECENT_SWAPS = 100
MIN_ID_PREFIX = 8
COMPLETED_STATES = ('btc_claimed', 'completed')

_PUBKEY = re.compile(r'^0[23][0-9a-f]{64}$')
_HASH160 = re.compile(r'^[0-9a-f]{40}$')
_OFFER_ID = re.compile(r'^[0-9a-f]{8,64}$')

WHOLE_OFFERS = ('Offers are taken whole: a swap fills exactly one offer, for exactly its '
                'MARS amount and its BTC amount. There are no partial fills.')


class ApiError(Exception):
    """An error answered as {"error": {"code", "message"}}."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    def body(self) -> dict:
        return {'error': {'code': self.code, 'message': self.message}}


# ---------------------------------------------------------------- helpers

def iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def unix(ts: Optional[float]) -> Optional[int]:
    return None if ts is None else int(ts)


def age(ts: Optional[float], now: float) -> Optional[int]:
    return None if ts is None else max(0, int(now - ts))


def envelope(now: float) -> dict:
    return {'api_version': API_VERSION, 'generated_at': iso(now), 'generated_at_unix': int(now)}


def parse_mars(text: str, name: str) -> int:
    """A decimal MARS amount ('250', '12.5') as zubrins, without floats."""
    try:
        value = Decimal(str(text).strip())
    except InvalidOperation:
        raise ApiError(400, 'invalid_parameter', f'{name} must be a decimal number of MARS')
    if not value.is_finite() or value <= 0:
        raise ApiError(400, 'invalid_parameter', f'{name} must be greater than zero')
    zubrins = value * COIN
    if zubrins != zubrins.to_integral_value():
        raise ApiError(400, 'invalid_parameter', f'{name} has more than 8 decimal places')
    if zubrins > 10 ** 18:
        raise ApiError(400, 'invalid_parameter', f'{name} is too large')
    return int(zubrins)


def parse_int(text: str, name: str, *, minimum: int = 1, maximum: int = 10 ** 18) -> int:
    if not re.fullmatch(r'\d{1,19}', str(text).strip()):
        raise ApiError(400, 'invalid_parameter', f'{name} must be a whole number')
    value = int(text)
    if not minimum <= value <= maximum:
        raise ApiError(400, 'invalid_parameter', f'{name} must be between {minimum} and {maximum}')
    return value


def parse_bool(text: Optional[str], name: str) -> bool:
    if text is None:
        return False
    low = str(text).strip().lower()
    if low in ('1', 'true', 'yes'):
        return True
    if low in ('0', 'false', 'no', ''):
        return False
    raise ApiError(400, 'invalid_parameter', f'{name} must be true or false')


def _market(st: dict):
    return st.get('market')


def _btc_height(st: dict) -> Optional[int]:
    m = _market(st)
    return m.btc_height if m else None


def _market_rate(st: dict) -> Optional[float]:
    m = _market(st)
    return m.mars_btc if m else None


# ---------------------------------------------------------------- market

def market_view(st: dict, now: float) -> dict:
    m = _market(st)
    price_age = age(m.prices_at, now) if m else None
    fresh = price_age is not None and price_age <= st['max_price_age_seconds'] and bool(m.mars_btc)
    rate = m.mars_btc if m else None
    open_offers = offers_list(st, now)
    return {
        **envelope(now),
        'price': {
            'btc_per_mars': rate,
            'sats_per_mars': round(rate * COIN, 4) if rate else None,
            'usd_per_mars': m.mars_usd if m else None,
            'usd_per_btc': m.btc_usd if m else None,
            'usd_per_mars_source': 'price.marscoin.org (CoinMarketCap listing 154)',
            'usd_per_btc_source': 'mempool.space',
            'updated_at': iso(m.prices_at) if m else None,
            'updated_at_unix': unix(m.prices_at) if m else None,
            'age_seconds': price_age,
            'fresh': fresh,
            'max_age_seconds': int(st['max_price_age_seconds']),
        },
        'chains': {
            'marscoin': {
                'height': st.get('mars_height'),
                'tip_time': iso(st.get('mars_tip_time')),
                'tip_time_unix': unix(st.get('mars_tip_time')),
                'tip_age_seconds': age(st.get('mars_tip_time'), now),
                'target_block_seconds': policy.MARS_BLOCK_SEC,
                'source': 'ElectrumX relays (highest connected tip)',
                'relays_connected': st.get('relays_connected', 0),
            },
            'bitcoin': {
                'height': m.btc_height if m else None,
                'tip_time': iso(m.btc_tip_time) if m else None,
                'tip_time_unix': unix(m.btc_tip_time) if m else None,
                'tip_age_seconds': age(m.btc_tip_time, now) if m else None,
                'target_block_seconds': policy.BTC_BLOCK_SEC,
                'source': 'mempool.space',
                'fetched_at': iso(m.btc_at) if m else None,
                'fetched_at_unix': unix(m.btc_at) if m else None,
                'fee_rate_fast_sat_per_vb': m.btc_fee_fast if m else None,
                'fee_rate_hour_sat_per_vb': m.btc_fee_hour if m else None,
            },
        },
        'maker': {
            'mode': st['mode'],
            'live': st['mode'] == 'live' and bool(st.get('maker_running')),
            'open_offers': sum(1 for o in open_offers if o['from_this_maker']),
        },
        'book': {
            'open_offers': len(open_offers),
            'open_mars_zubrins': sum(o['mars_amount_zubrins'] for o in open_offers),
            'safe_open_offers': sum(1 for o in open_offers if takeable(o)),
        },
        'feed_error': m.error if m else 'market feed not started',
    }


# ---------------------------------------------------------------- offers

def offer_view(oid: str, entry: dict, st: dict, now: float) -> dict:
    o = entry['raw']
    mars, btc = int(o['mars_amount_sat']), int(o['btc_amount_sat'])
    rate = btc / mars
    market_rate = _market_rate(st)
    locktime = int(o.get('mars_locktime') or 0)
    mars_height, btc_height = st.get('mars_height'), _btc_height(st)
    safety = policy.offer_safety(locktime, mars_height, btc_height)
    pubkey, hash160 = o.get('maker_pubkey'), o.get('payment_hash160')
    pubkey = pubkey if isinstance(pubkey, str) and _PUBKEY.match(pubkey) else None
    hash160 = hash160 if isinstance(hash160, str) and _HASH160.match(hash160) else None
    expires_at = float(o['expires_at'])
    well_formed = bool(pubkey and hash160 and _OFFER_ID.match(oid)
                       and 0 < locktime < 500_000_000
                       and mars > MARS_SPEND_FEE_ZUBRINS + 546 and btc > 546)
    return {
        'id': oid,
        'status': 'taken' if oid in st.get('taken', ()) else 'open',
        'mars_amount_zubrins': mars,
        'btc_amount_sats': btc,
        'price_btc_per_mars': rate,
        'price_sats_per_mars': round(rate * COIN, 4),
        'premium_percent': round((rate / market_rate - 1) * 100, 2) if market_rate else None,
        'mars_locktime_height': locktime or None,
        'mars_locktime_blocks_left': max(0, locktime - mars_height) if locktime and mars_height else None,
        'expires_at': iso(expires_at),
        'expires_at_unix': int(expires_at),
        'expires_in_seconds': max(0, int(expires_at - now)),
        'maker_pubkey': pubkey,
        'payment_hash160': hash160,
        'from_this_maker': oid in st.get('ours', ()),
        'relays_seen': len(entry.get('relays') or ()),
        'well_formed': well_formed,
        'safety': {
            'safe': safety['safe'],
            'margin_seconds': safety['margin_sec'],
            'min_margin_seconds': policy.MIN_REFUND_GAP_SEC,
            'reason': safety['reason'],
        },
    }


def takeable(offer: dict) -> bool:
    """Open, well formed, and safe by the wallets' refund-order rule."""
    return (offer['status'] == 'open' and offer['well_formed']
            and offer['safety']['safe'] is True and offer['expires_in_seconds'] > 0)


def all_offers(st: dict, now: float) -> List[dict]:
    out = []
    for oid, entry in st.get('offers', {}).items():
        try:
            if float(entry['raw']['expires_at']) <= now:
                continue
            out.append(offer_view(oid, entry, st, now))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
    return sort_offers(out, 'price')


def offers_list(st: dict, now: float) -> List[dict]:
    """Open offers, cheapest first."""
    return [o for o in all_offers(st, now) if o['status'] == 'open']


def sort_offers(offers: List[dict], sort: str) -> List[dict]:
    if sort == 'size':
        return sorted(offers, key=lambda o: (-o['mars_amount_zubrins'], o['price_btc_per_mars'], o['id']))
    return sorted(offers, key=lambda o: (o['price_btc_per_mars'], -o['mars_amount_zubrins'], o['id']))


def offers_view(st: dict, now: float, query) -> dict:
    sort = query.get('sort', 'price')
    if sort not in ('price', 'size'):
        raise ApiError(400, 'invalid_parameter', 'sort must be "price" or "size"')
    min_mars = parse_mars(query['min_mars'], 'min_mars') if 'min_mars' in query else None
    max_mars = parse_mars(query['max_mars'], 'max_mars') if 'max_mars' in query else None
    if min_mars is not None and max_mars is not None and min_mars > max_mars:
        raise ApiError(400, 'invalid_parameter', 'min_mars is larger than max_mars')
    safe_only = parse_bool(query.get('safe_only'), 'safe_only')
    offers = offers_list(st, now)
    total = len(offers)
    if min_mars is not None:
        offers = [o for o in offers if o['mars_amount_zubrins'] >= min_mars]
    if max_mars is not None:
        offers = [o for o in offers if o['mars_amount_zubrins'] <= max_mars]
    if safe_only:
        offers = [o for o in offers if takeable(o)]
    return {
        **envelope(now),
        'marscoin_height': st.get('mars_height'),
        'bitcoin_height': _btc_height(st),
        'market_price_btc_per_mars': _market_rate(st),
        'sort': sort,
        'count': len(offers),
        'open_offers_total': total,
        'offers': sort_offers(offers, sort),
        'note': WHOLE_OFFERS,
    }


def offer_detail(st: dict, now: float, offer_id: str) -> dict:
    offer_id = offer_id.strip().lower()
    if not re.fullmatch(r'[0-9a-f]{%d,64}' % MIN_ID_PREFIX, offer_id):
        raise ApiError(400, 'invalid_parameter',
                       f'an offer id is {MIN_ID_PREFIX} to 64 hexadecimal characters')
    matches = [o for o in all_offers(st, now) if o['id'].startswith(offer_id)]
    exact = [o for o in matches if o['id'] == offer_id]
    if exact:
        matches = exact
    if not matches:
        raise ApiError(404, 'offer_not_found',
                       'no open or recently taken offer has this id; offers expire after minutes')
    if len(matches) > 1:
        raise ApiError(400, 'ambiguous_id', 'several offers start with this id; give more characters')
    return {
        **envelope(now),
        'marscoin_height': st.get('mars_height'),
        'bitcoin_height': _btc_height(st),
        'offer': matches[0],
        'note': WHOLE_OFFERS,
    }


# ---------------------------------------------------------------- quote

def _fill(o: dict) -> dict:
    return {'offer_id': o['id'], 'mars_amount_zubrins': o['mars_amount_zubrins'],
            'btc_amount_sats': o['btc_amount_sats'],
            'price_sats_per_mars': o['price_sats_per_mars'],
            'premium_percent': o['premium_percent'],
            'expires_in_seconds': o['expires_in_seconds']}


def quote_view(st: dict, now: float, query) -> dict:
    """Which whole offers fit a request. Never more liquidity than the book has.

    Walks the open, safe offers from the cheapest price up and takes each
    one that still fits in what is left of the request. An offer larger
    than the remainder is skipped, never cut.
    """
    has_mars, has_btc = 'mars' in query, 'btc_sats' in query
    if has_mars == has_btc:
        raise ApiError(400, 'invalid_parameter',
                       'give exactly one of mars (decimal MARS to buy) or btc_sats (satoshis to spend)')
    opens = offers_list(st, now)
    usable = [o for o in opens if takeable(o)]
    fills, alternative = [], None
    if has_mars:
        want = parse_mars(query['mars'], 'mars')
        request = {'mars_zubrins': want}
        left = want
        for o in usable:
            if o['mars_amount_zubrins'] <= left:
                fills.append(o)
                left -= o['mars_amount_zubrins']
        covering = [o for o in usable if o['mars_amount_zubrins'] >= want]
        if left and covering:
            # Cheapest total cost among single offers that give at least the request.
            best = min(covering, key=lambda o: (o['btc_amount_sats'], o['id']))
            alternative = {**_fill(best), 'excess_mars_zubrins': best['mars_amount_zubrins'] - want}
        unfilled = {'unfilled_mars_zubrins': left, 'unspent_btc_sats': None}
    else:
        budget = parse_int(query['btc_sats'], 'btc_sats')
        request = {'btc_sats': budget}
        left = budget
        for o in usable:
            if o['btc_amount_sats'] <= left:
                fills.append(o)
                left -= o['btc_amount_sats']
        unfilled = {'unfilled_mars_zubrins': None, 'unspent_btc_sats': left}
    total_mars = sum(o['mars_amount_zubrins'] for o in fills)
    total_btc = sum(o['btc_amount_sats'] for o in fills)
    notes = [WHOLE_OFFERS,
             'Only open offers that pass the refund-order safety rule are used.',
             'A taker can have one swap in progress per relay connection, so several fills '
             'are taken one after another, and each offer may be gone by then.',
             f'Each Marscoin claim pays a fixed network fee of {MARS_SPEND_FEE_ZUBRINS} zubrins, '
             'taken from the amount received. The Bitcoin network fee for paying into the '
             'contract is the taker\'s own and is not included.']
    if not usable:
        notes.insert(0, 'There are no open, safe offers right now: nothing can be filled.')
    return {
        **envelope(now),
        'request': request,
        'fills': [_fill(o) for o in fills],
        'fully_filled': bool(fills) and left == 0,
        'total_mars_zubrins': total_mars,
        'total_btc_sats': total_btc,
        'average_price_btc_per_mars': total_btc / total_mars if total_mars else None,
        'average_price_sats_per_mars': round(total_btc / total_mars * COIN, 4) if total_mars else None,
        'mars_claim_fee_zubrins': MARS_SPEND_FEE_ZUBRINS * len(fills),
        'net_mars_zubrins': total_mars - MARS_SPEND_FEE_ZUBRINS * len(fills),
        **unfilled,
        'alternative_single_offer': alternative,
        'offers_considered': len(usable),
        'offers_excluded': len(opens) - len(usable),
        'notes': notes,
    }


# ---------------------------------------------------------------- swaps

def recent_swaps_view(st: dict, now: float, query) -> dict:
    limit = parse_int(query['limit'], 'limit', maximum=MAX_RECENT_SWAPS) if 'limit' in query else 25
    swaps = st.get('maker_swaps') or []
    done = sorted((s for s in swaps if s['state'] in COMPLETED_STATES),
                  key=lambda s: s['updated_at'], reverse=True)
    out = []
    for s in done[:limit]:
        mars, btc = int(s['mars_amount_sat']), int(s['btc_amount_sat'])
        out.append({
            'id': s['id'],
            'mars_amount_zubrins': mars,
            'btc_amount_sats': btc,
            'price_btc_per_mars': btc / mars if mars else None,
            'price_sats_per_mars': round(btc / mars * COIN, 4) if mars else None,
            'offer_created_at': iso(s['created_at']),
            'offer_created_at_unix': unix(s['created_at']),
            'btc_claimed_at': iso(s['updated_at']),
            'btc_claimed_at_unix': unix(s['updated_at']),
            'seconds_from_offer_to_btc_claim': max(0, int(s['updated_at'] - s['created_at'])),
        })
    return {
        **envelope(now),
        'count': len(out),
        'swaps': out,
        'totals': {
            'completed': len(done),
            'refunded': sum(1 for s in swaps if s['state'] == 'mars_refunded'),
            'mars_sold_zubrins': sum(int(s['mars_amount_sat']) for s in done),
            'btc_received_sats': sum(int(s['btc_amount_sat']) for s in done),
        },
        'note': 'Swaps this maker sold and was paid for (it claimed the Bitcoin, which reveals '
                'the secret the buyer needs). Amounts and times only.',
    }
