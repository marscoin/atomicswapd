"""A small client for the /api/v1 endpoints of an atomicswapd node.

Standard library only. Read-only: every call is an HTTP GET.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = 'https://swaps.marscoin.org'
USER_AGENT = 'marsswap-agent/0.1'

# A second view of the Marscoin tip, as the phone wallet uses.
MARS_EXPLORER_STATUS = 'https://explore.marscoin.org/api/status'
BTC_TIP_URLS = ('https://mempool.space/api/blocks/tip/height',
                'https://blockstream.info/api/blocks/tip/height')


class ApiError(Exception):
    """The server answered with {"error": {"code", "message"}}."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(f'{code}: {message}')
        self.status, self.code, self.message = status, code, message


class NetworkError(Exception):
    """No usable answer: unreachable, timed out, or not JSON."""


def http_get(url: str, timeout: float = 15):
    """GET ``url``; returns (status, body bytes). Raises NetworkError."""
    if not url.startswith(('https://', 'http://')):
        raise NetworkError(f'not an http(s) address: {url}')
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT,
                                                   'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise NetworkError(f'{url}: {getattr(e, "reason", e)}')


class Client:

    def __init__(self, base_url: str = DEFAULT_URL, fetch=http_get):
        self.base_url = base_url.rstrip('/')
        self.fetch = fetch

    def get(self, path: str, **params):
        query = urllib.parse.urlencode(
            {k: ('true' if v is True else v) for k, v in params.items()
             if v is not None and v is not False})
        url = self.base_url + path + ('?' + query if query else '')
        status, body = self.fetch(url)
        try:
            data = json.loads(body)
        except ValueError:
            raise NetworkError(f'{url}: answered {status} without JSON')
        if status >= 400 or (isinstance(data, dict) and set(data) == {'error'}):
            err = data.get('error') if isinstance(data, dict) else None
            if isinstance(err, dict):
                raise ApiError(status, str(err.get('code')), str(err.get('message')))
            raise NetworkError(f'{url}: answered {status}')
        return data

    def market(self) -> dict:
        return self.get('/api/v1/market')

    def offers(self, min_mars=None, max_mars=None, safe_only=False, sort=None) -> dict:
        return self.get('/api/v1/offers', min_mars=min_mars, max_mars=max_mars,
                        safe_only=safe_only, sort=sort)

    def offer(self, offer_id: str) -> dict:
        return self.get('/api/v1/offers/' + urllib.parse.quote(offer_id, safe=''))

    def quote(self, mars=None, btc_sats=None) -> dict:
        return self.get('/api/v1/quote', mars=mars, btc_sats=btc_sats)

    def recent_swaps(self, limit=None) -> dict:
        return self.get('/api/v1/swaps/recent', limit=limit)

    def protocol(self) -> dict:
        return self.get('/api/v1/protocol')

    def tools(self) -> dict:
        return self.get('/api/tools')


def second_marscoin_height(fetch=http_get, url: str = MARS_EXPLORER_STATUS):
    """The Marscoin tip according to a block explorer's own node, or None."""
    try:
        status, body = fetch(url)
        height = json.loads(body)['info']['blocks']
    except (NetworkError, ValueError, KeyError, TypeError):
        return None
    return height if isinstance(height, int) and not isinstance(height, bool) and height > 0 else None


def second_bitcoin_heights(fetch=http_get, urls=BTC_TIP_URLS) -> list:
    """The Bitcoin tip according to each public source that answers."""
    out = []
    for url in urls:
        try:
            status, body = fetch(url)
            if status == 200:
                out.append(int(body.strip()))
        except (NetworkError, ValueError):
            continue
    return out
