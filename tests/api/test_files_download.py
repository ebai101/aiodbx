from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import web
from anyio import Path as AsyncPath

from aiodbx import AsyncDropbox, DropboxProtocolError, RetryPolicy


@pytest.mark.asyncio
async def test_files_download_rejects_root_path() -> None:
    async with AsyncDropbox("test-token") as dbx:
        with pytest.raises(ValueError, match="non-root"):
            async with dbx.files_download(""):
                pass


@pytest.mark.asyncio
async def test_files_download_to_path_streams_content_and_returns_metadata(
    client_factory,
    tmp_path: Path,
) -> None:
    metadata = {
        ".tag": "file",
        "name": "fixture.bin",
        "path_display": "/fixture.bin",
        "size": 11,
    }

    async def download(request: web.Request) -> web.StreamResponse:
        assert request.headers["Authorization"] == "Bearer test-token"
        assert json.loads(request.headers["Dropbox-API-Arg"]) == {
            "path": "/fixture.bin"
        }

        response = web.StreamResponse(
            status=200,
            headers={"Dropbox-API-Result": json.dumps(metadata)},
        )
        await response.prepare(request)
        await response.write(b"hello ")
        await response.write(b"world")
        await response.write_eof()
        return response

    destination = AsyncPath(tmp_path) / "fixture.bin"

    async with client_factory(
        {"/2/files/download": download},
        api_host=False,
    ) as dbx:
        result = await dbx.files_download_to_path(
            "/fixture.bin",
            destination,
            chunk_size=3,
        )

    assert result == metadata
    assert await destination.read_bytes() == b"hello world"
    assert [path async for path in AsyncPath(tmp_path).glob(".*.partial")] == []


@pytest.mark.asyncio
async def test_files_download_to_path_refuses_existing_destination(
    client_factory,
    tmp_path: Path,
) -> None:
    request_count = 0

    async def download(_: web.Request) -> web.Response:
        nonlocal request_count
        request_count += 1
        return web.Response(
            body=b"request should not occur",
            headers={
                "Dropbox-API-Result": json.dumps(
                    {
                        ".tag": "file",
                        "name": "fixture.bin",
                        "path_display": "/fixture.bin",
                        "size": 24,
                    }
                )
            },
        )

    destination = AsyncPath(tmp_path) / "existing.bin"
    await destination.write_bytes(b"existing")

    async with client_factory(
        {"/2/files/download": download},
        api_host=False,
    ) as dbx:
        with pytest.raises(FileExistsError) as caught:
            await dbx.files_download_to_path("/fixture.bin", destination)

    assert str(caught.value) == str(destination)
    assert request_count == 0
    assert await destination.read_bytes() == b"existing"


@pytest.mark.asyncio
async def test_files_download_to_path_replaces_existing_destination_when_allowed(
    client_factory,
    tmp_path: Path,
) -> None:
    request_count = 0
    metadata = {
        ".tag": "file",
        "name": "fixture.bin",
        "path_display": "/fixture.bin",
        "size": 11,
    }

    async def download(_: web.Request) -> web.Response:
        nonlocal request_count
        request_count += 1
        return web.Response(
            body=b"hello world",
            headers={"Dropbox-API-Result": json.dumps(metadata)},
        )

    destination = AsyncPath(tmp_path) / "existing.bin"
    await destination.write_bytes(b"old content")

    async with client_factory(
        {"/2/files/download": download},
        api_host=False,
    ) as dbx:
        result = await dbx.files_download_to_path(
            "/fixture.bin",
            destination,
            overwrite=True,
        )

    assert result == metadata
    assert await destination.read_bytes() == b"hello world"
    assert request_count == 1


@pytest.mark.asyncio
async def test_files_download_rejects_missing_metadata_header(client_factory) -> None:
    async def download(_: web.Request) -> web.Response:
        return web.Response(body=b"payload")

    async with client_factory(
        {"/2/files/download": download},
        api_host=False,
    ) as dbx:
        with pytest.raises(DropboxProtocolError, match="Dropbox-API-Result"):
            async with dbx.files_download("/fixture.bin"):
                pass


@pytest.mark.asyncio
async def test_files_download_retries_before_a_successful_response(
    client_factory,
    tmp_path: Path,
) -> None:
    attempts = 0
    metadata = {
        ".tag": "file",
        "name": "fixture.bin",
        "path_display": "/fixture.bin",
        "size": 5,
    }

    async def download(request: web.Request) -> web.StreamResponse:
        nonlocal attempts
        attempts += 1

        if attempts == 1:
            return web.Response(status=503, text="temporarily unavailable")

        response = web.StreamResponse(
            headers={"Dropbox-API-Result": json.dumps(metadata)},
        )
        await response.prepare(request)
        await response.write(b"hello")
        await response.write_eof()
        return response

    destination = tmp_path / "fixture.bin"

    async with client_factory(
        {"/2/files/download": download},
        api_host=False,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0),
    ) as dbx:
        result = await dbx.files_download_to_path("/fixture.bin", destination)

    assert result == metadata
    assert attempts == 2
    assert destination.read_bytes() == b"hello"
