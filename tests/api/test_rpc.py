from __future__ import annotations

import pytest
from aiohttp import web

from aiodbx import AsyncDropbox


@pytest.mark.asyncio
async def test_rpc_sends_unstructured_json_payload(client_factory) -> None:
    payload = {
        "from_path": "/draft.txt",
        "to_path": "/published.txt",
        "autorename": False,
        "allow_ownership_transfer": False,
    }
    expected_response = {
        ".tag": "file",
        "name": "published.txt",
        "path_lower": "/published.txt",
    }

    async def move_v2(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["Content-Type"].startswith("application/json")
        assert await request.json() == payload
        return web.json_response(expected_response)

    async with client_factory(
        {"/2/files/move_v2": move_v2},
        content_host=False,
    ) as dbx:
        result = await dbx.rpc("/2/files/move_v2", payload)

    assert result == expected_response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "/",
        "/1/files/move",
        "/files/move_v2",
        "2/files/move_v2",
        "files/move_v2",
        "/2/files/move_v2/",
    ],
)
async def test_rpc_rejects_invalid_endpoint(endpoint: str) -> None:
    async with AsyncDropbox("test-token") as dbx:
        with pytest.raises(ValueError, match="endpoint"):
            await dbx.rpc(endpoint, {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arg",
    [
        None,
        [],
        "not a mapping",
        b"not a mapping",
    ],
)
async def test_rpc_rejects_non_mapping_payload(arg: object) -> None:
    async with AsyncDropbox("test-token") as dbx:
        with pytest.raises(TypeError, match="arg must be a mapping"):
            await dbx.rpc("/2/files/move_v2", arg)  # ty: ignore[invalid-argument-type]
