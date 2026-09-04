from __future__ import annotations

import asyncio

import pytest
from aiohttp import web

from aiodbx import (
    AsyncDropbox,
    DropboxAuthenticationError,
    DropboxConflictError,
    DropboxError,
    DropboxRateLimitError,
    RetryPolicy,
)


@pytest.mark.asyncio
async def test_401_becomes_authentication_error(server_factory) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "error_summary": "expired_access_token/..",
                "error": {".tag": "expired_access_token"},
            },
            status=401,
            headers={"X-Dropbox-Request-Id": "request-1"},
        )

    server = await server_factory({"/2/test": handler})

    async with AsyncDropbox("test-token") as dbx:
        transport = dbx._transport
        assert transport is not None
        transport._api_host = server.base_url

        with pytest.raises(DropboxAuthenticationError) as error:
            await transport.rpc("/2/test", {})

    assert error.value.status_code == 401
    assert error.value.error_tag == "expired_access_token"
    assert error.value.request_id == "request-1"


@pytest.mark.asyncio
async def test_409_becomes_conflict_error(server_factory) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "error_summary": "path/not_found/..",
                "error": {".tag": "path"},
            },
            status=409,
        )

    server = await server_factory({"/2/test": handler})

    async with AsyncDropbox("test-token") as dbx:
        transport = dbx._transport
        assert transport is not None
        transport._api_host = server.base_url

        with pytest.raises(DropboxConflictError):
            await transport.rpc("/2/test", {})


@pytest.mark.asyncio
async def test_invalid_success_json_becomes_dropbox_error(server_factory) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(text="not json", content_type="text/plain")

    server = await server_factory({"/2/test": handler})

    async with AsyncDropbox("test-token") as dbx:
        transport = dbx._transport
        assert transport is not None
        transport._api_host = server.base_url

        with pytest.raises(DropboxError, match="invalid JSON"):
            await transport.rpc("/2/test", {})


@pytest.mark.asyncio
async def test_rate_limit_is_retried(server_factory, monkeypatch) -> None:
    attempts = 0

    async def handler(_: web.Request) -> web.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return web.json_response(
                {"error_summary": "too_many_requests/.."},
                status=429,
                headers={"Retry-After": "0"},
            )
        return web.json_response({"ok": True})

    server = await server_factory({"/2/test": handler})

    async with AsyncDropbox(
        "test-token",
        retry_policy=RetryPolicy(max_attempts=2),
    ) as dbx:
        transport = dbx._transport
        assert transport is not None
        transport._api_host = server.base_url

        result = await transport.rpc("/2/test", {})

    assert result == {"ok": True}
    assert attempts == 2


@pytest.mark.asyncio
async def test_rate_limit_exhaustion_exposes_retry_after(server_factory) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response(
            {"error_summary": "too_many_requests/.."},
            status=429,
            headers={"Retry-After": "0"},
        )

    server = await server_factory({"/2/test": handler})

    async with AsyncDropbox(
        "test-token",
        retry_policy=RetryPolicy(max_attempts=1),
    ) as dbx:
        transport = dbx._transport
        assert transport is not None
        transport._api_host = server.base_url

        with pytest.raises(DropboxRateLimitError) as error:
            await transport.rpc("/2/test", {})

    assert error.value.retry_after == 0.0


@pytest.mark.asyncio
async def test_cancellation_is_not_retried(server_factory) -> None:
    started = asyncio.Event()

    async def handler(_: web.Request) -> web.Response:
        started.set()
        await asyncio.sleep(60)
        return web.json_response({"ok": True})

    server = await server_factory({"/2/test": handler})

    async with AsyncDropbox("test-token") as dbx:
        transport = dbx._transport
        assert transport is not None
        transport._api_host = server.base_url

        task = asyncio.create_task(transport.rpc("/2/test", {}))
        await started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task
