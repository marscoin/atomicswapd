"""HTTP routes for AI agents: /api/v1 and the discovery documents.

GET only. Responses are cacheable for a few seconds, readable from any
origin, and every failure under /api/v1 has the same JSON shape.
"""

import json
import logging
import re
import time

from aiohttp import web

from . import agentapi, agentdocs, agentprotocol

log = logging.getLogger(__name__)

# Documents for agents that live outside /api/.
DOC_PATHS = ('/llms.txt', '/openapi.json', '/.well-known/agent.json')

_HOST = re.compile(r'^([A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(:\d{1,5})?$')


def _json(data, *, status: int = 200, max_age: int = 0) -> web.Response:
    return web.Response(
        body=json.dumps(data, separators=(',', ':')).encode(), status=status,
        content_type='application/json',
        headers={'Cache-Control': f'public, max-age={max_age}' if max_age else 'no-store'})


def _error(e: agentapi.ApiError) -> web.Response:
    return _json(e.body(), status=e.status)


@web.middleware
async def v1_errors(request, handler):
    """Under /api/v1 every failure is {"error": {"code", "message"}}."""
    if not request.path.startswith('/api/v1'):
        return await handler(request)
    try:
        return await handler(request)
    except agentapi.ApiError as e:
        return _error(e)
    except web.HTTPMethodNotAllowed:
        return _error(agentapi.ApiError(405, 'method_not_allowed',
                                        'this API is read-only: use GET'))
    except web.HTTPNotFound:
        return _error(agentapi.ApiError(404, 'not_found', 'no such endpoint; see /openapi.json'))
    except web.HTTPException as e:
        return _error(agentapi.ApiError(e.status, 'http_error', e.reason or 'request failed'))
    except Exception:
        log.exception('agent API: %s', request.path)
        return _error(agentapi.ApiError(500, 'internal_error', 'the server could not answer'))


def public_base_url(request, configured: str = '') -> str:
    """Where clients reach this node: the configured address, else the
    address this request came in on (possibly behind a TLS-terminating proxy)."""
    if configured:
        return configured.rstrip('/')
    scheme = request.headers.get('X-Forwarded-Proto', request.scheme).split(',')[0].strip()
    if scheme not in ('http', 'https'):
        scheme = 'https'
    host = request.host or ''
    return f'{scheme}://{host if _HOST.match(host) else "localhost"}'


class AgentState:
    """The state behind /api/v1, read once per change like the snapshot."""

    def __init__(self, service):
        self.service = service
        self._rev = None
        self._state = None

    def get(self) -> dict:
        if self._rev != self.service.revision or self._state is None:
            self._state = self.service.agent_state()
            self._rev = self.service.revision
        return self._state


def add_agent_routes(app: web.Application, service):
    state = AgentState(service)

    def view(build, max_age=agentdocs.V1_MAX_AGE):
        async def handler(request):
            return _json(build(state.get(), time.time(), request), max_age=max_age)
        return handler

    def document(build, content_type='application/json'):
        async def handler(request):
            st = state.get()
            base = public_base_url(request, st.get('public_url') or '')
            headers = {'Cache-Control': f'public, max-age={agentdocs.DOCS_MAX_AGE}'}
            if not st.get('public_url'):
                headers['Vary'] = 'Host, X-Forwarded-Proto'
            body = build(base, st)
            if content_type == 'application/json':
                body = json.dumps(body, separators=(',', ':'))
            return web.Response(text=body, content_type=content_type, charset='utf-8',
                                headers=headers)
        return handler

    get = app.router.add_get
    get('/api/v1/market', view(lambda st, now, r: agentapi.market_view(st, now)))
    get('/api/v1/offers', view(lambda st, now, r: agentapi.offers_view(st, now, r.query)))
    get('/api/v1/offers/{id}',
        view(lambda st, now, r: agentapi.offer_detail(st, now, r.match_info['id'])))
    get('/api/v1/quote', view(lambda st, now, r: agentapi.quote_view(st, now, r.query)))
    get('/api/v1/swaps/recent',
        view(lambda st, now, r: agentapi.recent_swaps_view(st, now, r.query)))
    get('/api/v1/protocol', view(lambda st, now, r: agentprotocol.protocol_view(st, now),
                                 max_age=agentdocs.PROTOCOL_MAX_AGE))
    agent_json = document(agentdocs.agent_description)
    get('/.well-known/agent.json', agent_json)
    get('/api/agent', agent_json)
    get('/openapi.json', document(lambda base, st: agentdocs.openapi(base)))
    get('/api/tools', document(lambda base, st: agentdocs.tools(base)))
    get('/llms.txt', document(agentdocs.llms_txt, content_type='text/plain'))
