from __future__ import annotations

import asyncio

import pytest
from aiohttp import web

from aiodbx import (
    DropboxAuthenticationError,
    DropboxConflictError,
    DropboxError,
    DropboxRateLimitError,
    RetryPolicy,
)


def transport_for(dbx):
    transport = dbx._transport
    assert transport is not None
    return transport


@pytest.mark.asyncio
async def test_401_becomes_authentication_error(client_factory) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "error_summary": "expired_access_token/..",
                "error": {".tag": "expired_access_token"},
            },
            status=401,
            headers={"X-Dropbox-Request-Id": "request-1"},
        )

    async with client_factory(
        {"/2/test": handler},
        content_host=False,
    ) as dbx:
        with pytest.raises(DropboxAuthenticationError) as caught:
            await transport_for(dbx).rpc("/2/test", {})

    error = caught.value
    assert error.status_code == 401
    assert error.error_tag == "expired_access_token"
    assert error.request_id == "request-1"


@pytest.mark.asyncio
async def test_409_becomes_conflict_error(client_factory) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "error_summary": "path/not_found/..",
                "error": {".tag": "path"},
            },
            status=409,
        )

    async with client_factory({"/2/test": handler}, content_host=False) as dbx:
        transport = dbx._transport
        assert transport is not None

        with pytest.raises(DropboxConflictError):
            await transport.rpc("/2/test", {})


@pytest.mark.asyncio
async def test_invalid_success_json_becomes_dropbox_error(client_factory) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(text="not json", content_type="text/plain")

    async with client_factory({"/2/test": handler}, content_host=False) as dbx:
        transport = dbx._transport
        assert transport is not None

        with pytest.raises(DropboxError, match="invalid JSON"):
            await transport.rpc("/2/test", {})


@pytest.mark.asyncio
async def test_rate_limit_is_retried(client_factory) -> None:
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

    async with client_factory(
        {"/2/test": handler},
        content_host=False,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0),
    ) as dbx:
        result = await transport_for(dbx).rpc("/2/test", {})

    assert result == {"ok": True}
    assert attempts == 2


@pytest.mark.asyncio
async def test_rate_limit_exhaustion_exposes_retry_after(client_factory) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response(
            {"error_summary": "too_many_requests/.."},
            status=429,
            headers={"Retry-After": "0"},
        )

    async with client_factory(
        {"/2/test": handler},
        content_host=False,
        retry_policy=RetryPolicy(max_attempts=1),
    ) as dbx:
        transport = dbx._transport
        assert transport is not None

        with pytest.raises(DropboxRateLimitError) as caught:
            await transport.rpc("/2/test", {})

    assert caught.value.retry_after == 0.0


@pytest.mark.asyncio
async def test_cancellation_is_not_retried(client_factory) -> None:
    request_started = asyncio.Event()

    async def handler(_: web.Request) -> web.Response:
        request_started.set()
        await asyncio.sleep(60)
        return web.json_response({"ok": True})

    async with client_factory(
        {"/2/test": handler},
        content_host=False,
    ) as dbx:
        task = asyncio.create_task(transport_for(dbx).rpc("/2/test", {}))
        await request_started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_nested_dropbox_error_tag_is_exposed(client_factory) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "error_summary": "path/not_found/..",
                "error": {
                    ".tag": "path",
                    "path": {".tag": "not_found"},
                },
            },
            status=409,
        )

    async with client_factory(
        {"/2/test": handler},
        content_host=False,
    ) as dbx:
        transport = dbx._transport
        assert transport is not None

        with pytest.raises(DropboxConflictError) as caught:
            await transport.rpc("/2/test", {})

    assert caught.value.error_tag == "path/not_found"
    assert caught.value.error_summary == "path/not_found/.."


def test_error_string_includes_safe_metadata() -> None:
    error = DropboxError(
        message="Dropbox API request failed.",
        status_code=429,
        error_summary="too_many_requests/..",
        error_tag="too_many_requests",
        request_id="request-123",
        retry_after=2.5,
        response_body='{"potentially":"sensitive"}',
    )

    assert str(error) == (
        "Dropbox API request failed. "
        "(status=429, error=too_many_requests/.., "
        "tag=too_many_requests, request_id=request-123, retry_after=2.5s)"
    )
    assert "potentially" not in str(error)


def test_error_diagnostic_details_excludes_response_body() -> None:
    error = DropboxError(
        message="failed",
        status_code=400,
        error_summary="path/malformed_path/..",
        response_body='{"secret":"do-not-log"}',
    )

    assert error.diagnostic_details() == {
        "status_code": 400,
        "error_summary": "path/malformed_path/..",
        "error_tag": None,
        "request_id": None,
        "retry_after": None,
    }


@pytest.mark.asyncio
async def test_rpc_retries_server_error_then_succeeds(client_factory) -> None:
    attempts = 0

    async def handler(_: web.Request) -> web.Response:
        nonlocal attempts
        attempts += 1

        if attempts == 1:
            return web.Response(status=503, text="temporarily unavailable")

        return web.json_response({"ok": True})

    async with client_factory(
        {"/2/test": handler},
        content_host=False,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0),
    ) as dbx:
        result = await transport_for(dbx).rpc("/2/test", {})

    assert result == {"ok": True}
    assert attempts == 2
