from __future__ import annotations

import pytest
from aiohttp import web

from aiodbx import RetryPolicy


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


@pytest.mark.asyncio
async def test_files_get_metadata_retries_transient_server_error(
    client_factory,
) -> None:
    attempts = 0
    expected_response = {
        ".tag": "file",
        "name": "report.txt",
        "path_display": "/report.txt",
    }

    async def get_metadata(_: web.Request) -> web.Response:
        nonlocal attempts
        attempts += 1

        if attempts == 1:
            return web.Response(status=503, text="temporarily unavailable")

        return web.json_response(expected_response)

    async with client_factory(
        {"/2/files/get_metadata": get_metadata},
        content_host=False,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0),
    ) as dbx:
        result = await dbx.files_get_metadata("/report.txt")

    assert result == expected_response
    assert attempts == 2
