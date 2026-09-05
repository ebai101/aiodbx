from __future__ import annotations

import pytest
from aiohttp import web

from aiodbx import DropboxProtocolError


@pytest.mark.asyncio
async def test_files_list_folder_iter_follows_cursor(client_factory) -> None:
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

    async with client_factory(
        {
            "/2/files/list_folder": list_folder,
            "/2/files/list_folder/continue": list_folder_continue,
        },
        content_host=False,
    ) as dbx:
        entries = [entry async for entry in dbx.files_list_folder_iter("/")]

    assert entries == [
        {".tag": "file", "name": "first"},
        {".tag": "folder", "name": "second"},
    ]


@pytest.mark.asyncio
async def test_files_list_folder_iter_rejects_non_list_entries(
    client_factory,
) -> None:
    async def list_folder(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "entries": {"not": "a list"},
                "cursor": "cursor-1",
                "has_more": False,
            }
        )

    async with client_factory(
        {"/2/files/list_folder": list_folder},
        content_host=False,
    ) as dbx:
        with pytest.raises(DropboxProtocolError, match="non-list"):
            _ = [entry async for entry in dbx.files_list_folder_iter("")]


@pytest.mark.asyncio
async def test_files_list_folder_iter_rejects_non_object_entry(
    client_factory,
) -> None:
    async def list_folder(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "entries": ["not-an-object"],
                "cursor": "cursor-1",
                "has_more": False,
            }
        )

    async with client_factory(
        {"/2/files/list_folder": list_folder},
        content_host=False,
    ) as dbx:
        with pytest.raises(DropboxProtocolError, match="non-object"):
            _ = [entry async for entry in dbx.files_list_folder_iter("")]


@pytest.mark.asyncio
async def test_files_list_folder_iter_rejects_missing_cursor_when_more_results(
    client_factory,
) -> None:
    async def list_folder(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "entries": [],
                "has_more": True,
            }
        )

    async with client_factory(
        {"/2/files/list_folder": list_folder},
        content_host=False,
    ) as dbx:
        with pytest.raises(DropboxProtocolError, match="without a string cursor"):
            _ = [entry async for entry in dbx.files_list_folder_iter("")]
