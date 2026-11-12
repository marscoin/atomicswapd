"""Discovery documents for AI agents.

  /llms.txt                 orientation in plain text
  /.well-known/agent.json   the same facts for a program (also /api/agent)
  /openapi.json             OpenAPI 3.1 for every public endpoint
  /api/tools                tool definitions for the read endpoints

All four are generated from one list of endpoints (ENDPOINTS) and from
policy.py, so they cannot drift apart. The examples in the OpenAPI
document are produced by the real view functions from a fixed sample
state.
"""

from . import __version__, agentapi, agentprotocol, policy
from .market import Market

NAME = 'Marscoin atomic swap market'
DESCRIPTION = ('A public, read-only view of the open market for Bitcoin to Marscoin atomic '
               'swaps, and one of its makers. Offers are settled with hash time-locked '
               'contracts on both chains; nobody holds anyone else\'s coins.')
REPO_URL = 'https://github.com/marscoin/atomicswapd'
CONTACT_URL = REPO_URL + '/issues'
LINKS = {
    'source': REPO_URL,
    'agent_api_guide': REPO_URL + '/blob/main/docs/AGENTS-API.md',
    'reference_client': REPO_URL + '/tree/main/agent',
    'wallet': 'https://github.com/marscoin/electrum-mars',
    'relay_server': 'https://github.com/marscoin/electrumx',
    'how_swaps_work': 'https://marscoin.org/academy/atomic-swaps',
    'paper': 'https://marscoin.org/academy/ipas',
}

POLL_SECONDS = 10       # the order book is re-read from the relays this often by default
V1_MAX_AGE = 5
PROTOCOL_MAX_AGE = 60
DOCS_MAX_AGE = 300


# ---------------------------------------------------------------- endpoints

def _q(name, schema, description):
    return {'name': name, 'in': 'query', 'required': False, 'schema': schema,
            'description': description}


AMOUNT = {'type': 'string', 'pattern': r'^\d+(\.\d{1,8})?$'}

