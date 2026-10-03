from __future__ import annotations

import asyncio
from typing import cast

import pytest
from aiohttp import web

import aiodbx.oauth as oauth_module
from aiodbx import (
    AsyncDropbox,
    DropboxOAuthError,
    DropboxProtocolError,
    RetryPolicy,
)
from aiodbx.hosts import EndpointHosts
from aiodbx.oauth import _credential_from_arguments
from tests.helpers.http import make_app

APP_KEY = "app-key"
APP_SECRET = "app-secret"
REFRESH = "refresh-original"


def _token_response(access_token: str, expires_in: int = 14400) -> web.Response:
    return web.json_response(
        {
            "access_token": access_token,
            "expires_in": expires_in,
            "token_type": "bearer",
        }
    )


def test_rejects_incomplete_oauth_credentials() -> None:
    with pytest.raises(ValueError, match="app_key"):
        _credential_from_arguments(
            None, app_key="k", app_secret="s", refresh_token=None
        )


def test_rejects_mixed_credentials() -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        _credential_from_arguments(
            "token", app_key="k", app_secret="s", refresh_token="r"
        )


def test_rejects_empty_oauth_field() -> None:
    with pytest.raises(ValueError, match="app_key"):
        _credential_from_arguments(None, app_key="", app_secret="s", refresh_token="r")


def test_rejects_non_string_access_token() -> None:
    with pytest.raises(ValueError, match="access_token"):
        _credential_from_arguments(
            cast(str, 123),
            app_key=None,
            app_secret=None,
            refresh_token=None,
        )


async def test_first_request_mints_lazily_and_reuses(client_factory) -> None:
    grants: list[dict[str, object]] = []
    seen: list[str] = []

    async def token(request: web.Request) -> web.Response:
        grants.append(dict(await request.post()))
        return _token_response("access-1")

    async def account(request: web.Request) -> web.Response:
        seen.append(request.headers["Authorization"])
        return web.json_response({"account_id": "dbid:x"})

    async with client_factory(
        {"/oauth2/token": token, "/2/users/get_current_account": account},
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        refresh_token=REFRESH,
        content_host=False,
    ) as dbx:
        assert grants == []
        await dbx.users_get_current_account()
        await dbx.users_get_current_account()

    assert grants == [{"grant_type": "refresh_token", "refresh_token": REFRESH}]
    assert seen == ["Bearer access-1", "Bearer access-1"]


async def test_concurrent_first_calls_mint_one_token(client_factory) -> None:
    count = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def token(request: web.Request) -> web.Response:
        nonlocal count
        count += 1
        started.set()
        await release.wait()
        return _token_response("access-1")

    async def account(request: web.Request) -> web.Response:
        return web.json_response({"account_id": "dbid:x"})

    async with client_factory(
        {"/oauth2/token": token, "/2/users/get_current_account": account},
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        refresh_token=REFRESH,
        content_host=False,
    ) as dbx:
        tasks = [
            asyncio.create_task(dbx.users_get_current_account()) for _ in range(20)
        ]
        await started.wait()
        release.set()
        results = await asyncio.gather(*tasks)

    assert count == 1
    assert len(results) == 20


