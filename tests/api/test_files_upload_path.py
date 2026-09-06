import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from anyio import Path as AsyncPath

from aiodbx import DropboxError, DropboxProtocolError, RetryPolicy


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "remote_path", "mode"),
    [
        pytest.param(
            b"small upload",
            "/small.bin",
            "overwrite",
            id="small-nonempty-source",
        ),
        pytest.param(
            b"",
            "/empty.bin",
            "overwrite",
            id="empty-source",
        ),
    ],
)
async def test_files_upload_path_uses_simple_upload(
    client_factory,
    tmp_path: Path,
    body: bytes,
    remote_path: str,
    mode: str,
) -> None:
    source = AsyncPath(tmp_path / "fixture.bin")
    await source.write_bytes(body)

    expected_metadata = {
        ".tag": "file",
        "name": remote_path.removeprefix("/"),
        "path_display": remote_path,
        "size": len(body),
    }

    async def upload(request: web.Request) -> web.Response:
        assert request.path == "/2/files/upload"
        assert json.loads(request.headers["Dropbox-API-Arg"]) == {
            "path": remote_path,
            "mode": mode,
            "autorename": False,
            "mute": False,
            "strict_conflict": False,
        }
        assert await request.read() == body
        return web.json_response(expected_metadata)

    async with client_factory(
        {
            "/2/files/upload": upload,
        },
        api_host=False,
    ) as dbx:
        metadata = await dbx.files_upload_path(
            source,
            remote_path,
            mode=mode,
        )

    assert metadata == expected_metadata


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "chunk_size", "expected_requests"),
    [
        pytest.param(
            b"abcdef",
            3,
            [
                (
                    "/2/files/upload_session/start",
                    {"close": False},
                    b"abc",
                ),
                (
                    "/2/files/upload_session/finish",
                    {
                        "cursor": {
                            "session_id": "session-1",
                            "offset": 3,
                        },
                        "commit": {
                            "path": "/fixture.bin",
                            "mode": "overwrite",
                            "autorename": False,
                            "mute": False,
                            "strict_conflict": False,
                        },
                    },
                    b"def",
                ),
            ],
            id="two-chunks-start-then-finish",
        ),
        pytest.param(
            b"abcdefghi",
            3,
            [
                (
                    "/2/files/upload_session/start",
                    {"close": False},
                    b"abc",
                ),
                (
                    "/2/files/upload_session/append_v2",
                    {
                        "cursor": {
                            "session_id": "session-1",
                            "offset": 3,
                        },
                        "close": False,
                    },
                    b"def",
                ),
                (
                    "/2/files/upload_session/finish",
                    {
                        "cursor": {
                            "session_id": "session-1",
                            "offset": 6,
                        },
                        "commit": {
                            "path": "/fixture.bin",
                            "mode": "overwrite",
                            "autorename": False,
                            "mute": False,
                            "strict_conflict": False,
                        },
                    },
                    b"ghi",
                ),
            ],
            id="three-chunks-start-append-finish",
        ),
        pytest.param(
            b"abcd",
            3,
            [
                (
                    "/2/files/upload_session/start",
                    {"close": False},
                    b"abc",
                ),
                (
                    "/2/files/upload_session/finish",
                    {
                        "cursor": {
                            "session_id": "session-1",
                            "offset": 3,
                        },
                        "commit": {
                            "path": "/fixture.bin",
                            "mode": "overwrite",
                            "autorename": False,
                            "mute": False,
                            "strict_conflict": False,
                        },
                    },
                    b"d",
                ),
            ],
            id="one-byte-over-chunk-start-then-finish",
        ),
        pytest.param(
            b"abcdef",
            2,
            [
                (
                    "/2/files/upload_session/start",
                    {"close": False},
                    b"ab",
                ),
                (
                    "/2/files/upload_session/append_v2",
                    {
                        "cursor": {
                            "session_id": "session-1",
                            "offset": 2,
                        },
                        "close": False,
                    },
                    b"cd",
                ),
                (
                    "/2/files/upload_session/finish",
                    {
                        "cursor": {
                            "session_id": "session-1",
                            "offset": 4,
                        },
                        "commit": {
                            "path": "/fixture.bin",
                            "mode": "overwrite",
                            "autorename": False,
                            "mute": False,
                            "strict_conflict": False,
                        },
                    },
                    b"ef",
                ),
            ],
            id="three-exact-chunks-start-append-finish",
        ),
        pytest.param(
            b"abcdef",
            3,
            [
                (
                    "/2/files/upload_session/start",
                    {"close": False},
                    b"abc",
                ),
                (
                    "/2/files/upload_session/finish",
                    {
                        "cursor": {
                            "session_id": "session-1",
                            "offset": 3,
                        },
                        "commit": {
                            "path": "/fixture.bin",
                            "mode": "overwrite",
                            "autorename": False,
                            "mute": False,
                            "strict_conflict": False,
                        },
                    },
                    b"def",
                ),
            ],
            id="two-exact-chunks-final-nonempty-finish",
        ),
    ],
)
async def test_files_upload_path_uses_expected_session_request_sequence(
    client_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    body: bytes,
    chunk_size: int,
    expected_requests: list[tuple[str, dict[str, Any], bytes]],
) -> None:
    monkeypatch.setattr("aiodbx.files.SIMPLE_UPLOAD_MAX_BYTES", 1)

    source = AsyncPath(tmp_path / "fixture.bin")
    await source.write_bytes(body)

    observed_requests: list[tuple[str, dict[str, Any], bytes]] = []

    async def start(request: web.Request) -> web.Response:
        observed_requests.append(
            (
                request.path,
                json.loads(request.headers["Dropbox-API-Arg"]),
                await request.read(),
            )
        )
        return web.json_response({"session_id": "session-1"})

    async def append(request: web.Request) -> web.Response:
        observed_requests.append(
            (
                request.path,
                json.loads(request.headers["Dropbox-API-Arg"]),
                await request.read(),
            )
        )
        return web.Response(status=200)

    async def finish(request: web.Request) -> web.Response:
        observed_requests.append(
            (
                request.path,
                json.loads(request.headers["Dropbox-API-Arg"]),
                await request.read(),
            )
        )
        return web.json_response(
            {
                ".tag": "file",
                "name": "fixture.bin",
                "path_display": "/fixture.bin",
                "size": len(body),
            }
        )

    async with client_factory(
        {
            "/2/files/upload_session/start": start,
            "/2/files/upload_session/append_v2": append,
            "/2/files/upload_session/finish": finish,
        },
        api_host=False,
    ) as dbx:
        metadata = await dbx.files_upload_path(
            source,
            "/fixture.bin",
            mode="overwrite",
            chunk_size=chunk_size,
        )

    assert metadata == {
        ".tag": "file",
        "name": "fixture.bin",
        "path_display": "/fixture.bin",
        "size": len(body),
    }
    assert observed_requests == expected_requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chunk_size", "remote_path", "error_type", "source_exists"),
    [
        pytest.param(
            0,
            "/fixture.bin",
            ValueError,
            True,
            id="zero-chunk-size",
        ),
        pytest.param(
            -1,
            "/fixture.bin",
            ValueError,
            True,
            id="negative-chunk-size",
        ),
        pytest.param(
            True,
            "/fixture.bin",
            TypeError,
            True,
            id="boolean-chunk-size",
        ),
        pytest.param(
            "1024",
            "/fixture.bin",
            TypeError,
            True,
            id="string-chunk-size",
        ),
        pytest.param(
            None,
            "/fixture.bin",
            TypeError,
            True,
            id="none-chunk-size",
        ),
        pytest.param(
            1,
            "invalid.bin",
            ValueError,
            True,
            id="invalid-dropbox-path",
        ),
        pytest.param(
            1,
            "/fixture.bin",
            FileNotFoundError,
            False,
            id="missing-local-source",
        ),
    ],
)
async def test_files_upload_path_rejects_invalid_input(
    client_factory,
    tmp_path: Path,
    chunk_size: object,
    remote_path: str,
    error_type: type[BaseException],
    source_exists: bool,
) -> None:
    source = AsyncPath(tmp_path / "fixture.bin")
    if source_exists:
        await source.write_bytes(b"content")

    async with client_factory({}) as dbx:
        with pytest.raises(error_type):
            await dbx.files_upload_path(
                source,
                remote_path,
                chunk_size=chunk_size,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start_response",
    [
        pytest.param({}, id="missing-session-id"),
        pytest.param({"session_id": 1}, id="non-string-session-id"),
        pytest.param({"session_id": ""}, id="empty-session-id"),
    ],
)
async def test_files_upload_path_handles_malformed_start_response(
    client_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    start_response: dict[str, Any],
):
    monkeypatch.setattr("aiodbx.files.SIMPLE_UPLOAD_MAX_BYTES", 2)

    source = AsyncPath(tmp_path / "fixture.bin")
    await source.write_bytes(b"abc")

    async def start(request: web.Request) -> web.Response:
        assert json.loads(request.headers["Dropbox-API-Arg"]) == {
            "close": False,
        }
        assert await request.read() == b"abc"
        return web.json_response(start_response)

    async with client_factory(
        {
            "/2/files/upload_session/start": start,
        },
        api_host=False,
    ) as dbx:
        with pytest.raises(DropboxProtocolError, match="missing a session_id"):
            await dbx.files_upload_path(source, "/fixture.bin")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "body", "chunk_size"),
    [
        pytest.param(
            "simple",
            b"small, non-replayable upload",
            None,
            id="simple-upload",
        ),
        pytest.param(
            "append",
            b"abcdefghi",
            3,
            id="session-append",
        ),
        pytest.param(
            "finish",
            b"abcdef",
            3,
            id="session-finish",
        ),
    ],
)
async def test_files_upload_path_does_not_retry_ambiguous_content_upload(
    client_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: str,
    body: bytes,
    chunk_size: int | None,
) -> None:
    source = AsyncPath(tmp_path / "fixture.bin")
    await source.write_bytes(body)

    attempts = 0

    async def fail(request: web.Request) -> web.Response:
        nonlocal attempts
        attempts += 1
        await request.read()
        return web.Response(status=503, text="ambiguous remote state")

    if operation == "simple":
        routes = {
            "/2/files/upload": fail,
        }
        upload_kwargs: dict[str, Any] = {}
    else:
        monkeypatch.setattr("aiodbx.files.SIMPLE_UPLOAD_MAX_BYTES", 1)

        async def start(request: web.Request) -> web.Response:
            await request.read()
            return web.json_response({"session_id": "session-1"})

        if operation == "append":
            routes = {
                "/2/files/upload_session/start": start,
                "/2/files/upload_session/append_v2": fail,
            }
        else:
            routes = {
                "/2/files/upload_session/start": start,
                "/2/files/upload_session/finish": fail,
            }

        assert chunk_size is not None
        upload_kwargs = {"chunk_size": chunk_size}

    async with client_factory(
        routes,
        api_host=False,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0),
    ) as dbx:
        with pytest.raises(DropboxError) as caught:
            await dbx.files_upload_path(
                source,
                "/fixture.bin",
                **upload_kwargs,
            )

    assert caught.value.status_code == 503
    assert attempts == 1


