from __future__ import annotations

import json

import pytest
from aiohttp import web
from anyio import Path

from aiodbx import DropboxProtocolError
from aiodbx.client import AsyncDropbox
from aiodbx.hosts import EndpointHosts

from .helpers.local import make_app


@pytest.mark.asyncio
async def test_download_to_path_streams_content_and_returns_metadata(
    aiohttp_server,
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

    server = await aiohttp_server(make_app({"/2/files/download": download}))
    hosts = EndpointHosts(content=str(server.make_url("/")).rstrip("/"))
    destination = tmp_path / "fixture.bin"

    async with AsyncDropbox("test-token", _hosts=hosts) as dbx:
        result = await dbx.files.download_to_path(
            "/fixture.bin",
            destination,
            chunk_size=3,
        )

    assert result == metadata
    assert destination.read_bytes() == b"hello world"
    assert not any(tmp_path.glob(".*.partial"))


@pytest.mark.asyncio
async def test_download_to_path_refuses_existing_destination(
    aiohttp_server,
    tmp_path: Path,
) -> None:
    request_count = 0

    async def download(_: web.Request) -> web.Response:
        nonlocal request_count
        request_count += 1
        return web.Response(
            body=b"this response should never be requested",
            headers={
                "Dropbox-API-Result": json.dumps(
                    {
                        ".tag": "file",
                        "name": "fixture.bin",
                        "path_display": "/fixture.bin",
                        "size": 37,
                    }
                )
            },
        )

    server = await aiohttp_server(make_app({"/2/files/download": download}))
    hosts = EndpointHosts(content=str(server.make_url("/")).rstrip("/"))

    destination = tmp_path / "existing.bin"
    destination.write_bytes(b"existing")

    async with AsyncDropbox("test-token", _hosts=hosts) as dbx:
        with pytest.raises(FileExistsError) as caught:
            await dbx.files.download_to_path("/fixture.bin", destination)

    assert str(caught.value) == str(destination)
    assert destination.read_bytes() == b"existing"
    assert request_count == 0


@pytest.mark.asyncio
async def test_download_to_path_replaces_existing_destination_when_allowed(
    aiohttp_server,
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

    server = await aiohttp_server(make_app({"/2/files/download": download}))
    hosts = EndpointHosts(content=str(server.make_url("/")).rstrip("/"))

    destination = tmp_path / "existing.bin"
    destination.write_bytes(b"old content")

    async with AsyncDropbox("test-token", _hosts=hosts) as dbx:
        result = await dbx.files.download_to_path(
            "/fixture.bin",
            destination,
            overwrite=True,
        )

    assert result == metadata
    assert request_count == 1
    assert destination.read_bytes() == b"hello world"


@pytest.mark.asyncio
async def test_download_rejects_missing_metadata_header(aiohttp_server) -> None:
    async def download(_: web.Request) -> web.Response:
        return web.Response(body=b"payload")

    server = await aiohttp_server(make_app({"/2/files/download": download}))
    hosts = EndpointHosts(content=str(server.make_url("/")).rstrip("/"))

    async with AsyncDropbox("test-token", _hosts=hosts) as dbx:
        with pytest.raises(DropboxProtocolError, match="Dropbox-API-Result"):
            async with dbx.files.download("/fixture.bin"):
                pass


@pytest.mark.asyncio
async def test_download_rejects_root_path() -> None:
    async with AsyncDropbox("test-token") as dbx:
        with pytest.raises(ValueError, match="non-root"):
            async with dbx.files.download(""):
                pass
