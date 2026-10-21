"""Read-only HTTP API, live stream and the dashboard.

Nothing here can change the maker: there are no write endpoints, and
the snapshot is built from an allow-list of public fields.
"""

import asyncio
import json
import logging
import os
from pathlib import Path

from aiohttp import web

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / 'web'

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline' "
       "https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


class SnapshotCache:
    """Build the snapshot once per change, however many clients watch."""

    def __init__(self, service):
        self.service = service
        self._rev = None
        self._body = None

    def body(self) -> bytes:
        if self._rev != self.service.revision or self._body is None:
            self._body = json.dumps(self.service.snapshot(), separators=(',', ':')).encode()
            self._rev = self.service.revision
        return self._body


@web.middleware
async def security_headers(request, handler):
    resp = await handler(request)
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('Referrer-Policy', 'no-referrer')
    resp.headers.setdefault('Content-Security-Policy', CSP)
    if request.path.startswith('/api/'):
        resp.headers.setdefault('Access-Control-Allow-Origin', '*')
    return resp


def make_app(service, max_stream_clients: int = 200) -> web.Application:
    cache = SnapshotCache(service)
    clients = set()
    app = web.Application(middlewares=[security_headers])

    async def snapshot(request):
        return web.Response(body=cache.body(), content_type='application/json',
                            headers={'Cache-Control': 'no-store'})

    async def history(request):
        return web.json_response(service.history, headers={'Cache-Control': 'max-age=300'})

    async def healthz(request):
        return web.json_response({'ok': True, 'revision': service.revision})

    async def stream(request):
        if len(clients) >= max_stream_clients:
            raise web.HTTPServiceUnavailable(text='too many live viewers; try again soon')
        resp = web.StreamResponse(headers={
            'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store',
            'X-Accel-Buffering': 'no'})
        await resp.prepare(request)
        clients.add(resp)
        last, idle = None, 0
        try:
            while True:
                if service.revision != last:
                    last, idle = service.revision, 0
                    await resp.write(b'event: snapshot\ndata: ' + cache.body() + b'\n\n')
                elif idle >= 15:
                    idle = 0
                    await resp.write(b': keepalive\n\n')
                await asyncio.sleep(1)
                idle += 1
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            clients.discard(resp)
        return resp

    async def index(request):
        page = WEB_DIR / 'index.html'
        if not page.exists():
            return web.Response(text='atomicswapd is running. The dashboard was not built; '
                                     'see /api/snapshot.', content_type='text/plain')
        return web.FileResponse(page, headers={'Cache-Control': 'no-cache'})

    app.router.add_get('/api/snapshot', snapshot)
    app.router.add_get('/api/history', history)
    app.router.add_get('/api/stream', stream)
    app.router.add_get('/healthz', healthz)
    app.router.add_get('/', index)
    if (WEB_DIR / 'assets').is_dir():
        app.router.add_static('/assets/', WEB_DIR / 'assets', append_version=False)
    for name in ('favicon.svg', 'og.png'):
        if (WEB_DIR / name).exists():
            app.router.add_get(f'/{name}', lambda r, n=name: web.FileResponse(WEB_DIR / n))
    return app


async def serve(service, host: str, port: int, max_stream_clients: int):
    runner = web.AppRunner(make_app(service, max_stream_clients), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info('dashboard on http://%s:%d', host, port)
    return runner
