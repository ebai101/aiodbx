from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web

import aiodbx.oauth as oauth_module
from aiodbx import (
    DropboxAuthenticationError,
    DropboxError,
    RetryPolicy,
)
from tests.oauth.test_refresh import APP_KEY, APP_SECRET, REFRESH
from tests.oauth.test_refresh import _token_response as token_response

EXPIRED = {
    "error_summary": "expired_access_token/..",
    "error": {".tag": "expired_access_token"},
}

CREDENTIALS = {
    "app_key": APP_KEY,
    "app_secret": APP_SECRET,
    "refresh_token": REFRESH,
}


def _expired_response() -> web.Response:
    return web.json_response(EXPIRED, status=401)


async def test_rpc_401_expired_replays_once_with_independent_budget(
    client_factory,
) -> None:
    token_calls = 0

    async def token(request: web.Request) -> web.Response:
        nonlocal token_calls
        token_calls += 1
        return token_response(f"access-{token_calls}")

    seen: list[str] = []

    async def account(request: web.Request) -> web.Response:
        seen.append(request.headers["Authorization"])
        if len(seen) == 1:
            return _expired_response()
        return web.json_response({"account_id": "dbid:x"})

    async with client_factory(
        {"/oauth2/token": token, "/2/users/get_current_account": account},
        retry_policy=RetryPolicy(max_attempts=1),
        content_host=False,
        **CREDENTIALS,
    ) as dbx:
        result = await dbx.users_get_current_account()

    assert result == {"account_id": "dbid:x"}
    assert seen == ["Bearer access-1", "Bearer access-2"]
    assert token_calls == 2


async def test_download_401_expired_replays_before_yield(client_factory) -> None:
    token_calls = 0

    async def token(request: web.Request) -> web.Response:
        nonlocal token_calls
        token_calls += 1
        return token_response(f"access-{token_calls}")

    seen: list[str] = []

    async def download(request: web.Request) -> web.Response:
        seen.append(request.headers["Authorization"])
        if len(seen) == 1:
            return _expired_response()
        return web.Response(
            body=b"payload",
            headers={"Dropbox-API-Result": json.dumps({".tag": "file"})},
        )

    async with (
        client_factory(
            {"/oauth2/token": token, "/2/files/download": download},
            **CREDENTIALS,
        ) as dbx,
        dbx.files_download("/fixture.bin") as response,
    ):
        body = b"".join([chunk async for chunk in response.iter_bytes()])

    assert body == b"payload"
    assert seen == ["Bearer access-1", "Bearer access-2"]
    assert token_calls == 2


async def test_upload_401_expired_replays_even_when_nonretryable(
    client_factory,
) -> None:
    token_calls = 0

    async def token(request: web.Request) -> web.Response:
        nonlocal token_calls
        token_calls += 1
        return token_response(f"access-{token_calls}")

    seen: list[str] = []

    async def upload(request: web.Request) -> web.Response:
        seen.append(request.headers["Authorization"])
        if len(seen) == 1:
            return _expired_response()
        return web.json_response({".tag": "file", "path_display": "/x"})

    async with client_factory(
        {"/oauth2/token": token, "/2/files/upload": upload},
        **CREDENTIALS,
    ) as dbx:
        result = await dbx.files_upload(b"data", "/x")

    assert result["path_display"] == "/x"
    assert seen == ["Bearer access-1", "Bearer access-2"]
    assert token_calls == 2


async def test_empty_result_401_expired_replays(client_factory) -> None:
    token_calls = 0

    async def token(request: web.Request) -> web.Response:
        nonlocal token_calls
        token_calls += 1
        return token_response(f"access-{token_calls}")

    seen: list[str] = []

    async def append(request: web.Request) -> web.Response:
        seen.append(request.headers["Authorization"])
        if len(seen) == 1:
            return _expired_response()
        return web.Response(status=200)

    async with client_factory(
        {"/oauth2/token": token, "/2/files/upload_session/append_v2": append},
        **CREDENTIALS,
    ) as dbx:
        await dbx.files_upload_session_append_v2(
            {"session_id": "s", "offset": 0}, b"data"
        )

    assert seen == ["Bearer access-1", "Bearer access-2"]
    assert token_calls == 2


