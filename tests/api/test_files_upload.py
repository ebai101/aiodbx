from __future__ import annotations

import json

import pytest
from aiohttp import web

from aiodbx import AsyncDropbox


@pytest.mark.asyncio
async def test_files_upload_sends_content_headers_and_body(client_factory) -> None:
    body = b"hello from aiodbx\n"
    metadata = {
        ".tag": "file",
        "name": "fixture.txt",
        "path_display": "/fixture.txt",
        "size": len(body),
    }

    async def upload(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["Content-Type"].startswith("application/octet-stream")
        assert json.loads(request.headers["Dropbox-API-Arg"]) == {
            "path": "/fixture.txt",
            "mode": "overwrite",
            "autorename": False,
            "mute": False,
            "strict_conflict": False,
        }
        assert await request.read() == body
        return web.json_response(metadata)

    async with client_factory(
        {"/2/files/upload": upload},
        api_host=False,
    ) as dbx:
        result = await dbx.files_upload(
            body,
            "/fixture.txt",
            mode="overwrite",
        )

    assert result == metadata


@pytest.mark.asyncio
async def test_files_upload_rejects_non_bytes() -> None:
    async with AsyncDropbox("test-token") as dbx:
        with pytest.raises(TypeError, match="bytes"):
            await dbx.files_upload("not bytes", "/fixture.txt")  # ty: ignore[invalid-argument-type]


@pytest.mark.asyncio
async def test_files_upload_rejects_root_path() -> None:
    async with AsyncDropbox("test-token") as dbx:
        with pytest.raises(ValueError, match="non-root"):
            await dbx.files_upload(b"content", "")


@pytest.mark.asyncio
async def test_files_upload_rejects_oversized_content(monkeypatch) -> None:
    monkeypatch.setattr("aiodbx.files.SIMPLE_UPLOAD_MAX_BYTES", 3)

    async with AsyncDropbox("test-token") as dbx:
        with pytest.raises(ValueError, match="150 MiB"):
            await dbx.files_upload(b"1234", "/fixture.txt")
