from __future__ import annotations

import pytest
from aiohttp import web

from aiodbx import AsyncDropbox


@pytest.mark.asyncio
async def test_get_metadata_sends_expected_arguments(server_factory) -> None:
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

    server = await server_factory({"/2/files/get_metadata": get_metadata})

    async with AsyncDropbox("test-token") as dbx:
        transport = dbx._transport
        assert transport is not None
        transport._api_host = server.base_url

        response = await dbx.files.get_metadata("/report.txt")

    assert response[".tag"] == "file"


@pytest.mark.asyncio
async def test_iter_folder_follows_cursor(server_factory) -> None:
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
                "entries": [{"name": "first", ".tag": "file"}],
                "cursor": "cursor-1",
                "has_more": True,
            }
        )

    async def continue_list_folder(request: web.Request) -> web.Response:
        assert await request.json() == {"cursor": "cursor-1"}
        return web.json_response(
            {
                "entries": [{"name": "second", ".tag": "folder"}],
                "cursor": "cursor-2",
                "has_more": False,
            }
        )

    server = await server_factory(
        {
            "/2/files/list_folder": list_folder,
            "/2/files/list_folder/continue": continue_list_folder,
        }
    )

    async with AsyncDropbox("test-token") as dbx:
        transport = dbx._transport
        assert transport is not None
        transport._api_host = server.base_url

        entries = [entry async for entry in dbx.files.iter_folder("/")]

    assert entries == [
        {"name": "first", ".tag": "file"},
        {"name": "second", ".tag": "folder"},
    ]
