from __future__ import annotations

from typing import Any, cast

import pytest
from aiohttp import web

from aiodbx import AsyncDropbox, DropboxConflictError, SharedLink


@pytest.mark.asyncio
async def test_shared_link_metadata_sends_url_and_returns_metadata(
    client_factory,
) -> None:
    metadata = {"name": "root", ".tag": "folder"}

    async def handler(request: web.Request) -> web.Response:
        assert await request.json() == {"url": "https://example.test/link"}
        return web.json_response(metadata)

    async with client_factory(
        {"/2/sharing/get_shared_link_metadata": handler}, content_host=False
    ) as dbx:
        assert (
            await dbx.sharing_get_shared_link_metadata(
                SharedLink("https://example.test/link")
            )
            == metadata
        )


@pytest.mark.asyncio
async def test_shared_link_metadata_sends_link_password(client_factory) -> None:
    async def handler(request: web.Request) -> web.Response:
        assert await request.json() == {
            "url": "https://example.test/link",
            "link_password": "pw",
        }
        return web.json_response({"name": "root", ".tag": "folder"})

    async with client_factory(
        {"/2/sharing/get_shared_link_metadata": handler}, content_host=False
    ) as dbx:
        await dbx.sharing_get_shared_link_metadata(
            SharedLink("https://example.test/link", password="pw")
        )


@pytest.mark.asyncio
async def test_shared_link_metadata_conflict(client_factory) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response(
            {"error": {".tag": "shared_link_not_found"}}, status=409
        )

    async with client_factory(
        {"/2/sharing/get_shared_link_metadata": handler}, content_host=False
    ) as dbx:
        with pytest.raises(DropboxConflictError) as exc:
            await dbx.sharing_get_shared_link_metadata(
                SharedLink("https://example.test/link")
            )
    assert exc.value.error_tag == "shared_link_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [{"url": "https://example.test/link"}, "url"])
async def test_shared_link_metadata_rejects_wrong_type(value: object) -> None:
    async with AsyncDropbox("test-token") as dbx:
        with pytest.raises(TypeError):
            await dbx.sharing_get_shared_link_metadata(cast(Any, value))
