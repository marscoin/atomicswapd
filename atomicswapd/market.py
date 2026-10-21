"""Market data: MARS and BTC prices, the Bitcoin tip and fee rates.

Every value carries the time it was fetched. There are no fallback
prices: a stale or missing price means no offers, never a guessed one.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, asdict
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)

MARS_PRICE_URL = 'https://price.marscoin.org/json/'
BTC_PRICE_URL = 'https://mempool.space/api/v1/prices'
BTC_BLOCKS_URL = 'https://mempool.space/api/v1/blocks'
BTC_FEES_URL = 'https://mempool.space/api/v1/fees/recommended'
MARS_CMC_ID = '154'


@dataclass
class Market:
    mars_usd: Optional[float] = None
    btc_usd: Optional[float] = None
    prices_at: Optional[float] = None
    btc_height: Optional[int] = None
    btc_tip_time: Optional[int] = None
    btc_at: Optional[float] = None
    btc_fee_fast: Optional[float] = None    # sat/vB
    btc_fee_hour: Optional[float] = None
    error: Optional[str] = None

    @property
    def mars_btc(self) -> Optional[float]:
        if self.mars_usd and self.btc_usd:
            return self.mars_usd / self.btc_usd
        return None

    def price_age(self, now: Optional[float] = None) -> Optional[float]:
        if self.prices_at is None:
            return None
        return (now or time.time()) - self.prices_at

    def to_dict(self) -> dict:
        d = asdict(self)
        d['mars_btc'] = self.mars_btc
        d['sats_per_mars'] = round(self.mars_btc * 1e8, 2) if self.mars_btc else None
        return d


def parse_mars_usd(data: dict) -> float:
    price = data['data'][MARS_CMC_ID]['quote']['USD']['price']
    price = float(price)
    if not price > 0:
        raise ValueError('MARS price is not positive')
    return price


def parse_btc_usd(data: dict) -> float:
    price = float(data['USD'])
    if not price > 0:
        raise ValueError('BTC price is not positive')
    return price


class MarketFeed:

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.market = Market()
        self.on_btc_block = None  # callback(height)

    async def _json(self, url: str):
        async with self.session.get(url) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def refresh_prices(self):
        mars_usd = parse_mars_usd(await self._json(MARS_PRICE_URL))
        btc_usd = parse_btc_usd(await self._json(BTC_PRICE_URL))
        m = self.market
        m.mars_usd, m.btc_usd, m.prices_at = mars_usd, btc_usd, time.time()

    async def refresh_bitcoin(self):
        blocks = await self._json(BTC_BLOCKS_URL)
        tip = blocks[0]
        m = self.market
        new_block = m.btc_height is not None and tip['height'] > m.btc_height
        m.btc_height, m.btc_tip_time, m.btc_at = tip['height'], tip['timestamp'], time.time()
        try:
            fees = await self._json(BTC_FEES_URL)
            m.btc_fee_fast, m.btc_fee_hour = fees.get('fastestFee'), fees.get('hourFee')
        except Exception as e:
            log.debug('fee fetch failed: %s', e)
        if new_block and self.on_btc_block:
            self.on_btc_block(tip['height'])

    async def run(self, stop: asyncio.Event, price_every: float, btc_every: float = 20):
        next_price = 0.0
        while not stop.is_set():
            errors = []
            if time.monotonic() >= next_price:
                try:
                    await self.refresh_prices()
                    next_price = time.monotonic() + price_every
                except Exception as e:
                    errors.append(f'prices: {e}')
                    next_price = time.monotonic() + 15
            try:
                await self.refresh_bitcoin()
            except Exception as e:
                errors.append(f'bitcoin: {e}')
            self.market.error = '; '.join(errors) or None
            if errors:
                log.warning('market feed: %s', self.market.error)
            try:
                await asyncio.wait_for(stop.wait(), btc_every)
            except asyncio.TimeoutError:
                pass