async def test_non_expired_401_does_not_recover(client_factory) -> None:
    token_calls = 0

    async def token(request: web.Request) -> web.Response:
        nonlocal token_calls
        token_calls += 1
        return token_response(f"access-{token_calls}")

    seen = 0

    async def account(request: web.Request) -> web.Response:
        nonlocal seen
        seen += 1
        return web.json_response(
            {
                "error_summary": "invalid_access_token/..",
                "error": {".tag": "invalid_access_token"},
            },
            status=401,
        )

    async with client_factory(
        {"/oauth2/token": token, "/2/users/get_current_account": account},
        content_host=False,
        **CREDENTIALS,
    ) as dbx:
        with pytest.raises(DropboxAuthenticationError) as caught:
            await dbx.users_get_current_account()

    assert caught.value.error_tag == "invalid_access_token"
    assert seen == 1
    assert token_calls == 1


async def test_static_token_401_is_not_replayed(client_factory) -> None:
    token_route_called = False

    async def token(request: web.Request) -> web.Response:
        nonlocal token_route_called
        token_route_called = True
        return token_response("should-not-happen")

    seen = 0

    async def account(request: web.Request) -> web.Response:
        nonlocal seen
        seen += 1
        assert request.headers["Authorization"] == "Bearer test-token"
        return _expired_response()

    async with client_factory(
        {"/oauth2/token": token, "/2/users/get_current_account": account},
        content_host=False,
    ) as dbx:
        with pytest.raises(DropboxAuthenticationError):
            await dbx.users_get_current_account()

    assert seen == 1
    assert token_route_called is False


async def test_nonretryable_upload_503_does_not_replay(client_factory) -> None:
    seen = 0

    async def token(request: web.Request) -> web.Response:
        return token_response("access-1")

    async def upload(request: web.Request) -> web.Response:
        nonlocal seen
        seen += 1
        return web.Response(status=503, text="busy")

    async with client_factory(
        {"/oauth2/token": token, "/2/files/upload": upload},
        **CREDENTIALS,
    ) as dbx:
        with pytest.raises(DropboxError) as caught:
            await dbx.files_upload(b"data", "/x")

    assert caught.value.status_code == 503
    assert seen == 1


async def test_stale_rejection_does_not_discard_newer_lease(
    client_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [0.0]
    monkeypatch.setattr(oauth_module, "_now", lambda: clock[0])

    token_calls = 0

    async def token(request: web.Request) -> web.Response:
        nonlocal token_calls
        token_calls += 1
        return token_response(f"access-{token_calls}", expires_in=100)

    first_seen = asyncio.Event()
    release_old = asyncio.Event()
    seen: list[str] = []

    async def account(request: web.Request) -> web.Response:
        auth = request.headers["Authorization"]
        seen.append(auth)
        if auth == "Bearer access-1":
            first_seen.set()
            await release_old.wait()
            return _expired_response()
        return web.json_response({"account_id": "dbid:x"})

    async with client_factory(
        {"/oauth2/token": token, "/2/users/get_current_account": account},
        content_host=False,
        **CREDENTIALS,
    ) as dbx:
        old = asyncio.create_task(dbx.users_get_current_account())
        await first_seen.wait()
        clock[0] = 200.0
        await dbx.users_get_current_account()
        release_old.set()
        await old

    assert token_calls == 2
    assert seen == ["Bearer access-1", "Bearer access-2", "Bearer access-2"]


async def test_consumer_error_in_download_body_is_not_auth_recovery(
    client_factory,
) -> None:
    token_calls = 0

    async def token(request: web.Request) -> web.Response:
        nonlocal token_calls
        token_calls += 1
        return token_response("access-1")

    seen = 0

    async def download(request: web.Request) -> web.Response:
        nonlocal seen
        seen += 1
        return web.Response(
            body=b"payload",
            headers={"Dropbox-API-Result": json.dumps({".tag": "file"})},
        )

    async with client_factory(
        {"/oauth2/token": token, "/2/files/download": download},
        **CREDENTIALS,
    ) as dbx:
        with pytest.raises(DropboxAuthenticationError):
            async with dbx.files_download("/x"):
                raise DropboxAuthenticationError(message="consumer failure")

    assert seen == 1
    assert token_calls == 1
