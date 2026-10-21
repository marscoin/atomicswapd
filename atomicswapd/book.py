"""The public order book, merged from every relay."""

import time
from typing import Dict, Iterable, List, Optional

from . import policy

COIN = policy.COIN


def merge_offers(per_relay: Dict[str, Iterable[dict]], now: float) -> Dict[str, dict]:
    """One entry per offer id, remembering which relays carry it.

    Relays may hold slightly different copies; the most recently seen one
    wins. Expired and malformed offers are dropped.
    """
    merged: Dict[str, dict] = {}
    for relay, offers in per_relay.items():
        for o in offers:
            try:
                oid = str(o['offer_id'])
                mars_sat, btc_sat = int(o['mars_amount_sat']), int(o['btc_amount_sat'])
                expires_at = float(o['expires_at'])
            except (KeyError, TypeError, ValueError):
                continue
            if mars_sat <= 0 or btc_sat <= 0 or expires_at <= now:
                continue
            entry = merged.get(oid)
            if entry is None or float(o.get('last_seen') or 0) > float(entry['raw'].get('last_seen') or 0):
                merged[oid] = {'raw': o, 'relays': set(entry['relays']) if entry else set()}
            merged[oid]['relays'].add(relay)
    return merged


def public_offer(entry: dict, *, now: float, market_rate: Optional[float],
                 mars_height: Optional[int], btc_height: Optional[int],
                 taken: bool, ours: bool) -> dict:
    o = entry['raw']
    mars_sat, btc_sat = int(o['mars_amount_sat']), int(o['btc_amount_sat'])
    rate = btc_sat / mars_sat
    mars_locktime = int(o.get('mars_locktime') or 0)
    address = str(o.get('maker_address') or '')
    return {
        'id': str(o['offer_id'])[:12],
        'mars': mars_sat / COIN,
        'btc': btc_sat / COIN,
        'rate': rate,
        'sats_per_mars': round(rate * COIN, 2),
        'premium_percent': round((rate / market_rate - 1) * 100, 2) if market_rate else None,
        'expires_in': round(float(o['expires_at']) - now),
        'maker': address[:6] + '…' + address[-4:] if len(address) > 12 else address,
        'relays': len(entry['relays']),
        'taken': taken,
        'ours': ours,
        'mars_locktime': mars_locktime,
        'safety': policy.offer_safety(mars_locktime, mars_height, btc_height),
    }


def sorted_book(offers: List[dict]) -> List[dict]:
    """Cheapest first, like an ask ladder."""
    return sorted(offers, key=lambda o: (o['rate'], -o['mars']))


def depth(offers: List[dict]) -> List[dict]:
    """Cumulative MARS available at or below each price (open offers only)."""
    total = 0.0
    out = []
    for o in sorted_book([o for o in offers if not o['taken']]):
        total += o['mars']
        out.append({'sats_per_mars': o['sats_per_mars'], 'cumulative_mars': round(total, 8)})
    return out


def quote(offers: List[dict], btc_amount: float) -> Optional[dict]:
    """The single open, safe offer a buyer with ``btc_amount`` BTC would take.

    Swaps fill one whole offer, so this picks the cheapest offer whose
    full price fits the budget.
    """
    fits = [o for o in sorted_book(offers)
            if not o['taken'] and o['safety'].get('safe') and o['btc'] <= btc_amount + 1e-12]
    return fits[0] if fits else None