ENDPOINTS = [
    {
        'path': '/api/v1/market', 'tool': 'get_market', 'schema': 'Market', 'params': [],
        'summary': 'Price, chain tips, fee rates, and whether the maker is live.',
        'description': (
            'Current market for Bitcoin to Marscoin atomic swaps: the reference price (BTC per '
            'MARS, satoshis per MARS, USD) with its age and sources, the Marscoin and Bitcoin '
            'chain tips and how old they are, Bitcoin fee rates, and whether this node\'s maker '
            'is posting offers. Read-only. The price is a reference from public price feeds, '
            'not an offer: what you can actually buy is in list_offers.'),
    },
    {
        'path': '/api/v1/offers', 'tool': 'list_offers', 'schema': 'OfferList',
        'summary': 'The open offers, with a safety verdict for each.',
        'description': (
            'Open offers to sell MARS for BTC, from every relay this node watches. Each has '
            'integer amounts (zubrins of MARS, satoshis of BTC), its price and premium over the '
            'reference price, the Marscoin refund height and blocks left, seconds until it '
            'expires, the maker\'s public key and payment hash, and a safety object computed '
            'with the wallets\' refund-order rule. Offers are taken whole. Read-only: taking an '
            'offer happens on the relays with your own keys (see get_protocol). Offers are '
            'unsigned and expire within minutes; fetch again before acting, and recompute the '
            'safety verdict yourself.'),
        'params': [
            _q('min_mars', AMOUNT, 'Only offers of at least this many MARS (decimal, e.g. 100 or 12.5).'),
            _q('max_mars', AMOUNT, 'Only offers of at most this many MARS (decimal).'),
            _q('safe_only', {'type': 'boolean'},
               'Only offers that are well formed and pass the safety rule now.'),
            _q('sort', {'type': 'string', 'enum': ['price', 'size'], 'default': 'price'},
               'price: cheapest first (default). size: largest first.'),
        ],
    },
    {
        'path': '/api/v1/offers/{id}', 'tool': 'get_offer', 'schema': 'OfferDetail',
        'summary': 'One offer by id.',
        'description': (
            'One offer by its id (the full id, or a unique prefix of at least 8 hexadecimal '
            'characters), including offers a buyer has just accepted (status "taken"). Returns '
            'the error code offer_not_found once the offer has expired or been withdrawn, '
            'which is normal: offers live for minutes. Read-only.'),
        'params': [{'name': 'id', 'in': 'path', 'required': True,
                    'schema': {'type': 'string', 'pattern': '^[0-9a-f]{8,64}$'},
                    'description': 'Offer id, or a unique prefix of 8 or more hex characters.'}],
    },
    {
        'path': '/api/v1/quote', 'tool': 'get_quote', 'schema': 'Quote',
        'summary': 'Which whole offers fit an amount of MARS or a BTC budget.',
        'description': (
            'Which open, safe offers best fit a request, given exactly one of: mars (decimal '
            'MARS to buy) or btc_sats (integer satoshis to spend). Offers are taken whole, so '
            'the answer is a list of complete offers, cheapest price first, that fit inside '
            'the request; the rest is reported as unfilled or unspent. For a MARS request it '
            'also names the cheapest single offer that would cover the whole amount, if one '
            'exists. It never invents liquidity: with no usable offers the fills are empty. A '
            'quote reserves nothing and is not a commitment by anyone.'),
        'params': [
            _q('mars', AMOUNT, 'MARS to buy, decimal. Give this or btc_sats, not both.'),
            _q('btc_sats', {'type': 'integer', 'minimum': 1},
               'Satoshis of BTC to spend. Give this or mars, not both.'),
        ],
    },
    {
        'path': '/api/v1/swaps/recent', 'tool': 'list_recent_swaps', 'schema': 'RecentSwaps',
        'summary': 'Swaps this maker completed: amounts and times.',
        'description': (
            'Swaps this node\'s maker has completed (it sold MARS and claimed the BTC), newest '
            'first, with amounts, price and times only, plus totals. Other makers\' swaps are '
            'not visible to this node. Read-only.'),
        'params': [_q('limit', {'type': 'integer', 'minimum': 1,
                                'maximum': agentapi.MAX_RECENT_SWAPS, 'default': 25},
                      'How many swaps to return.')],
    },
    {
        'path': '/api/v1/protocol', 'tool': 'get_protocol', 'schema': 'Protocol', 'params': [],
        'summary': 'How to take an offer with your own keys, for a program.',
        'description': (
            'A machine-readable description of the swap protocol: relay addresses and JSON-RPC '
            'methods, the offer and acceptance formats, the contract script, timelocks, '
            'confirmation requirements, every check a taker must make before paying Bitcoin, '
            'fees, and the recovery paths. Read this before attempting a swap. This server '
            'cannot take an offer for you and has no write endpoints.'),
    },
]

OTHER_ENDPOINTS = [
    {'path': '/llms.txt', 'summary': 'Orientation for a language model, in plain text.',
     'media': 'text/plain'},
    {'path': '/.well-known/agent.json', 'summary': 'Machine-readable description of this service.',
     'schema': 'AgentDescription'},
    {'path': '/api/agent', 'summary': 'The same document as /.well-known/agent.json.',
     'schema': 'AgentDescription'},
    {'path': '/openapi.json', 'summary': 'This OpenAPI 3.1 document.', 'schema': 'OpenApiDocument'},
    {'path': '/api/tools', 'summary': 'Tool definitions for the read endpoints, in two common shapes.',
     'schema': 'ToolDefinitions'},
    {'path': '/api/snapshot', 'schema': 'Snapshot',
     'summary': 'Everything the dashboard shows, in the dashboard\'s own shape. Prefer /api/v1.'},
    {'path': '/api/history', 'schema': 'History',
     'summary': '30 days of hourly MARS/BTC and MARS/USD market prices.'},
    {'path': '/api/stream', 'media': 'text/event-stream',
     'summary': 'The snapshot as server-sent events (event: snapshot), sent on every change.'},
    {'path': '/healthz', 'summary': 'Liveness check.', 'schema': 'Health'},
]


# ---------------------------------------------------------------- schemas

def _n(*types):
    return {'type': list(types) + ['null']}


def _obj(props: dict, optional=()) -> dict:
    return {'type': 'object', 'properties': props,
            'required': [k for k in props if k not in optional], 'additionalProperties': False}


