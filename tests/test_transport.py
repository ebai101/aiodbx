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

from .helpers import make_app


@pytest.mark.asyncio
async def test_401_becomes_authentication_error(aiohttp_server) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "error_summary": "expired_access_token/..",
                "error": {".tag": "expired_access_token"},
            },
            status=401,
            headers={"X-Dropbox-Request-Id": "request-1"},
        )

    server = await aiohttp_server(make_app({"/2/test": handler}))

    async with AsyncDropbox(
        "test-token",
        _api_host=str(server.make_url("/")).rstrip("/"),
    ) as dbx:
        transport = dbx._transport
        assert transport is not None

        with pytest.raises(DropboxAuthenticationError) as caught:
            await transport.rpc("/2/test", {})

    error = caught.value
    assert error.status_code == 401
    assert error.error_tag == "expired_access_token"
    assert error.request_id == "request-1"


@pytest.mark.asyncio
async def test_409_becomes_conflict_error(aiohttp_server) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "error_summary": "path/not_found/..",
                "error": {".tag": "path"},
            },
            status=409,
        )

    server = await aiohttp_server(make_app({"/2/test": handler}))

    async with AsyncDropbox(
        "test-token",
        _api_host=str(server.make_url("/")).rstrip("/"),
    ) as dbx:
        transport = dbx._transport
        assert transport is not None

        with pytest.raises(DropboxConflictError):
            await transport.rpc("/2/test", {})


@pytest.mark.asyncio
async def test_invalid_success_json_becomes_dropbox_error(aiohttp_server) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(text="not json", content_type="text/plain")

    server = await aiohttp_server(make_app({"/2/test": handler}))

    async with AsyncDropbox(
        "test-token",
        _api_host=str(server.make_url("/")).rstrip("/"),
    ) as dbx:
        transport = dbx._transport
        assert transport is not None

        with pytest.raises(DropboxError, match="invalid JSON"):
            await transport.rpc("/2/test", {})


@pytest.mark.asyncio
async def test_rate_limit_is_retried(aiohttp_server) -> None:
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

    server = await aiohttp_server(make_app({"/2/test": handler}))

    async with AsyncDropbox(
        "test-token",
        retry_policy=RetryPolicy(max_attempts=2),
        _api_host=str(server.make_url("/")).rstrip("/"),
    ) as dbx:
        transport = dbx._transport
        assert transport is not None
        result = await transport.rpc("/2/test", {})

    assert result == {"ok": True}
    assert attempts == 2


@pytest.mark.asyncio
async def test_rate_limit_exhaustion_exposes_retry_after(aiohttp_server) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response(
            {"error_summary": "too_many_requests/.."},
            status=429,
            headers={"Retry-After": "0"},
        )

    server = await aiohttp_server(make_app({"/2/test": handler}))

    async with AsyncDropbox(
        "test-token",
        retry_policy=RetryPolicy(max_attempts=1),
        _api_host=str(server.make_url("/")).rstrip("/"),
    ) as dbx:
        transport = dbx._transport
        assert transport is not None

        with pytest.raises(DropboxRateLimitError) as caught:
            await transport.rpc("/2/test", {})

    assert caught.value.retry_after == 0.0


@pytest.mark.asyncio
async def test_cancellation_is_not_retried(aiohttp_server) -> None:
    request_started = asyncio.Event()

    async def handler(_: web.Request) -> web.Response:
        request_started.set()
        await asyncio.sleep(60)
        return web.json_response({"ok": True})

    server = await aiohttp_server(make_app({"/2/test": handler}))

    async with AsyncDropbox(
        "test-token",
        _api_host=str(server.make_url("/")).rstrip("/"),
    ) as dbx:
        transport = dbx._transport
        assert transport is not None

        task = asyncio.create_task(transport.rpc("/2/test", {}))
        await request_started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task
