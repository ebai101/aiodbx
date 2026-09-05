from __future__ import annotations

import pytest
from aiohttp import web


@pytest.mark.asyncio
async def test_files_get_metadata_sends_expected_arguments(client_factory) -> None:
    expected_response = {
        ".tag": "file",
        "name": "report.txt",
        "path_display": "/report.txt",
    }

    async def get_metadata(request: web.Request) -> web.Response:
        assert await request.json() == {
            "path": "/report.txt",
            "include_media_info": False,
            "include_deleted": False,
            "include_has_explicit_shared_members": False,
        }
        return web.json_response(expected_response)

    async with client_factory(
        {"/2/files/get_metadata": get_metadata},
        content_host=False,
    ) as dbx:
        response = await dbx.files_get_metadata("/report.txt")

    assert response == expected_response