INT, NUM, STR, BOOL = {'type': 'integer'}, {'type': 'number'}, {'type': 'string'}, {'type': 'boolean'}
ENVELOPE = {'api_version': {'type': 'string', 'enum': [agentapi.API_VERSION]},
            'generated_at': {'type': 'string', 'description': 'ISO 8601, UTC'},
            'generated_at_unix': INT}


def _ref(name: str) -> dict:
    return {'$ref': f'#/components/schemas/{name}'}


def schemas() -> dict:
    offer = _obj({
        'id': {'type': 'string', 'description': 'The offer id used on the relays.'},
        'status': {'type': 'string', 'enum': ['open', 'taken']},
        'mars_amount_zubrins': {'type': 'integer', 'description': 'MARS sold, in zubrins (1e-8 MARS).'},
        'btc_amount_sats': {'type': 'integer', 'description': 'BTC wanted, in satoshis.'},
        'price_btc_per_mars': NUM,
        'price_sats_per_mars': NUM,
        'premium_percent': {**_n('number'), 'description': 'Over the reference price; null without one.'},
        'mars_locktime_height': {**_n('integer'),
                                 'description': 'Marscoin height at which the maker can refund.'},
        'mars_locktime_blocks_left': _n('integer'),
        'expires_at': STR, 'expires_at_unix': INT, 'expires_in_seconds': INT,
        'maker_pubkey': {**_n('string'), 'description': '33-byte compressed key, hex; null if malformed.'},
        'payment_hash160': {**_n('string'), 'description': '20 bytes, hex; null if malformed.'},
        'from_this_maker': {'type': 'boolean', 'description': 'Posted by this node\'s own maker.'},
        'relays_seen': {'type': 'integer', 'description': 'How many watched relays carry the offer.'},
        'well_formed': {'type': 'boolean',
                        'description': 'False if a field a wallet needs is missing or malformed.'},
        'safety': _ref('Safety'),
    })
    fill = _obj({
        'offer_id': STR, 'mars_amount_zubrins': INT, 'btc_amount_sats': INT,
        'price_sats_per_mars': NUM, 'premium_percent': _n('number'), 'expires_in_seconds': INT,
        'excess_mars_zubrins': INT}, optional=('excess_mars_zubrins',))
    return {
        'Error': _obj({'error': _obj({'code': STR, 'message': STR})}),
        'Safety': {**_obj({
            'safe': {**_n('boolean'), 'description': 'null while a block height is unknown.'},
            'margin_seconds': {**_n('integer'),
                               'description': 'Pessimistic time between the Bitcoin refund and '
                                              'the Marscoin refund if accepted now.'},
            'min_margin_seconds': INT,
            'reason': _n('string')}),
            'description': 'The wallets\' refund-order rule (atomicswapd/policy.py offer_safety). '
                           'It does not cover the on-chain checks a taker makes after accepting.'},
        'Offer': offer,
        'Market': _obj({
            **ENVELOPE,
            'price': _obj({
                'btc_per_mars': _n('number'), 'sats_per_mars': _n('number'),
                'usd_per_mars': _n('number'), 'usd_per_btc': _n('number'),
                'usd_per_mars_source': STR, 'usd_per_btc_source': STR,
                'updated_at': _n('string'), 'updated_at_unix': _n('integer'),
                'age_seconds': _n('integer'),
                'fresh': {'type': 'boolean',
                          'description': 'The maker prices offers only from a fresh price.'},
                'max_age_seconds': INT}),
            'chains': _obj({
                'marscoin': _obj({
                    'height': _n('integer'), 'tip_time': _n('string'), 'tip_time_unix': _n('integer'),
                    'tip_age_seconds': _n('integer'), 'target_block_seconds': INT, 'source': STR,
                    'relays_connected': INT}),
                'bitcoin': _obj({
                    'height': _n('integer'), 'tip_time': _n('string'), 'tip_time_unix': _n('integer'),
                    'tip_age_seconds': _n('integer'), 'target_block_seconds': INT, 'source': STR,
                    'fetched_at': _n('string'), 'fetched_at_unix': _n('integer'),
                    'fee_rate_fast_sat_per_vb': _n('number'),
                    'fee_rate_hour_sat_per_vb': _n('number')})}),
            'maker': _obj({
                'mode': {'type': 'string', 'enum': ['watch', 'live']},
                'live': {'type': 'boolean', 'description': 'The maker is running in live mode.'},
                'open_offers': INT}),
            'book': _obj({'open_offers': INT, 'open_mars_zubrins': INT, 'safe_open_offers': INT}),
            'feed_error': _n('string')}),
        'OfferList': _obj({
            **ENVELOPE, 'marscoin_height': _n('integer'), 'bitcoin_height': _n('integer'),
            'market_price_btc_per_mars': _n('number'),
            'sort': {'type': 'string', 'enum': ['price', 'size']},
            'count': INT, 'open_offers_total': INT,
            'offers': {'type': 'array', 'items': _ref('Offer')}, 'note': STR}),
        'OfferDetail': _obj({
            **ENVELOPE, 'marscoin_height': _n('integer'), 'bitcoin_height': _n('integer'),
            'offer': _ref('Offer'), 'note': STR}),
        'Fill': fill,
        'Quote': _obj({
            **ENVELOPE,
            'request': {'type': 'object', 'properties': {'mars_zubrins': INT, 'btc_sats': INT},
                        'additionalProperties': False},
            'fills': {'type': 'array', 'items': _ref('Fill'),
                      'description': 'Whole offers, cheapest price first. Empty if nothing fits.'},
            'fully_filled': BOOL,
            'total_mars_zubrins': INT, 'total_btc_sats': INT,
            'average_price_btc_per_mars': _n('number'), 'average_price_sats_per_mars': _n('number'),
            'mars_claim_fee_zubrins': INT,
            'net_mars_zubrins': {'type': 'integer',
                                 'description': 'total_mars_zubrins minus the Marscoin claim fees.'},
            'unfilled_mars_zubrins': _n('integer'), 'unspent_btc_sats': _n('integer'),
            'alternative_single_offer': {'anyOf': [_ref('Fill'), {'type': 'null'}]},
            'offers_considered': INT, 'offers_excluded': INT,
            'notes': {'type': 'array', 'items': STR}}),
        'RecentSwaps': _obj({
            **ENVELOPE, 'count': INT,
            'swaps': {'type': 'array', 'items': _obj({
                'id': STR, 'mars_amount_zubrins': INT, 'btc_amount_sats': INT,
                'price_btc_per_mars': _n('number'), 'price_sats_per_mars': _n('number'),
                'offer_created_at': STR, 'offer_created_at_unix': INT,
                'btc_claimed_at': {'type': 'string',
                                   'description': 'When the swap record last changed: the '
                                                  'maker\'s Bitcoin claim.'},
                'btc_claimed_at_unix': INT, 'seconds_from_offer_to_btc_claim': INT})},
            'totals': _obj({'completed': INT, 'refunded': INT, 'mars_sold_zubrins': INT,
                            'btc_received_sats': INT}),
            'note': STR}),
        'Protocol': {'type': 'object', 'required': [
            'api_version', 'generated_at', 'generated_at_unix', 'summary', 'units', 'transport',
            'relay_methods', 'offer_format', 'acceptance_format', 'relay_limits', 'htlc',
            'timelocks', 'confirmations', 'sequence', 'taker_checks', 'fees', 'recovery',
            'what_this_server_does', 'what_this_server_does_not_do', 'unconfirmed'],
            'description': 'See docs/AGENTS-API.md. Sections may gain fields.'},
        'AgentDescription': {'type': 'object', 'required': [
            'name', 'description', 'version', 'api_version', 'base_url', 'endpoints', 'units',
            'limits', 'safety_rules', 'contact', 'links']},
        'OpenApiDocument': {'type': 'object', 'required': ['openapi', 'info', 'paths']},
        'Tool': _obj({'name': STR, 'description': STR, 'input_schema': {'type': 'object'}}),
        'FunctionTool': _obj({'type': {'type': 'string', 'enum': ['function']},
                              'function': _obj({'name': STR, 'description': STR,
                                                'parameters': {'type': 'object'}})}),
        'ToolDefinitions': _obj({
            'api_version': STR, 'base_url': STR,
            'tools': {'type': 'array', 'items': _ref('Tool')},
            'functions': {'type': 'array', 'items': _ref('FunctionTool')},
            'execution': {'type': 'object',
                          'description': 'For each tool name: the GET request that runs it.'},
            'note': STR}),
        'Snapshot': {'type': 'object', 'required': [
            'version', 'revision', 'now', 'mode', 'chains', 'market', 'relays', 'book', 'depth',
            'timeline', 'maker', 'setup', 'events'],
            'description': 'The dashboard\'s state. Amounts are decimal coins; fields may change.'},
        'History': _obj({'mars_btc': {'type': 'array', 'items': {'type': 'array'}},
                         'mars_usd': {'type': 'array', 'items': {'type': 'array'}}}),
        'Health': _obj({'ok': BOOL, 'revision': INT}),
    }


