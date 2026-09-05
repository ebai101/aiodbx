from __future__ import annotations

import pytest
from aiohttp import web

from aiodbx import AsyncDropbox, DropboxProtocolError
from aiodbx.hosts import EndpointHosts

from .helpers.local import make_app


@pytest.mark.asyncio
async def test_get_metadata_sends_expected_arguments(aiohttp_server) -> None:
    async def get_metadata(request: web.Request) -> web.Response:
        assert await request.json() == {
            "path": "/report.txt",
            "include_media_info": False,
            "include_deleted": False,
            "include_has_explicit_shared_members": False,
        }
        return web.json_response(
            {
                ".tag": "file",
                "name": "report.txt",
                "path_display": "/report.txt",
            }
        )

    server = await aiohttp_server(make_app({"/2/files/get_metadata": get_metadata}))
    hosts = EndpointHosts(api=str(server.make_url("/")).rstrip("/"))

    async with AsyncDropbox(
        "test-token",
        _hosts=hosts,
    ) as dbx:
        response = await dbx.files_get_metadata("/report.txt")

    assert response == {
        ".tag": "file",
        "name": "report.txt",
        "path_display": "/report.txt",
    }


@pytest.mark.asyncio
async def test_iter_folder_follows_cursor(aiohttp_server) -> None:
    async def list_folder(request: web.Request) -> web.Response:
        assert await request.json() == {
            "path": "/",
            "recursive": False,
            "include_media_info": False,
            "include_deleted": False,
            "include_has_explicit_shared_members": False,
            "include_mounted_folders": True,
        }
        return web.json_response(
            {
                "entries": [{".tag": "file", "name": "first"}],
                "cursor": "cursor-1",
                "has_more": True,
            }
        )

    async def list_folder_continue(request: web.Request) -> web.Response:
        assert await request.json() == {"cursor": "cursor-1"}
        return web.json_response(
            {
                "entries": [{".tag": "folder", "name": "second"}],
                "cursor": "cursor-2",
                "has_more": False,
            }
        )

    server = await aiohttp_server(
        make_app(
            {
                "/2/files/list_folder": list_folder,
                "/2/files/list_folder/continue": list_folder_continue,
            }
        )
    )
    hosts = EndpointHosts(api=str(server.make_url("/")).rstrip("/"))

    async with AsyncDropbox(
        "test-token",
        _hosts=hosts,
    ) as dbx:
        entries = [entry async for entry in dbx.files_list_folder_iter("/")]

    assert entries == [
        {".tag": "file", "name": "first"},
        {".tag": "folder", "name": "second"},
    ]


@pytest.mark.asyncio
async def test_files_list_folder_iter_rejects_non_list_entries(
    aiohttp_server,
) -> None:
    async def list_folder(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "entries": {"not": "a list"},
                "cursor": "cursor-1",
                "has_more": False,
            }
        )

    server = await aiohttp_server(make_app({"/2/files/list_folder": list_folder}))
    hosts = EndpointHosts(api=str(server.make_url("/")).rstrip("/"))

    async with AsyncDropbox("test-token", _hosts=hosts) as dbx:
        with pytest.raises(DropboxProtocolError, match="non-list"):
            _ = [entry async for entry in dbx.files_list_folder_iter("")]


@pytest.mark.asyncio
async def test_files_list_folder_iter_rejects_non_object_entry(
    aiohttp_server,
) -> None:
    async def list_folder(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "entries": ["not-an-object"],
                "cursor": "cursor-1",
                "has_more": False,
            }
        )

    server = await aiohttp_server(make_app({"/2/files/list_folder": list_folder}))
    hosts = EndpointHosts(api=str(server.make_url("/")).rstrip("/"))

    async with AsyncDropbox("test-token", _hosts=hosts) as dbx:
        with pytest.raises(DropboxProtocolError, match="non-object"):
            _ = [entry async for entry in dbx.files_list_folder_iter("")]


@pytest.mark.asyncio
async def test_files_list_folder_iter_rejects_missing_cursor_when_more_results(
    aiohttp_server,
) -> None:
    async def list_folder(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "entries": [],
                "has_more": True,
            }
        )

    server = await aiohttp_server(make_app({"/2/files/list_folder": list_folder}))
    hosts = EndpointHosts(api=str(server.make_url("/")).rstrip("/"))

    async with AsyncDropbox("test-token", _hosts=hosts) as dbx:
        with pytest.raises(DropboxProtocolError, match="without a string cursor"):
            _ = [entry async for entry in dbx.files_list_folder_iter("")]
