from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiohttp
import pytest
from aiohttp import web
from aiohttp.typedefs import Handler

from aiodbx import (
    DropboxError,
    DropboxProtocolError,
    RetryPolicy,
)
from aiodbx.hosts import EndpointHosts
from aiodbx.transport import DropboxTransport


@pytest.fixture
def transport_factory(aiohttp_server):
    @asynccontextmanager
    async def create(
        routes: dict[str, Handler],
        *,
        retry_policy: RetryPolicy | None = None,
    ) -> AsyncIterator[DropboxTransport]:
        app = web.Application()

        for path, handler in routes.items():
            app.router.add_post(path, handler)

        server = await aiohttp_server(app)
        base_url = str(server.make_url("")).rstrip("/")

        async with aiohttp.ClientSession() as session:
            yield DropboxTransport(
                session=session,
                access_token="test-token",
                retry_policy=retry_policy
                or RetryPolicy(
                    max_attempts=2,
                    base_delay=0,
                ),
                hosts=EndpointHosts(
                    api=base_url,
                    content=base_url,
                    notify=base_url,
                ),
            )

    return create


@pytest.mark.asyncio
async def test_content_download_sends_content_headers_and_exposes_metadata(
    transport_factory,
) -> None:
    metadata = {
        ".tag": "file",
        "name": "fixture.bin",
        "path_display": "/fixture.bin",
        "size": 11,
    }

    async def download(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        assert json.loads(request.headers["Dropbox-API-Arg"]) == {
            "path": "/fixture.bin",
        }
        assert request.headers.get("Content-Type") == "application/octet-stream"

        return web.Response(
            body=b"hello world",
            headers={"Dropbox-API-Result": json.dumps(metadata)},
        )

    async with (
        transport_factory(
            {"/2/files/download": download},
        ) as transport,
        transport.content_download(
            "/2/files/download",
            {"path": "/fixture.bin"},
        ) as response,
    ):
        assert response.metadata == metadata
        assert [chunk async for chunk in response.iter_bytes(chunk_size=3)] == [
            b"hel",
            b"lo ",
            b"wor",
            b"ld",
        ]


@pytest.mark.asyncio
async def test_content_download_retries_retryable_failure_before_stream_yield(
    transport_factory,
) -> None:
    attempts = 0
    metadata = {".tag": "file", "name": "fixture.bin"}

    async def download(_: web.Request) -> web.Response:
        nonlocal attempts
        attempts += 1

        if attempts == 1:
            return web.Response(
                status=503,
                text="temporarily unavailable",
                headers={"X-Dropbox-Request-Id": "request-1"},
            )

        return web.Response(
            body=b"payload",
            headers={"Dropbox-API-Result": json.dumps(metadata)},
        )

    async with (
        transport_factory(
            {"/2/files/download": download},
        ) as transport,
        transport.content_download(
            "/2/files/download",
            {"path": "/fixture.bin"},
            retryable=True,
        ) as response,
    ):
        assert response.metadata == metadata
        assert (
            b"".join([chunk async for chunk in response.iter_bytes(chunk_size=1024)])
            == b"payload"
        )

    assert attempts == 2


@pytest.mark.asyncio
async def test_content_download_does_not_retry_when_not_marked_retryable(
    transport_factory,
) -> None:
    attempts = 0

    async def download(_: web.Request) -> web.Response:
        nonlocal attempts
        attempts += 1
        return web.Response(status=503, text="temporarily unavailable")

    with pytest.raises(DropboxError) as caught:
        async with (
            transport_factory(
                {"/2/files/download": download},
            ) as transport,
            transport.content_download(
                "/2/files/download", {"path": "/fixture.bin"}, retryable=False
            ),
        ):
            pass

    assert caught.value.status_code == 503
    assert attempts == 1


@pytest.mark.asyncio
async def test_content_download_honors_retry_after(
    transport_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def download(_: web.Request) -> web.Response:
        nonlocal attempts
        attempts += 1

        if attempts == 1:
            return web.Response(
                status=429,
                text="rate limited",
                headers={"Retry-After": "3.5"},
            )

        return web.Response(
            body=b"payload",
            headers={"Dropbox-API-Result": json.dumps({})},
        )

    async with (
        transport_factory(
            {"/2/files/download": download},
        ) as transport,
        transport.content_download(
            "/2/files/download",
            {"path": "/fixture.bin"},
            retryable=True,
        ) as response,
    ):
        assert b"".join([chunk async for chunk in response.iter_bytes()]) == b"payload"

    assert attempts == 2
    assert delays == [3.5]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header_value",
    [
        pytest.param(None, id="missing-header"),
        pytest.param("{not-json", id="invalid-json"),
        pytest.param("[]", id="non-object-json"),
    ],
)
async def test_content_download_rejects_invalid_metadata_header(
    transport_factory,
    header_value: str | None,
) -> None:
    async def download(_: web.Request) -> web.Response:
        headers: dict[str, str] = {}
        if header_value is not None:
            headers["Dropbox-API-Result"] = header_value
        return web.Response(body=b"payload", headers=headers)

    with pytest.raises(DropboxProtocolError) as caught:
        async with (
            transport_factory(
                {"/2/files/download": download},
            ) as transport,
            transport.content_download(
                "/2/files/download",
                {"path": "/fixture.bin"},
            ),
        ):
            pass

    assert caught.value.status_code == 200


@pytest.mark.asyncio
async def test_request_stream_propagates_cancellation_without_retry(
    transport_factory,
) -> None:
    request_started = asyncio.Event()
    release_response = asyncio.Event()
    attempts = 0

    async def download(_: web.Request) -> web.Response:
        nonlocal attempts
        attempts += 1
        request_started.set()
        await release_response.wait()
        return web.Response(
            body=b"payload",
            headers={"Dropbox-API-Result": json.dumps({})},
        )

    async def consume() -> None:
        async with (
            transport_factory(
                {"/2/files/download": download},
            ) as transport,
            transport.content_download(
                "/2/files/download",
                {"path": "/fixture.bin"},
                retryable=True,
            ),
        ):
            raise AssertionError("stream should not be acquired before cancellation")

    task = asyncio.create_task(consume())
    await request_started.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert attempts == 1


@pytest.mark.asyncio
async def test_download_does_not_retry_after_body_bytes_begin_flowing(
    client_factory, tmp_path
) -> None:
    attempts = 0
    destination = tmp_path / "fixture.bin"

    async def download(request: web.Request) -> web.StreamResponse:
        nonlocal attempts
        attempts += 1

        response = web.StreamResponse(
            headers={"Dropbox-API-Result": json.dumps({".tag": "file"})},
        )
        await response.prepare(request)
        await response.write(b"partial")

        if request.transport:
            request.transport.close()
        return response

    async with client_factory(
        {"/2/files/download": download},
        api_host=False,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0),
    ) as dbx:
        with pytest.raises(
            (aiohttp.ClientPayloadError, aiohttp.ClientConnectionError),
        ):
            await dbx.files_download_to_path("/fixture.bin", destination)

    assert attempts == 1
    assert not destination.exists()
    assert list(tmp_path.glob(".*.partial")) == []