# ---------------------------------------------------------------- examples

def example_state() -> dict:
    """A small fixed market, so the documented examples come from real code."""
    t = 1_794_000_000
    market = Market(mars_usd=0.0612, btc_usd=68_000.0, prices_at=t - 20, btc_height=900_000,
                    btc_tip_time=t - 300, btc_at=t - 5, btc_fee_fast=4, btc_fee_hour=2)

    def offer(oid, mars, btc, locktime):
        return {'raw': {'offer_id': oid, 'mars_amount_sat': mars * policy.COIN,
                        'btc_amount_sat': btc, 'maker_pubkey': '02' + oid[:2] * 32,
                        'payment_hash160': oid[:2] * 20, 'mars_locktime': locktime,
                        'expires_at': t + 600, 'last_seen': t - 30},
                'relays': {'electrum.marscoin.org'}}
    return {
        'version': __version__, 'mode': 'live', 'market': market, 'max_price_age_seconds': 600,
        'mars_height': 3_700_000, 'mars_tip_time': t - 90,
        'offers': {
            'a1' * 16: offer('a1' * 16, 100, 9_450, 3_700_470),
            'b2' * 16: offer('b2' * 16, 200, 19_260, 3_700_470),
            'c3' * 16: offer('c3' * 16, 300, 26_000, 3_700_150),
        },
        'taken': set(), 'ours': {'a1' * 16, 'b2' * 16}, 'maker_running': True,
        'maker_swaps': [{'id': 'd4d4d4d4', 'state': 'btc_claimed', 'mars_amount_sat': 150 * policy.COIN,
                         'btc_amount_sat': 14_200, 'created_at': t - 7_200, 'updated_at': t - 4_100,
                         'accepted': True}],
        'relays': [{'host': 'electrum.marscoin.org', 'port': 50002, 'tls_verify': True}],
        'relays_connected': 1,
        'maker_config': {'offer_minutes': 15, 'refresh_seconds': 300, 'max_pending_per_taker': 1,
                         'min_offer_mars': 10, 'max_offer_mars': 1000},
        'poll_seconds': 10, 'public_url': '',
    }


