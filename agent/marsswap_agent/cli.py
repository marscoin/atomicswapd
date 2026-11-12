"""Command line for the reference client.

  python -m marsswap_agent market
  python -m marsswap_agent offers [--min-mars N] [--max-mars N] [--safe-only] [--sort price|size]
  python -m marsswap_agent quote (--mars N | --btc-sats N)
  python -m marsswap_agent check-offer ID [--no-independent] [--taker-pubkey HEX]
  python -m marsswap_agent protocol
  python -m marsswap_agent mcp

Options for every command: --url (or MARSSWAP_URL; default
https://swaps.marscoin.org) and --json.

Exit codes:
  0  yes: data returned; for check-offer, the offer is safe to accept;
     for quote, at least one offer fits
  1  no: the offer is not safe or its safety is unknown; nothing fits the quote
  2  no answer: network error, API error (such as an expired offer id), bad usage
  3  check-offer only: this client and the server disagree about safety

This client reads and checks. It holds no keys and sends no transactions.
"""

import argparse
import json
import os
import sys

from . import report
from .client import ApiError, Client, DEFAULT_URL, NetworkError, http_get
from .report import EXIT_ERROR, EXIT_NO, EXIT_OK, mars, pct


def _age(seconds) -> str:
    return 'unknown' if seconds is None else f'{seconds} s ago'


def format_market(m: dict) -> str:
    p, c = m['price'], m['chains']
    return '\n'.join([
        f'price: {p["sats_per_mars"]} sats/MARS ({p["btc_per_mars"]} BTC/MARS), '
        f'${p["usd_per_mars"]} per MARS, updated {_age(p["age_seconds"])}'
        + ('' if p['fresh'] else '  [STALE]'),
        f'Marscoin: block {c["marscoin"]["height"]}, found {_age(c["marscoin"]["tip_age_seconds"])}',
        f'Bitcoin:  block {c["bitcoin"]["height"]}, found {_age(c["bitcoin"]["tip_age_seconds"])}, '
        f'fees {c["bitcoin"]["fee_rate_fast_sat_per_vb"]} sat/vB fast, '
        f'{c["bitcoin"]["fee_rate_hour_sat_per_vb"]} within the hour',
        f'maker: {"live" if m["maker"]["live"] else "not live"} ({m["maker"]["mode"]} mode), '
        f'{m["maker"]["open_offers"]} of its offers open',
        f'book: {m["book"]["open_offers"]} open offers, {mars(m["book"]["open_mars_zubrins"])} MARS, '
        f'{m["book"]["safe_open_offers"]} safe to take',
    ] + ([f'feed error: {m["feed_error"]}'] if m.get('feed_error') else []))


def format_offers(body: dict) -> str:
    if not body['offers']:
        return f'no offers match ({body["open_offers_total"]} open in total)'
    safe = {True: 'safe', False: 'UNSAFE', None: 'unknown'}
    lines = [f'{"id":<32}  {"MARS":>12}  {"sats":>10}  {"sats/MARS":>10}  {"premium":>8}  '
             f'{"blocks":>6}  {"expires":>7}  safety']
    for o in body['offers']:
        lines.append(
            f'{o["id"]:<32}  {mars(o["mars_amount_zubrins"]):>12}  {o["btc_amount_sats"]:>10}  '
            f'{o["price_sats_per_mars"]:>10}  {pct(o["premium_percent"]):>8}  '
            f'{str(o["mars_locktime_blocks_left"]):>6}  {str(o["expires_in_seconds"]) + "s":>7}  '
            f'{safe[o["safety"]["safe"]]}' + ('' if o['well_formed'] else ' (malformed)'))
    lines.append(body['note'])
    return '\n'.join(lines)


