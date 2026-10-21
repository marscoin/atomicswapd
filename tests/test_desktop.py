import asyncio

from aiohttp.test_utils import TestClient, TestServer

from atomicswapd.api import SESSION_COOKIE, TOKEN_HEADER, make_app

TOKEN = 'a' * 64


class FakeService:
    revision = 1
    history = {'mars_btc': [], 'mars_usd': []}

    def snapshot(self):
        return {'revision': 1, 'maker': {'balance': {'deposit_address': 'mars1qsecret'}}}


def run(test):
    async def go():
        async with TestClient(TestServer(make_app(FakeService(), desktop_token=TOKEN))) as client:
            await test(client)
    asyncio.run(go())


async def session_cookie(client):
    r = await client.post('/api/session', headers={TOKEN_HEADER: TOKEN})
    assert r.status == 200
    return {'Cookie': f'{SESSION_COOKIE}={r.cookies[SESSION_COOKIE].value}'}


def test_api_needs_a_session():
    async def t(client):
        for path in ('/api/snapshot', '/api/stream', '/api/history'):
            assert (await client.get(path)).status == 401
        assert (await client.get('/healthz')).status == 200     # liveness only
    run(t)


def test_session_needs_the_token():
    async def t(client):
        assert (await client.post('/api/session')).status == 403
        assert (await client.post('/api/session', headers={TOKEN_HEADER: 'b' * 64})).status == 403
        r = await client.post('/api/session', headers={TOKEN_HEADER: TOKEN})
        cookie = r.cookies[SESSION_COOKIE]
        assert r.status == 200 and cookie['httponly'] and cookie['samesite'] == 'Strict'
    run(t)


def test_session_unlocks_reads_without_cors():
    async def t(client):
        headers = await session_cookie(client)
        r = await client.get('/api/snapshot', headers=headers)
        assert r.status == 200 and 'mars1qsecret' in await r.text()
        assert 'Access-Control-Allow-Origin' not in r.headers
        client.session.cookie_jar.clear()        # send only the forged cookie
        assert (await client.get('/api/snapshot', headers={'Cookie': f'{SESSION_COOKIE}=forged'})).status == 401
    run(t)


def test_no_preflight_and_no_foreign_hosts():
    async def t(client):
        assert (await client.options('/api/snapshot')).status == 403
        headers = await session_cookie(client)
        headers['Host'] = 'attacker.example:8080'          # DNS rebinding
        assert (await client.get('/api/snapshot', headers=headers)).status == 403
    run(t)


def test_writes_need_the_token_too():
    async def t(client):
        headers = await session_cookie(client)
        # No write endpoints exist yet; the guard answers before routing.
        assert (await client.post('/api/anything', headers=headers)).status == 403
        headers[TOKEN_HEADER] = TOKEN
        assert (await client.post('/api/anything', headers=headers)).status == 404   # past the guard
    run(t)


def test_short_tokens_are_refused():
    import pytest
    with pytest.raises(ValueError):
        make_app(FakeService(), desktop_token='short')
