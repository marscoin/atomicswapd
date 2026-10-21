"""A small ElectrumX client for reading the swap order book.

One TLS connection per relay, JSON-RPC over newline-delimited JSON.
Subscribes to block headers so the tip arrives as soon as a block is
found. Only read calls are made; this client never posts or accepts
offers.
"""

import asyncio
import json
import logging
import ssl
import time
from typing import Any, Dict, Optional

from .config import Server

log = logging.getLogger(__name__)

CLIENT_NAME = 'atomicswapd'
PROTOCOL = '1.4'


class RelayError(Exception):
    pass


def header_time(header_hex: str) -> Optional[int]:
    """Timestamp of an 80-byte block header (bytes 68..72, little endian)."""
    try:
        raw = bytes.fromhex(header_hex)
        return int.from_bytes(raw[68:72], 'little') if len(raw) >= 80 else None
    except ValueError:
        return None


class Relay:
    """A connection to one ElectrumX relay, reconnecting with backoff."""

    def __init__(self, server: Server, timeout: float = 15):
        self.server = server
        self.timeout = timeout
        self._reader = None
        self._writer = None
        self._next_id = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self._read_task = None
        self._lock = asyncio.Lock()
        # Public status
        self.connected = False
        self.version: Optional[str] = None
        self.tip_height: Optional[int] = None
        self.tip_time: Optional[int] = None
        self.latency_ms: Optional[float] = None
        self.last_error: Optional[str] = None
        self.last_ok: Optional[float] = None
        self.abuse_controls: Optional[bool] = None  # relay has report_abandoned
        self.on_tip = None  # callback(relay)

    # ------------------------------------------------------------ connection

    def _ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if not self.server.tls_verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def connect(self):
        s = self.server
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(
                s.host, s.port, ssl=self._ssl_context(),
                server_hostname=s.host if s.tls_verify else None,
                limit=2 ** 22),
            self.timeout)
        self._read_task = asyncio.create_task(self._read_loop())
        version = await self.call('server.version', CLIENT_NAME, PROTOCOL)
        self.version = version[0] if isinstance(version, list) and version else str(version)
        header = await self.call('blockchain.headers.subscribe')
        self._set_tip(header)
        self.abuse_controls = await self._probe_abuse_controls()
        self.connected = True
        self.last_error = None

    async def _probe_abuse_controls(self) -> bool:
        # Relays with abuse controls know report_abandoned. For an offer
        # that doesn't exist it changes nothing and returns false.
        try:
            await self.call('atomicswap.report_abandoned', 'atomicswapd-probe')
            return True
        except RelayError:
            return False

    async def close(self):
        self.connected = False
        if self._read_task:
            self._read_task.cancel()
            self._read_task = None
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RelayError('connection closed'))
        self._pending.clear()

    async def run(self, stop: asyncio.Event):
        """Keep the connection up until ``stop`` is set."""
        delay = 2
        while not stop.is_set():
            try:
                await self.connect()
                delay = 2
                log.info('connected to %s (%s)', self.server.label, self.version)
                while not stop.is_set() and self._read_task and not self._read_task.done():
                    await asyncio.sleep(30)
                    await self.call('server.ping')
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = _describe(e)
                log.warning('relay %s: %s', self.server.label, self.last_error)
            await self.close()
            if stop.is_set():
                break
            try:
                await asyncio.wait_for(stop.wait(), delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, 120)

    # ------------------------------------------------------------ protocol

    async def _read_loop(self):
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    raise RelayError('server closed the connection')
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if 'id' in msg and msg['id'] in self._pending:
                    fut = self._pending.pop(msg['id'])
                    if fut.done():
                        continue
                    if msg.get('error'):
                        err = msg['error']
                        text = err.get('message') if isinstance(err, dict) else str(err)
                        fut.set_exception(RelayError(text))
                    else:
                        fut.set_result(msg.get('result'))
                elif msg.get('method') == 'blockchain.headers.subscribe':
                    params = msg.get('params') or []
                    if params:
                        self._set_tip(params[0])
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.last_error = _describe(e)
            self.connected = False
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(RelayError(self.last_error))
            self._pending.clear()

    async def call(self, method: str, *params: Any):
        if self._writer is None:
            raise RelayError('not connected')
        async with self._lock:
            self._next_id += 1
            msg_id = self._next_id
            fut = asyncio.get_running_loop().create_future()
            self._pending[msg_id] = fut
            line = json.dumps({'jsonrpc': '2.0', 'id': msg_id,
                               'method': method, 'params': list(params)}) + '\n'
            self._writer.write(line.encode())
            await self._writer.drain()
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(fut, self.timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise RelayError(f'{method} timed out')
        self.latency_ms = round((time.monotonic() - started) * 1000, 1)
        self.last_ok = time.time()
        return result

    def _set_tip(self, header):
        if not isinstance(header, dict) or 'height' not in header:
            return
        changed = header['height'] != self.tip_height
        self.tip_height = header['height']
        self.tip_time = header_time(header.get('hex', ''))
        if changed and self.on_tip:
            self.on_tip(self)

    # ------------------------------------------------------------ swap reads

    async def get_offers(self) -> list:
        result = await self.call('atomicswap.get_offers')
        return result if isinstance(result, list) else []

    async def get_acceptance(self, offer_id: str) -> Optional[dict]:
        result = await self.call('atomicswap.get_acceptance', offer_id)
        return result if isinstance(result, dict) else None

    def status(self) -> dict:
        return {
            'host': self.server.label,
            'tls_verified': self.server.tls_verify,
            'connected': self.connected,
            'version': self.version,
            'tip_height': self.tip_height,
            'latency_ms': self.latency_ms,
            'abuse_controls': self.abuse_controls,
            'last_error': None if self.connected else self.last_error,
        }


def _describe(e: Exception) -> str:
    if isinstance(e, asyncio.TimeoutError):
        return 'timed out'
    if isinstance(e, ssl.SSLCertVerificationError):
        return 'TLS certificate not trusted'
    if isinstance(e, ConnectionRefusedError):
        return 'connection refused'
    if isinstance(e, OSError) and e.strerror:
        return e.strerror
    return str(e) or type(e).__name__
