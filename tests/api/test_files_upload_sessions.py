from __future__ import annotations

import json

import pytest
from aiohttp import web

from aiodbx import AsyncDropbox, DropboxError, RetryPolicy


@pytest.mark.asyncio
async def test_files_upload_session_start_sends_content_request(client_factory) -> None:
    body = b"first upload-session block"

    async def start(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["Content-Type"].startswith("application/octet-stream")
        assert json.loads(request.headers["Dropbox-API-Arg"]) == {
            "close": False,
        }
        assert await request.read() == body
        return web.json_response({"session_id": "session-1"})

    async with client_factory(
        {"/2/files/upload_session/start": start},
        api_host=False,
    ) as dbx:
        result = await dbx.files_upload_session_start(body)

    assert result == {"session_id": "session-1"}


@pytest.mark.asyncio
async def test_files_upload_session_append_v2_sends_empty_success_request(
    client_factory,
) -> None:
    body = b"next upload-session block"
    cursor = {"session_id": "session-1", "offset": 24}

    async def append(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["Content-Type"].startswith("application/octet-stream")
        assert json.loads(request.headers["Dropbox-API-Arg"]) == {
            "cursor": cursor,
            "close": False,
        }
        assert await request.read() == body
        return web.Response(status=200)

    async with client_factory(
        {"/2/files/upload_session/append_v2": append},
        api_host=False,
    ) as dbx:
        result = await dbx.files_upload_session_append_v2(cursor, body)

    assert result is None


@pytest.mark.asyncio
async def test_files_upload_session_finish_sends_cursor_commit_and_data(
    client_factory,
) -> None:
    body = b"last block"
    cursor = {"session_id": "session-1", "offset": 48}
    commit = {
        "path": "/uploaded.bin",
        "mode": "overwrite",
        "autorename": False,
        "mute": False,
        "strict_conflict": False,
    }
    metadata = {
        ".tag": "file",
        "name": "uploaded.bin",
        "path_display": "/uploaded.bin",
        "size": len(body) + cursor["offset"],
    }

    async def finish(request: web.Request) -> web.Response:
        assert json.loads(request.headers["Dropbox-API-Arg"]) == {
            "cursor": cursor,
            "commit": commit,
        }
        assert await request.read() == body
        return web.json_response(metadata)

    async with client_factory(
        {"/2/files/upload_session/finish": finish},
        api_host=False,
    ) as dbx:
        result = await dbx.files_upload_session_finish(cursor, commit, body)

    assert result == metadata


@pytest.mark.asyncio
async def test_files_upload_session_finish_batch_sends_expected_json(
    client_factory,
) -> None:
    entries = [
        {
            "cursor": {"session_id": "session-1", "offset": 4},
            "commit": {
                "path": "/one.txt",
                "mode": "overwrite",
                "autorename": False,
                "mute": False,
                "strict_conflict": False,
            },
        }
    ]

    async def finish_batch(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["Content-Type"].startswith("application/json")
        assert await request.json() == {"entries": entries}
        return web.json_response({".tag": "complete"})

    async with client_factory(
        {"/2/files/upload_session/finish_batch": finish_batch},
        content_host=False,
    ) as dbx:
        result = await dbx.files_upload_session_finish_batch(entries)

    assert result == {".tag": "complete"}


@pytest.mark.asyncio
async def test_files_upload_session_finish_batch_check_retries_read_only_poll(
    client_factory,
) -> None:
    attempts = 0

    async def check(request: web.Request) -> web.Response:
        nonlocal attempts
        attempts += 1
        assert await request.json() == {"async_job_id": "job-1"}

        if attempts == 1:
            return web.Response(status=503, text="temporarily unavailable")

        return web.json_response({".tag": "complete"})

    async with client_factory(
        {"/2/files/upload_session/finish_batch/check": check},
        content_host=False,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0),
    ) as dbx:
        result = await dbx.files_upload_session_finish_batch_check("job-1")

    assert result == {".tag": "complete"}
    assert attempts == 2


@pytest.mark.asyncio
async def test_upload_session_append_is_not_retried_after_server_failure(
    client_factory,
) -> None:
    attempts = 0
    cursor = {"session_id": "session-1", "offset": 0}

    async def append(request: web.Request) -> web.Response:
        nonlocal attempts
        attempts += 1
        assert await request.read() == b"non-replayable"
        return web.Response(status=503, text="uncertain remote state")

    async with client_factory(
        {"/2/files/upload_session/append_v2": append},
        api_host=False,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0),
    ) as dbx:
        with pytest.raises(DropboxError) as caught:
            await dbx.files_upload_session_append_v2(
                cursor,
                b"non-replayable",
            )

    assert caught.value.status_code == 503
    assert attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cursor", "message"),
    [
        ({}, "session_id"),
        ({"session_id": "", "offset": 0}, "session_id"),
        ({"session_id": "session-1", "offset": -1}, "offset"),
        ({"session_id": "session-1", "offset": True}, "offset"),
    ],
)
async def test_upload_session_append_rejects_invalid_cursor(
    cursor: dict[str, object],
    message: str,
) -> None:
    async with AsyncDropbox("test-token") as dbx:
        with pytest.raises(ValueError, match=message):
            await dbx.files_upload_session_append_v2(
                cursor,  # type: ignore[arg-type]
                b"content",
            )
