"""HTTP API, live stream and the dashboard.

On a public server the API is read-only: there are no write endpoints,
and the snapshot is built from an allow-list of public fields.

In desktop mode (the Mars Swap app) the engine listens on 127.0.0.1 only,
and the API belongs to the app window alone: the window trades a
per-launch token for a strict same-site session cookie, every API call
needs that session, and no CORS headers are sent, so no web page in a
browser can read from or write to it.
"""

import asyncio
import hmac
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Optional

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


SESSION_COOKIE = 'swap_session'
TOKEN_HEADER = 'X-Swap-Token'
LOCAL_HOSTS = ('127.0.0.1', 'localhost')


class DesktopAuth:
    """Desktop mode: only the app window that knows the token gets in."""

    def __init__(self, token: str):
        if len(token) < 32:
            raise ValueError('the session token must be at least 32 characters')
        self.token = token
        self.sessions: set = set()

    def token_ok(self, request) -> bool:
        given = request.headers.get(TOKEN_HEADER, '')
        return bool(given) and hmac.compare_digest(given, self.token)

    def session_ok(self, request) -> bool:
        return request.cookies.get(SESSION_COOKIE, '') in self.sessions

    def new_session(self) -> str:
        sid = secrets.token_hex(32)
        self.sessions.add(sid)
        return sid


def desktop_guard(auth: DesktopAuth):
    @web.middleware
    async def guard(request, handler):
        # Only this machine, by name: stops DNS-rebinding pages.
        if (request.host or '').rsplit(':', 1)[0] not in LOCAL_HOSTS:
            raise web.HTTPForbidden(text='local access only')
        if request.method == 'OPTIONS':          # no CORS, ever
            raise web.HTTPForbidden()
        if request.path.startswith('/api/') and request.path != '/api/session':
            if not auth.session_ok(request):
                raise web.HTTPUnauthorized(text='no session')
            if request.method not in ('GET', 'HEAD') and not auth.token_ok(request):
                raise web.HTTPForbidden(text='token required')
        return await handler(request)
    return guard


def security_headers(desktop: bool):
    @web.middleware
    async def headers(request, handler):
        resp = await handler(request)
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('Referrer-Policy', 'no-referrer')
        resp.headers.setdefault('Content-Security-Policy', CSP)
        if request.path.startswith('/api/') and not desktop:
            resp.headers.setdefault('Access-Control-Allow-Origin', '*')
        return resp
    return headers


def make_app(service, max_stream_clients: int = 200,
             desktop_token: Optional[str] = None) -> web.Application:
    cache = SnapshotCache(service)
    clients = set()
    auth = DesktopAuth(desktop_token) if desktop_token else None
    middlewares = [security_headers(desktop=auth is not None)]
    if auth:
        middlewares.insert(0, desktop_guard(auth))
    app = web.Application(middlewares=middlewares)

    async def snapshot(request):
        return web.Response(body=cache.body(), content_type='application/json',
                            headers={'Cache-Control': 'no-store'})

    async def history(request):
        # Never let a browser keep an empty answer from right after startup.
        ready = bool(service.history.get('mars_btc'))
        return web.json_response(service.history, headers={
            'Cache-Control': 'max-age=300' if ready else 'no-store'})

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

    async def session(request):
        if not auth.token_ok(request):
            raise web.HTTPForbidden(text='token required')
        resp = web.json_response({'ok': True})
        resp.set_cookie(SESSION_COOKIE, auth.new_session(), httponly=True,
                        samesite='Strict', path='/')
        return resp

    if auth:
        app.router.add_post('/api/session', session)
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


async def serve(service, host: str, port: int, max_stream_clients: int,
                desktop_token: Optional[str] = None):
    """Start the HTTP server; returns (runner, actual port). Port 0 picks a free one."""
    runner = web.AppRunner(make_app(service, max_stream_clients, desktop_token), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    port = runner.addresses[0][1]
    log.info('dashboard on http://%s:%d', host, port)
    return runner, port