EXAMPLE_NOW = 1_794_000_000


def examples() -> dict:
    st, now = example_state(), EXAMPLE_NOW
    return {
        'Market': agentapi.market_view(st, now),
        'OfferList': agentapi.offers_view(st, now, {}),
        'OfferDetail': agentapi.offer_detail(st, now, 'a1' * 16),
        'Quote': agentapi.quote_view(st, now, {'mars': '250'}),
        'RecentSwaps': agentapi.recent_swaps_view(st, now, {}),
        'Protocol': agentprotocol.protocol_view(st, now),
        'Error': agentapi.ApiError(404, 'offer_not_found', 'no open or recently taken offer '
                                   'has this id; offers expire after minutes').body(),
        'History': {'mars_btc': [[1793996400, 9.0e-07]], 'mars_usd': [[1793996400, 0.0612]]},
        'Health': {'ok': True, 'revision': 1234},
    }


# ---------------------------------------------------------------- openapi

def _json_response(schema: str, description: str, example=None) -> dict:
    content = {'schema': _ref(schema)}
    if example is not None:
        content['example'] = example
    return {'description': description, 'content': {'application/json': content}}


def openapi(base_url: str) -> dict:
    ex = examples()
    error = lambda text: _json_response('Error', text, ex['Error'])   # noqa: E731
    paths = {}
    for e in ENDPOINTS:
        responses = {'200': _json_response(e['schema'], e['summary'], ex.get(e['schema']))}
        if e['params']:
            responses['400'] = error('A parameter is missing or malformed.')
        if '{id}' in e['path']:
            responses['404'] = error('No such offer (expired, withdrawn, or never existed).')
        paths[e['path']] = {'get': {
            'operationId': e['tool'], 'summary': e['summary'], 'description': e['description'],
            'tags': ['agent API v1'], 'parameters': e['params'], 'responses': responses}}
    for e in OTHER_ENDPOINTS:
        if e.get('media'):
            ok = {'description': e['summary'], 'content': {e['media']: {'schema': {'type': 'string'}}}}
        else:
            ok = _json_response(e['schema'], e['summary'], ex.get(e['schema']))
        tag = 'dashboard' if e['path'] in ('/api/snapshot', '/api/history', '/api/stream', '/healthz') \
            else 'discovery'
        paths[e['path']] = {'get': {
            'operationId': 'get_' + e['path'].strip('/').replace('/', '_').replace('.', '_').replace('-', '_'),
            'summary': e['summary'], 'tags': [tag], 'parameters': [], 'responses': {'200': ok}}}
    return {
        'openapi': '3.1.0',
        'info': {
            'title': NAME,
            'version': __version__,
            'summary': 'Read-only market data for Bitcoin to Marscoin atomic swaps.',
            'description': (
                DESCRIPTION + ' Every endpoint is GET, needs no authentication, and allows any '
                'origin. There are no write endpoints: a swap is carried out by the taker\'s own '
                'software against the relays and both chains (see /api/v1/protocol). Amounts '
                'ending _zubrins are integers of 1e-8 MARS; amounts ending _sats are integers of '
                f'1e-8 BTC. Responses under /api/v1 may be cached for {V1_MAX_AGE} seconds; the '
                f'order book changes about every {POLL_SECONDS} seconds, so polling faster gains '
                'nothing. For live updates use /api/stream.'),
            'license': {'name': 'MIT', 'identifier': 'MIT'},
            'contact': {'name': 'atomicswapd issues', 'url': CONTACT_URL},
        },
        'servers': [{'url': base_url}],
        'tags': [{'name': 'agent API v1', 'description': 'Stable field names, explicit units.'},
                 {'name': 'discovery', 'description': 'Documents describing this service.'},
                 {'name': 'dashboard', 'description': 'What the web dashboard uses.'}],
        'paths': paths,
        'components': {'schemas': schemas()},
    }


