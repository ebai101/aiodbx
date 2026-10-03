from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from aiohttp.typedefs import Handler

from aiodbx import (
    DropboxAuthenticationError,
    DropboxError,
    DropboxOAuthError,
    DropboxProtocolError,
    oauth_exchange_code,
)
from aiodbx.hosts import EndpointHosts
from tests.helpers.http import make_app

APP_KEY = "app-key"
APP_SECRET = "app-secret"


@pytest.fixture
def token_hosts(aiohttp_server):
    @asynccontextmanager
    async def create(handler: Handler) -> AsyncIterator[EndpointHosts]:
        server = await aiohttp_server(make_app({"/oauth2/token": handler}))
        root = str(server.make_url("/")).rstrip("/")
        yield EndpointHosts(api=root, content=root, notify=root)

    return create


def _basic_auth(request: web.Request) -> tuple[str, str]:
    header = request.headers["Authorization"]
    assert header.startswith("Basic ")
    decoded = base64.b64decode(header[len("Basic ") :]).decode()
    key, _, secret = decoded.partition(":")
    return key, secret


async def test_exchange_sends_form_and_basic_auth(token_hosts) -> None:
    received: dict[str, object] = {}

    async def token(request: web.Request) -> web.Response:
        assert request.headers["Content-Type"].startswith(
            "application/x-www-form-urlencoded"
        )
        received["key"], received["secret"] = _basic_auth(request)
        form = await request.post()
        received["form"] = dict(form)
        return web.json_response(
            {
                "access_token": "access-1",
                "expires_in": 14400,
                "token_type": "bearer",
                "refresh_token": "refresh-original",
                "account_id": "dbid:abc",
                "scope": "files.content.read",
            }
        )

    async with token_hosts(token) as hosts:
        refresh_token = await oauth_exchange_code(
            "the-code",
            app_key=APP_KEY,
            app_secret=APP_SECRET,
            redirect_uri="https://example.com/cb",
            code_verifier="dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk",
            _hosts=hosts,
        )

    assert refresh_token == "refresh-original"
    assert (received["key"], received["secret"]) == (APP_KEY, APP_SECRET)
    assert received["form"] == {
        "grant_type": "authorization_code",
        "code": "the-code",
        "redirect_uri": "https://example.com/cb",
        "code_verifier": "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk",
    }


async def test_exchange_omits_optional_fields(token_hosts) -> None:
    form_seen: dict[str, object] = {}

    async def token(request: web.Request) -> web.Response:
        form_seen.update(dict(await request.post()))
        return web.json_response(
            {"access_token": "a", "expires_in": 100, "token_type": "bearer"}
        )

    async with token_hosts(token) as hosts:
        with pytest.raises(DropboxProtocolError):
            await oauth_exchange_code(
                "code", app_key=APP_KEY, app_secret=APP_SECRET, _hosts=hosts
            )

    assert "redirect_uri" not in form_seen
    assert "code_verifier" not in form_seen


async def test_exchange_error_is_typed_not_retried_and_keeps_secrets_out(
    token_hosts,
) -> None:
    secret = "super-secret-value"
    calls = 0

    async def token(request: web.Request) -> web.Response:
        nonlocal calls
        calls += 1
        return web.json_response(
            {
                "error": "invalid_grant",
                "error_description": f"the {secret} and the-code are bad",
            },
            status=400,
        )

    async with token_hosts(token) as hosts:
        with pytest.raises(DropboxOAuthError) as caught:
            await oauth_exchange_code(
                "the-code",
                app_key=APP_KEY,
                app_secret=secret,
                _hosts=hosts,
            )

    error = caught.value
    assert isinstance(error, DropboxAuthenticationError)
    assert error.status_code == 400
    assert error.error_tag == "invalid_grant"
    assert error.response_body is None
    assert secret not in str(error)
    assert secret not in repr(error)
    assert secret not in repr(error.diagnostic_details())
    assert calls == 1


async def test_exchange_unknown_error_code_is_sanitized(token_hosts) -> None:
    async def token(request: web.Request) -> web.Response:
        return web.json_response({"error": "mystery code with spaces"}, status=400)

    async with token_hosts(token) as hosts:
        with pytest.raises(DropboxOAuthError) as caught:
            await oauth_exchange_code(
                "code", app_key=APP_KEY, app_secret=APP_SECRET, _hosts=hosts
            )

    assert caught.value.error_tag == "unknown_oauth_error"
    assert "mystery" not in str(caught.value)


async def test_exchange_does_not_retry_server_error(token_hosts) -> None:
    calls = 0

    async def token(request: web.Request) -> web.Response:
        nonlocal calls
        calls += 1
        return web.Response(status=503, text="busy")

    async with token_hosts(token) as hosts:
        with pytest.raises(DropboxError):
            await oauth_exchange_code(
                "code", app_key=APP_KEY, app_secret=APP_SECRET, _hosts=hosts
            )

    assert calls == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"expires_in": 100, "token_type": "bearer"},
        {"access_token": "", "expires_in": 100, "token_type": "bearer"},
        {"access_token": "a", "expires_in": True, "token_type": "bearer"},
        {"access_token": "a", "expires_in": 1.5, "token_type": "bearer"},
        {"access_token": "a", "expires_in": 0, "token_type": "bearer"},
        {"access_token": "a", "expires_in": 100, "token_type": "mac"},
        {"access_token": "a", "expires_in": 100},
    ],
)
async def test_exchange_rejects_malformed_success(token_hosts, payload) -> None:
    async def token(request: web.Request) -> web.Response:
        return web.json_response(payload)

    async with token_hosts(token) as hosts:
        with pytest.raises(DropboxProtocolError) as caught:
            await oauth_exchange_code(
                "code", app_key=APP_KEY, app_secret=APP_SECRET, _hosts=hosts
            )

    assert caught.value.response_body is None


async def test_concurrent_exchanges_do_not_cross_state(aiohttp_server) -> None:
    seen: list[str] = []

    async def token(request: web.Request) -> web.Response:
        form = await request.post()
        seen.append(str(form["code"]))
        return web.json_response(
            {
                "access_token": "access-1",
                "expires_in": 14400,
                "token_type": "bearer",
                "refresh_token": f"refresh-{form['code']}",
            }
        )

    server = await aiohttp_server(make_app({"/oauth2/token": token}))
    root = str(server.make_url("/")).rstrip("/")
    hosts = EndpointHosts(api=root, content=root, notify=root)

    first, second = await asyncio.gather(
        oauth_exchange_code(
            "code-a", app_key=APP_KEY, app_secret=APP_SECRET, _hosts=hosts
        ),
        oauth_exchange_code(
            "code-b", app_key=APP_KEY, app_secret=APP_SECRET, _hosts=hosts
        ),
    )

    assert set(seen) == {"code-a", "code-b"}
    assert {first, second} == {"refresh-code-a", "refresh-code-b"}