def format_quote(q: dict) -> str:
    lines = []
    if not q['fills']:
        lines.append('nothing fits: no whole, safe offer is within this request')
    for f in q['fills']:
        lines.append(f'take {f["offer_id"]}: {mars(f["mars_amount_zubrins"])} MARS for '
                     f'{f["btc_amount_sats"]} sats ({f["price_sats_per_mars"]} sats/MARS)')
    if q['fills']:
        lines.append(f'total: {mars(q["total_mars_zubrins"])} MARS for {q["total_btc_sats"]} sats, '
                     f'average {q["average_price_sats_per_mars"]} sats/MARS; '
                     f'{mars(q["net_mars_zubrins"])} MARS after claim fees')
    if q['unfilled_mars_zubrins']:
        lines.append(f'unfilled: {mars(q["unfilled_mars_zubrins"])} MARS')
    if q['unspent_btc_sats']:
        lines.append(f'unspent: {q["unspent_btc_sats"]} sats')
    alt = q.get('alternative_single_offer')
    if alt:
        lines.append(f'or one offer covering it all: {alt["offer_id"]}, '
                     f'{mars(alt["mars_amount_zubrins"])} MARS for {alt["btc_amount_sats"]} sats '
                     f'({mars(alt["excess_mars_zubrins"])} MARS more than asked)')
    lines.append(f'{q["offers_considered"]} offers considered, {q["offers_excluded"]} excluded')
    lines += [f'note: {n}' for n in q['notes'][:1]]
    return '\n'.join(lines)


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--url', default=os.environ.get('MARSSWAP_URL', DEFAULT_URL),
                        help='the atomicswapd node (default: %(default)s)')
    common.add_argument('--json', action='store_true', help='print the JSON answer')
    p = argparse.ArgumentParser(prog='marsswap_agent', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('market', parents=[common], help='price, chain tips, fees, maker status')
    o = sub.add_parser('offers', parents=[common], help='open offers')
    o.add_argument('--min-mars')
    o.add_argument('--max-mars')
    o.add_argument('--safe-only', action='store_true')
    o.add_argument('--sort', choices=['price', 'size'])
    q = sub.add_parser('quote', parents=[common], help='whole offers that fit an amount')
    amount = q.add_mutually_exclusive_group(required=True)
    amount.add_argument('--mars', help='MARS to buy, decimal')
    amount.add_argument('--btc-sats', help='satoshis to spend')
    c = sub.add_parser('check-offer', parents=[common],
                       help='recompute an offer\'s safety and compare with the server')
    c.add_argument('id')
    c.add_argument('--no-independent', dest='independent', action='store_false',
                   help='judge by the server\'s block heights alone, without asking public '
                        'explorers (a stale or dishonest server can then mislead you)')
    c.add_argument('--taker-pubkey', help='your 33-byte compressed public key (hex), to print '
                                          'the contract addresses for this offer')
    sub.add_parser('protocol', parents=[common], help='the protocol description (JSON)')
    sub.add_parser('mcp', parents=[common], help='serve the read tools over MCP (stdio)')
    return p


def main(argv=None, fetch=http_get, out=None) -> int:
    out = out or sys.stdout
    try:
        args = parser().parse_args(argv)
    except SystemExit as e:
        return EXIT_OK if e.code == 0 else EXIT_ERROR
    client = Client(args.url, fetch=fetch)

    def emit(data, text):
        print(json.dumps(data, indent=2) if args.json else text, file=out)

    try:
        if args.command == 'mcp':
            from . import mcp
            return mcp.serve(client, fetch=fetch)
        if args.command == 'market':
            m = client.market()
            emit(m, format_market(m))
            return EXIT_OK
        if args.command == 'offers':
            body = client.offers(args.min_mars, args.max_mars, args.safe_only, args.sort)
            emit(body, format_offers(body))
            return EXIT_OK
        if args.command == 'quote':
            q = client.quote(mars=args.mars, btc_sats=args.btc_sats)
            emit(q, format_quote(q))
            return EXIT_OK if q['fills'] else EXIT_NO
        if args.command == 'protocol':
            print(json.dumps(client.protocol(), indent=2), file=out)
            return EXIT_OK
        if args.command == 'check-offer':
            if args.taker_pubkey:
                from .safety import _PUBKEY
                if not _PUBKEY.match(args.taker_pubkey):
                    print('error: --taker-pubkey must be a 33-byte compressed public key in hex '
                          '(a PUBLIC key; never pass a private key)', file=sys.stderr)
                    return EXIT_ERROR
            r = report.check_offer(client, args.id, independent=args.independent,
                                   taker_pubkey=args.taker_pubkey, fetch=fetch)
            emit(r, report.format_check(r))
            return r['exit_code']
    except ApiError as e:
        print(f'error: {e.code}: {e.message}', file=sys.stderr)
        return EXIT_ERROR
    except NetworkError as e:
        print(f'error: {e}', file=sys.stderr)
        return EXIT_ERROR
    return EXIT_ERROR