# ---------------------------------------------------------------- tools

def _input_schema(e: dict) -> dict:
    props, required = {}, []
    for p in e['params']:
        props[p['name']] = {**p['schema'], 'description': p['description']}
        if p.get('required'):
            required.append(p['name'])
    return {'type': 'object', 'properties': props, 'required': required,
            'additionalProperties': False}


def tools(base_url: str) -> dict:
    plain, functions, execution = [], [], {}
    for e in ENDPOINTS:
        schema = _input_schema(e)
        plain.append({'name': e['tool'], 'description': e['description'], 'input_schema': schema})
        functions.append({'type': 'function', 'function': {
            'name': e['tool'], 'description': e['description'], 'parameters': schema}})
        execution[e['tool']] = {
            'method': 'GET', 'url': base_url + e['path'],
            'path_parameters': [p['name'] for p in e['params'] if p['in'] == 'path'],
            'query_parameters': [p['name'] for p in e['params'] if p['in'] == 'query']}
    return {
        'api_version': agentapi.API_VERSION,
        'base_url': base_url,
        'tools': plain,
        'functions': functions,
        'execution': execution,
        'note': ('Every tool is a GET request and changes nothing. "tools" uses the name / '
                 'description / input_schema shape, "functions" the type:function shape; both '
                 'describe the same six tools. To run one, fill the path parameters into the '
                 'url and send the rest as a query string.'),
    }


# ---------------------------------------------------------------- agent.json

