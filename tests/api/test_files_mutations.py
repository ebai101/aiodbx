from __future__ import annotations

import pytest
from aiohttp import web

from aiodbx import AsyncDropbox, DropboxConflictError


@pytest.mark.asyncio
async def test_files_create_folder_v2_sends_expected_request(client_factory) -> None:
    expected_metadata = {
        ".tag": "folder",
        "name": "created-folder",
        "path_lower": "/created-folder",
        "path_display": "/created-folder",
        "id": "id:created-folder",
    }

    async def create_folder(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["Content-Type"].startswith("application/json")
        assert await request.json() == {
            "path": "/created-folder",
            "autorename": False,
        }
        return web.json_response(expected_metadata)

    async with client_factory(
        {"/2/files/create_folder_v2": create_folder},
        content_host=False,
    ) as dbx:
        metadata = await dbx.files_create_folder_v2("/created-folder")

    assert metadata == expected_metadata


@pytest.mark.asyncio
async def test_files_create_folder_v2_passes_autorename(client_factory) -> None:
    async def create_folder(request: web.Request) -> web.Response:
        assert await request.json() == {
            "path": "/created-folder",
            "autorename": True,
        }
        return web.json_response(
            {
                ".tag": "folder",
                "name": "created-folder (1)",
                "path_lower": "/created-folder (1)",
            }
        )

    async with client_factory(
        {"/2/files/create_folder_v2": create_folder},
        content_host=False,
    ) as dbx:
        metadata = await dbx.files_create_folder_v2(
            "/created-folder",
            autorename=True,
        )

    assert metadata["path_lower"] == "/created-folder (1)"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "",
        "/",
        "missing-leading-slash",
        "/trailing-slash/",
    ],
)
async def test_files_create_folder_v2_rejects_invalid_path(path: str) -> None:
    async with AsyncDropbox("test-token") as dbx:
        with pytest.raises(ValueError):
            await dbx.files_create_folder_v2(path)


@pytest.mark.asyncio
async def test_files_create_folder_v2_maps_conflict(client_factory) -> None:
    async def create_folder(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "error_summary": "path/conflict/folder/..",
                "error": {
                    ".tag": "path",
                    "path": {
                        ".tag": "conflict",
                        "conflict": {".tag": "folder"},
                    },
                },
            },
            status=409,
            headers={"X-Dropbox-Request-Id": "request-create-conflict"},
        )

    async with client_factory(
        {"/2/files/create_folder_v2": create_folder},
        content_host=False,
    ) as dbx:
        with pytest.raises(DropboxConflictError) as caught:
            await dbx.files_create_folder_v2("/already-exists")

    error = caught.value
    assert error.status_code == 409
    assert error.error_summary == "path/conflict/folder/.."
    assert error.error_tag == "path/conflict/folder"
    assert error.request_id == "request-create-conflict"
