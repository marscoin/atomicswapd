"""Configuration: a TOML file plus a few environment overrides.

Modes:
  watch  The wallet, balance, order book and offer plan are all real, but
         nothing is posted or locked. Use it to check a new setup.
  live   Posts offers, locks MARS for accepted offers, claims BTC.

Secrets never live here. The wallet password comes from the
ATOMICSWAPD_WALLET_PASSWORD environment variable.
"""

import os
import tomllib
from dataclasses import dataclass, field
from typing import List, Optional

MODES = ('watch', 'live')


class ConfigError(ValueError):
    pass


@dataclass
class Server:
    host: str
    port: int = 50002
    # False only for relays with self-signed certificates. The dashboard
    # labels those as unverified.
    tls_verify: bool = True

    @property
    def label(self) -> str:
        return self.host if self.port == 50002 else f'{self.host}:{self.port}'


DEFAULT_SERVERS = [
    Server('electrum.marscoin.org'),
    Server('147.182.177.23', tls_verify=False),
    Server('164.90.138.207', tls_verify=False),
]


@dataclass
class Maker:
    # Where BTC from completed swaps goes. Required for live mode.
    btc_receive_address: str = ''
    # Pricing and sizing, as in the Electrum-Mars Auto-Maker
    fee_percent: float = 5.0          # minimum spread over the market rate
    num_offers: int = 3               # tiers, sized 1:2:3...
    min_offer_mars: float = 10
    max_offer_mars: float = 1000
    daily_limit_mars: float = 10000
    reserve_percent: float = 20       # never offered
    offer_minutes: float = 15         # offers expire unless refreshed
    refresh_seconds: int = 300
    # Exposure: MARS locked in pending swaps plus MARS on offer never
    # exceeds this share of inventory.
    inventory_cap_percent: float = 30
    # Swaps in progress allowed per buyer (per relay client tag)
    max_pending_per_taker: int = 1


@dataclass
class Config:
    mode: str = 'watch'
    data_dir: str = '/data'
    wallet_name: str = 'maker'
    servers: List[Server] = field(default_factory=lambda: list(DEFAULT_SERVERS))
    maker: Maker = field(default_factory=Maker)
    poll_seconds: float = 10
    market_seconds: float = 60
    # Prices older than this are not used to plan offers.
    max_price_age_seconds: float = 600
    api_host: str = '0.0.0.0'
    api_port: int = 8080
    max_stream_clients: int = 200

    @property
    def electrum_dir(self) -> str:
        return os.path.join(self.data_dir, 'electrum')

    @property
    def wallet_path(self) -> str:
        return os.path.join(self.electrum_dir, 'wallets', self.wallet_name)

    @property
    def swaps_dir(self) -> str:
        return os.path.join(self.electrum_dir, 'atomic_swaps')

    def validate(self):
        if self.mode not in MODES:
            raise ConfigError(f'mode must be one of {MODES}, not {self.mode!r}')
        if not self.servers:
            raise ConfigError('at least one server is required')
        m = self.maker
        if self.mode == 'live' and not m.btc_receive_address:
            raise ConfigError('live mode needs maker.btc_receive_address '
                              '(or ATOMICSWAPD_BTC_ADDRESS)')
        if not 0 < m.inventory_cap_percent <= 100:
            raise ConfigError('maker.inventory_cap_percent must be in (0, 100]')
        if not 0 <= m.reserve_percent < 100:
            raise ConfigError('maker.reserve_percent must be in [0, 100)')
        if m.fee_percent < 0:
            raise ConfigError('maker.fee_percent must not be negative')
        if m.num_offers < 1:
            raise ConfigError('maker.num_offers must be at least 1')
        if not 0 < m.min_offer_mars <= m.max_offer_mars:
            raise ConfigError('need 0 < maker.min_offer_mars <= maker.max_offer_mars')
        if not 1 <= m.offer_minutes <= 60:
            raise ConfigError('maker.offer_minutes must be between 1 and 60')
        if m.refresh_seconds < 60 or m.refresh_seconds >= m.offer_minutes * 60:
            raise ConfigError('maker.refresh_seconds must be >= 60 and shorter '
                              'than the offer lifetime')
        if m.max_pending_per_taker < 1:
            raise ConfigError('maker.max_pending_per_taker must be at least 1')
        if self.poll_seconds < 2 or self.market_seconds < 10:
            raise ConfigError('poll_seconds >= 2 and market_seconds >= 10, please')
        return self


def _build(cls, data: dict, where: str):
    known = set(cls.__dataclass_fields__)
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f'unknown setting(s) in {where}: {", ".join(sorted(unknown))}')
    return cls(**data)


def from_dict(data: dict) -> Config:
    data = dict(data)
    servers = data.pop('servers', None)
    maker = data.pop('maker', None)
    cfg = _build(Config, data, 'the top level')
    if servers is not None:
        cfg.servers = [_build(Server, s, '[[servers]]') for s in servers]
    if maker is not None:
        cfg.maker = _build(Maker, maker, '[maker]')
    return cfg


def load(path: Optional[str] = None, env=os.environ) -> Config:
    path = path or env.get('ATOMICSWAPD_CONFIG')
    data = {}
    if path:
        with open(path, 'rb') as f:
            data = tomllib.load(f)
    cfg = from_dict(data)
    if env.get('ATOMICSWAPD_MODE'):
        cfg.mode = env['ATOMICSWAPD_MODE']
    if env.get('ATOMICSWAPD_DATA'):
        cfg.data_dir = env['ATOMICSWAPD_DATA']
    if env.get('ATOMICSWAPD_BTC_ADDRESS'):
        cfg.maker.btc_receive_address = env['ATOMICSWAPD_BTC_ADDRESS'].strip()
    if env.get('ATOMICSWAPD_FEE_PERCENT'):
        try:
            cfg.maker.fee_percent = float(env['ATOMICSWAPD_FEE_PERCENT'])
        except ValueError:
            raise ConfigError('ATOMICSWAPD_FEE_PERCENT must be a number, e.g. 8')
    if env.get('PORT'):
        cfg.api_port = int(env['PORT'])
    return cfg.validate()