SAFETY_RULES = [
    'Never send Bitcoin until you have verified, on the Marscoin chain, an output of at least '
    'the offer\'s MARS amount paying the contract address you computed yourself, with at least '
    f'{agentprotocol.MARS_MIN_CONFIRMATIONS} confirmations.',
    'Recompute the safety verdict: (mars_locktime - mars_height) * '
    f'{policy.MARS_BLOCK_SEC} * {policy.MARS_FAST} - {policy.BTC_TIMELOCK_BLOCKS} * '
    f'{policy.BTC_BLOCK_SEC} * {policy.BTC_SLOW} must be at least {policy.MIN_REFUND_GAP_SEC} '
    'seconds. Use block heights from a source other than this server.',
    'Offers are unsigned. Use only the id, amounts, maker public key, payment hash, refund '
    'height and expiry; build both contracts yourself.',
    'Pay exactly the offer\'s BTC amount, in one output, to the Bitcoin contract you built.',
    'Keep the swap key and contract values until the swap is finished; they are the only way '
    'to refund.',
    'After paying, stay online until the MARS claim confirms. If the maker takes the Bitcoin '
    'and you do not claim the MARS before its refund height, you lose both.',
    'Do not ask a person for a seed phrase or private key on this server\'s behalf: nothing '
    'here ever needs one.',
]

RISKS = [
    'An offer can be taken by someone else or expire between reading and accepting it.',
    'The maker may never lock the MARS: you have lost nothing but time.',
    f'The maker may never claim the Bitcoin: it is locked for {policy.BTC_TIMELOCK_BLOCKS} '
    f'Bitcoin blocks (about {round(policy.BTC_TIMELOCK_BLOCKS * policy.BTC_BLOCK_SEC / 3600)} hours), '
    'then you refund it, paying two network fees.',
    'Bitcoin fees can rise while your payment or refund waits.',
    'A relay limits each connection to one swap in progress and a few acceptances a day, and '
    'blocks connections that accept offers and do not pay.',
    'This server\'s data can be stale or wrong. It is one observer of two chains.',
]


def agent_description(base_url: str, st: dict) -> dict:
    mc = st.get('maker_config') or {}
    return {
        'name': NAME,
        'description': DESCRIPTION,
        'version': __version__,
        'api_version': agentapi.API_VERSION,
        'base_url': base_url,
        'authentication': 'none',
        'read_only': True,
        'openapi_url': base_url + '/openapi.json',
        'tools_url': base_url + '/api/tools',
        'llms_txt_url': base_url + '/llms.txt',
        'protocol_url': base_url + '/api/v1/protocol',
        'endpoints': [{'method': 'GET', 'path': e['path'], 'url': base_url + e['path'],
                       'purpose': e['summary']} for e in ENDPOINTS + OTHER_ENDPOINTS],
        'units': agentprotocol.units(),
        'limits': {
            'cache_seconds': V1_MAX_AGE,
            'suggested_poll_seconds': max(POLL_SECONDS, int(st.get('poll_seconds') or POLL_SECONDS)),
            'live_updates': base_url + '/api/stream',
            'offer_lifetime_seconds': int(mc.get('offer_minutes', 15) * 60),
            'offer_refresh_seconds': mc.get('refresh_seconds'),
            'swaps_in_progress_per_taker': mc.get('max_pending_per_taker'),
            'partial_fills': False,
            'note': 'There is no request quota in the server, but the order book changes only '
                    'when the relays are re-read. Poll no faster than suggested_poll_seconds.',
        },
        'capabilities': {
            'reads_market': True, 'quotes': True, 'accepts_offers': False,
            'holds_user_funds': False, 'signs_transactions_for_users': False,
        },
        'safety_rules': SAFETY_RULES,
        'risks': RISKS,
        'contact': {'issues': CONTACT_URL},
        'links': LINKS,
    }


# ---------------------------------------------------------------- llms.txt

def _hours(seconds: float) -> int:
    return round(seconds / 3600)