async def test_proactive_refresh_at_deadline(
    client_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [0.0]
    monkeypatch.setattr(oauth_module, "_now", lambda: clock[0])

    count = 0
    seen: list[str] = []

    async def token(request: web.Request) -> web.Response:
        nonlocal count
        count += 1
        return _token_response(f"access-{count}", expires_in=100)

    async def account(request: web.Request) -> web.Response:
        seen.append(request.headers["Authorization"])
        return web.json_response({"account_id": "dbid:x"})

    async with client_factory(
        {"/oauth2/token": token, "/2/users/get_current_account": account},
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        refresh_token=REFRESH,
        content_host=False,
    ) as dbx:
        await dbx.users_get_current_account()
        clock[0] = 89.0
        await dbx.users_get_current_account()
        clock[0] = 90.0
        await dbx.users_get_current_account()

    assert count == 2
    assert seen == ["Bearer access-1", "Bearer access-1", "Bearer access-2"]


async def test_rejects_lease_that_is_already_stale(
    client_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [0.0]
    monkeypatch.setattr(oauth_module, "_now", lambda: clock[0])

    started = asyncio.Event()
    release = asyncio.Event()
    account_calls = 0

    async def token(request: web.Request) -> web.Response:
        started.set()
        await release.wait()
        return _token_response("access-1", expires_in=1)

    async def account(request: web.Request) -> web.Response:
        nonlocal account_calls
        account_calls += 1
        return web.json_response({})

    async with client_factory(
        {"/oauth2/token": token, "/2/users/get_current_account": account},
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        refresh_token=REFRESH,
        content_host=False,
    ) as dbx:
        task = asyncio.create_task(dbx.users_get_current_account())
        await started.wait()
        clock[0] = 5.0
        release.set()
        with pytest.raises(DropboxProtocolError):
            await task

    assert account_calls == 0


async def test_refresh_retries_transient_server_error(client_factory) -> None:
    count = 0

    async def token(request: web.Request) -> web.Response:
        nonlocal count
        count += 1
        if count == 1:
            return web.Response(status=503, text="busy")
        return _token_response("access-1")

    async def account(request: web.Request) -> web.Response:
        return web.json_response({"account_id": "dbid:x"})

    async with client_factory(
        {"/oauth2/token": token, "/2/users/get_current_account": account},
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        refresh_token=REFRESH,
        content_host=False,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0),
    ) as dbx:
        account_result = await dbx.users_get_current_account()

    assert account_result == {"account_id": "dbid:x"}
    assert count == 2


async def test_shared_failed_refresh_reports_to_all_waiters(client_factory) -> None:
    count = 0

    async def token(request: web.Request) -> web.Response:
        nonlocal count
        count += 1
        await asyncio.sleep(0)
        return web.json_response({"error": "invalid_grant"}, status=400)

    async def account(request: web.Request) -> web.Response:
        return web.json_response({})

    async with client_factory(
        {"/oauth2/token": token, "/2/users/get_current_account": account},
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        refresh_token=REFRESH,
        content_host=False,
    ) as dbx:
        results = await asyncio.gather(
            dbx.users_get_current_account(),
            dbx.users_get_current_account(),
            return_exceptions=True,
        )

    assert count == 1
    assert all(isinstance(result, DropboxOAuthError) for result in results)


async def test_aclose_reports_closed_to_inflight_refresh(client_factory) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def token(request: web.Request) -> web.Response:
        started.set()
        await release.wait()
        return _token_response("access-1")

    async def account(request: web.Request) -> web.Response:
        return web.json_response({})

    async with client_factory(
        {"/oauth2/token": token, "/2/users/get_current_account": account},
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        refresh_token=REFRESH,
        content_host=False,
    ) as dbx:
        task = asyncio.create_task(dbx.users_get_current_account())
        await started.wait()
        await dbx.aclose()
        with pytest.raises(RuntimeError, match="closed"):
            await task


async def test_cancelling_a_waiter_is_not_swallowed(client_factory) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def token(request: web.Request) -> web.Response:
        started.set()
        await release.wait()
        return _token_response("access-1")

    async def account(request: web.Request) -> web.Response:
        return web.json_response({})

    async with client_factory(
        {"/oauth2/token": token, "/2/users/get_current_account": account},
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        refresh_token=REFRESH,
        content_host=False,
    ) as dbx:
        task = asyncio.create_task(dbx.users_get_current_account())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()


async def test_restart_mints_a_fresh_token(client_factory) -> None:
    tokens: list[str] = []

    async def token(request: web.Request) -> web.Response:
        tokens.append("call")
        return _token_response(f"access-{len(tokens)}")

    async def account(request: web.Request) -> web.Response:
        return web.json_response({"token": request.headers["Authorization"]})

    async with client_factory(
        {"/oauth2/token": token, "/2/users/get_current_account": account},
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        refresh_token=REFRESH,
        content_host=False,
    ) as dbx:
        first = await dbx.users_get_current_account()
        await dbx.aclose()
        await dbx.start()
        second = await dbx.users_get_current_account()

    assert len(tokens) == 2
    assert first["token"] == "Bearer access-1"
    assert second["token"] == "Bearer access-2"


async def test_clients_do_not_share_token_state(aiohttp_server) -> None:
    count = 0

    async def token(request: web.Request) -> web.Response:
        nonlocal count
        count += 1
        return _token_response(f"access-{count}")

    async def account(request: web.Request) -> web.Response:
        return web.json_response({})

    server = await aiohttp_server(
        make_app(
            {
                "/oauth2/token": token,
                "/2/users/get_current_account": account,
            }
        )
    )
    root = str(server.make_url("/")).rstrip("/")
    hosts = EndpointHosts(api=root, content=root, notify=root)

    first = AsyncDropbox(
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        refresh_token=REFRESH,
        _hosts=hosts,
    )
    second = AsyncDropbox(
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        refresh_token=REFRESH,
        _hosts=hosts,
    )

    async with first:
        await first.users_get_current_account()
    async with second:
        await second.users_get_current_account()

    assert count == 2


async def test_refresh_retries_non_json_429_with_retry_after(
    client_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    calls = 0

    async def token(request: web.Request) -> web.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return web.Response(
                status=429, text="slow down", headers={"Retry-After": "2.5"}
            )
        return _token_response("access-1")

    async def account(request: web.Request) -> web.Response:
        return web.json_response({"account_id": "dbid:x"})

    async with client_factory(
        {"/oauth2/token": token, "/2/users/get_current_account": account},
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        refresh_token=REFRESH,
        content_host=False,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0),
    ) as dbx:
        result = await dbx.users_get_current_account()

    assert result == {"account_id": "dbid:x"}
    assert calls == 2
    assert delays == [2.5]


async def test_refresh_retries_body_read_failure(client_factory) -> None:
    calls = 0

    async def token(request: web.Request) -> web.StreamResponse:
        nonlocal calls
        calls += 1

        if calls == 1:
            response = web.StreamResponse(headers={"Content-Type": "application/json"})
            await response.prepare(request)
            await response.write(b'{"access_token": "a",')
            if request.transport:
                request.transport.close()
            return response

        return _token_response("access-1")

    async def account(request: web.Request) -> web.Response:
        return web.json_response({"account_id": "dbid:x"})

    async with client_factory(
        {"/oauth2/token": token, "/2/users/get_current_account": account},
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        refresh_token=REFRESH,
        content_host=False,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0),
    ) as dbx:
        result = await dbx.users_get_current_account()

    assert result == {"account_id": "dbid:x"}
    assert calls == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"expires_in": 100, "token_type": "bearer"},
        {"access_token": "", "expires_in": 100, "token_type": "bearer"},
        {"access_token": "a", "expires_in": True, "token_type": "bearer"},
        {"access_token": "a", "expires_in": 0, "token_type": "bearer"},
        {"access_token": "a", "expires_in": 100, "token_type": "mac"},
    ],
)
async def test_refresh_rejects_malformed_lease(client_factory, payload) -> None:
    async def token(request: web.Request) -> web.Response:
        return web.json_response(payload)

    async def account(request: web.Request) -> web.Response:
        return web.json_response({})

    async with client_factory(
        {"/oauth2/token": token, "/2/users/get_current_account": account},
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        refresh_token=REFRESH,
        content_host=False,
    ) as dbx:
        with pytest.raises(DropboxProtocolError):
            await dbx.users_get_current_account()


async def test_refresh_rejects_overflowing_expiry(client_factory) -> None:
    async def token(request: web.Request) -> web.Response:
        return web.json_response(
            {"access_token": "a", "expires_in": 10**400, "token_type": "bearer"}
        )

    async def account(request: web.Request) -> web.Response:
        return web.json_response({})

    async with client_factory(
        {"/oauth2/token": token, "/2/users/get_current_account": account},
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        refresh_token=REFRESH,
        content_host=False,
    ) as dbx:
        with pytest.raises(DropboxProtocolError):
            await dbx.users_get_current_account()