@pytest.mark.asyncio
async def test_files_upload_path_propagates_cancellation_during_append(
    client_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("aiodbx.files.SIMPLE_UPLOAD_MAX_BYTES", 1)

    source = AsyncPath(tmp_path / "fixture.bin")
    await source.write_bytes(b"abcdefghi")

    append_started = asyncio.Event()
    finish_requests = 0

    async def start(request: web.Request) -> web.Response:
        assert json.loads(request.headers["Dropbox-API-Arg"]) == {
            "close": False,
        }
        assert await request.read() == b"abc"
        return web.json_response({"session_id": "session-1"})

    async def append(request: web.Request) -> web.Response:
        assert json.loads(request.headers["Dropbox-API-Arg"]) == {
            "cursor": {
                "session_id": "session-1",
                "offset": 3,
            },
            "close": False,
        }
        assert await request.read() == b"def"

        append_started.set()
        await asyncio.Event().wait()
        raise AssertionError("cancelled append handler unexpectedly resumed")

    async def finish(request: web.Request) -> web.Response:
        nonlocal finish_requests
        finish_requests += 1
        return web.Response(status=500)

    async with client_factory(
        {
            "/2/files/upload_session/start": start,
            "/2/files/upload_session/append_v2": append,
            "/2/files/upload_session/finish": finish,
        },
        api_host=False,
    ) as dbx:
        task = asyncio.create_task(
            dbx.files_upload_path(
                source,
                "/fixture.bin",
                chunk_size=3,
            )
        )

        await append_started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    assert finish_requests == 0