def llms_txt(base_url: str, st: dict) -> str:
    mc = st.get('maker_config') or {}
    lines = [
        f'# {NAME}',
        '',
        f'> {DESCRIPTION}',
        '',
        'This file is written for language models and the programs that run them.',
        '',
        '## What this is',
        '',
        '- A market where people sell Marscoin (MARS) for Bitcoin (BTC) without an exchange.',
        '- Offers are posted on public relays (Marscoin ElectrumX servers). This server reads',
        '  them, adds prices and a safety verdict, and runs one of the makers.',
        '- Everything here is read-only. There is no account, no API key, and no endpoint that',
        '  takes an offer or a payment. A swap is done by your own software, with your own keys,',
        '  against the relays and the two chains.',
        '',
        '## What an atomic swap guarantees',
        '',
        '- The maker locks MARS in a contract only you can open, with a secret the maker knows.',
        f'  The maker can take it back after {policy.MARS_MAKER_TIMELOCK_BLOCKS} Marscoin blocks '
        f'(about {_hours(policy.MARS_MAKER_TIMELOCK_BLOCKS * policy.MARS_BLOCK_SEC)} hours).',
        '- You lock BTC in a contract only the maker can open, with the same secret. You can take',
        f'  it back after {policy.BTC_TIMELOCK_BLOCKS} Bitcoin blocks '
        f'(about {_hours(policy.BTC_TIMELOCK_BLOCKS * policy.BTC_BLOCK_SEC)} hours).',
        '- When the maker takes the BTC, the secret appears on the Bitcoin chain, and you use it',
        '  to take the MARS. Either both happen or both sides refund.',
        '- The guarantee holds only if you make the checks below and stay online to claim.',
        '',
        '## Units',
        '',
        '- MARS: Marscoin. 1 MARS = 100,000,000 zubrins. Fields ending `_zubrins` are integers.',
        '- BTC: Bitcoin. 1 BTC = 100,000,000 satoshis. Fields ending `_sats` are integers.',
        '- Prices are BTC per MARS, and satoshis per whole MARS.',
        '- Times: `_at` is ISO 8601 UTC, `_at_unix` is unix seconds, `_seconds` is a duration.',
        '',
        '## Endpoints (all GET, JSON unless noted, any origin)',
        '',
    ]
    for e in ENDPOINTS + OTHER_ENDPOINTS:
        lines.append(f'- [{e["path"]}]({base_url}{e["path"]}): {e["summary"]}')
    lines += [
        '',
        'Query parameters: `/api/v1/offers?min_mars=&max_mars=&safe_only=true&sort=price|size`,',
        '`/api/v1/quote?mars=250` or `/api/v1/quote?btc_sats=20000`, `/api/v1/swaps/recent?limit=25`.',
        'Errors are `{"error": {"code": "...", "message": "..."}}` with a 4xx status.',
        '',
        '## Rules for reading',
        '',
        '- Offers are taken whole. There are no partial fills; a quote lists complete offers.',
        f'- Offers from this maker live {int(mc.get("offer_minutes", 15))} minutes and are replaced every '
        f'{mc.get("refresh_seconds", 300)} seconds with new ids.',
        '  An id you saw a few minutes ago may be gone: that is normal.',
        f'- Responses are cacheable for {V1_MAX_AGE} seconds and the book is re-read about every '
        f'{POLL_SECONDS} seconds.',
        '  Do not poll faster. For live updates read `/api/stream` (server-sent events).',
        '- `price` in `/api/v1/market` is a reference from public price feeds. It is not an offer.',
        '',
        '## Safety rules an agent must apply',
        '',
    ]
    lines += [f'{i}. {rule}' for i, rule in enumerate(SAFETY_RULES, 1)]
    lines += ['', '## What can go wrong', '']
    lines += [f'- {risk}' for risk in RISKS]
    lines += [
        '',
        '## How to carry out a swap',
        '',
        f'Read [{base_url}/api/v1/protocol]({base_url}/api/v1/protocol) first. Two ways exist:',
        '',
        '- Use a wallet that implements the protocol: Electrum-Mars (desktop) or the Martian',
        '  Republic app (phone). Both make every check above. Electrum-Mars has no command-line',
        '  or RPC interface for swaps; a person confirms each swap in its window.',
        '- Implement the protocol yourself from `/api/v1/protocol`. The reference client in the',
        '  repository reads this API and recomputes the safety checks, but does not handle keys',
        '  or payments.',
        '',
        '## Links',
        '',
    ]
    lines += [f'- [{key.replace("_", " ")}]({url})' for key, url in LINKS.items()]
    lines += [f'- [OpenAPI]({base_url}/openapi.json)', f'- [Tool definitions]({base_url}/api/tools)',
              f'- [Agent description]({base_url}/.well-known/agent.json)', '']
    return '\n'.join(lines)
