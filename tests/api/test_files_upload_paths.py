from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from anyio import Path as AsyncPath

from aiodbx import (
    DropboxError,
    DropboxProtocolError,
    RetryPolicy,
    UploadPath,
)


@pytest.mark.asyncio
async def test_files_upload_paths_streams_and_batch_commits_sources(
    client_factory,
    tmp_path: Path,
) -> None:
    first = AsyncPath(tmp_path / "first.bin")
    second = AsyncPath(tmp_path / "second.bin")
    await first.write_bytes(b"abc")
    await second.write_bytes(b"defgh")

    observed_requests: list[tuple[str, dict[str, Any], bytes]] = []
    session_ids = iter(("session-1", "session-2"))

    async def start(request: web.Request) -> web.Response:
        observed_requests.append(
            (
                request.path,
                json.loads(request.headers["Dropbox-API-Arg"]),
                await request.read(),
            )
        )
        return web.json_response({"session_id": next(session_ids)})

    async def append(request: web.Request) -> web.Response:
        observed_requests.append(
            (
                request.path,
                json.loads(request.headers["Dropbox-API-Arg"]),
                await request.read(),
            )
        )
        return web.Response(status=200)

    expected_result = {
        ".tag": "complete",
        "entries": [
            {
                ".tag": "success",
                "success": {
                    ".tag": "file",
                    "name": "first.bin",
                },
            },
            {
                ".tag": "success",
                "success": {
                    ".tag": "file",
                    "name": "second.bin",
                },
            },
        ],
    }

    async def finish_batch(request: web.Request) -> web.Response:
        assert await request.json() == {
            "entries": [
                {
                    "cursor": {
                        "session_id": "session-1",
                        "offset": 3,
                    },
                    "commit": {
                        "path": "/first.bin",
                        "mode": "overwrite",
                        "autorename": False,
                        "mute": False,
                        "strict_conflict": False,
                    },
                },
                {
                    "cursor": {
                        "session_id": "session-2",
                        "offset": 5,
                    },
                    "commit": {
                        "path": "/second.bin",
                        "mode": "overwrite",
                        "autorename": False,
                        "mute": False,
                        "strict_conflict": False,
                    },
                },
            ],
        }
        return web.json_response(expected_result)

    async with client_factory(
        {
            "/2/files/upload_session/start": start,
            "/2/files/upload_session/append_v2": append,
            "/2/files/upload_session/finish_batch": finish_batch,
        },
    ) as dbx:
        result = await dbx.files_upload_paths(
            [
                UploadPath(first, "/first.bin"),
                UploadPath(second, "/second.bin"),
            ],
            mode="overwrite",
            chunk_size=3,
        )

    assert result == expected_result
    assert observed_requests == [
        (
            "/2/files/upload_session/start",
            {"close": True},
            b"abc",
        ),
        (
            "/2/files/upload_session/start",
            {"close": False},
            b"def",
        ),
        (
            "/2/files/upload_session/append_v2",
            {
                "cursor": {
                    "session_id": "session-2",
                    "offset": 3,
                },
                "close": True,
            },
            b"gh",
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "chunk_size", "expected_content_requests"),
    [
        pytest.param(
            b"abc",
            3,
            [
                (
                    "/2/files/upload_session/start",
                    {"close": True},
                    b"abc",
                ),
            ],
            id="single-chunk-closes-start",
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
                    "/2/files/upload_session/append_v2",
                    {
                        "cursor": {
                            "session_id": "session-1",
                            "offset": 3,
                        },
                        "close": True,
                    },
                    b"d",
                ),
            ],
            id="one-byte-over-chunk-closes-append",
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
                    "/2/files/upload_session/append_v2",
                    {
                        "cursor": {
                            "session_id": "session-1",
                            "offset": 6,
                        },
                        "close": True,
                    },
                    b"ghi",
                ),
            ],
            id="three-chunks-closes-final-append",
        ),
        pytest.param(
            b"",
            3,
            [
                (
                    "/2/files/upload_session/start",
                    {"close": True},
                    b"",
                ),
            ],
            id="empty-source-closes-start",
        ),
    ],
)
async def test_files_upload_paths_closes_each_session_before_batch_finish(
    client_factory,
    tmp_path: Path,
    body: bytes,
    chunk_size: int,
    expected_content_requests: list[tuple[str, dict[str, Any], bytes]],
) -> None:
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

    async def finish_batch(request: web.Request) -> web.Response:
        assert await request.json() == {
            "entries": [
                {
                    "cursor": {
                        "session_id": "session-1",
                        "offset": len(body),
                    },
                    "commit": {
                        "path": "/fixture.bin",
                        "mode": "add",
                        "autorename": False,
                        "mute": False,
                        "strict_conflict": False,
                    },
                },
            ],
        }
        return web.json_response(
            {
                ".tag": "complete",
                "entries": [],
            }
        )

    async with client_factory(
        {
            "/2/files/upload_session/start": start,
            "/2/files/upload_session/append_v2": append,
            "/2/files/upload_session/finish_batch": finish_batch,
        },
    ) as dbx:
        result = await dbx.files_upload_paths(
            [
                UploadPath(source, "/fixture.bin"),
            ],
            chunk_size=chunk_size,
        )

    assert result == {
        ".tag": "complete",
        "entries": [],
    }
    assert observed_requests == expected_content_requests


@pytest.mark.asyncio
async def test_files_upload_paths_polls_finish_batch_job_until_complete(
    client_factory,
    tmp_path: Path,
) -> None:
    source = AsyncPath(tmp_path / "fixture.bin")
    await source.write_bytes(b"abc")

    poll_attempts = 0

    async def start(request: web.Request) -> web.Response:
        assert json.loads(request.headers["Dropbox-API-Arg"]) == {
            "close": True,
        }
        assert await request.read() == b"abc"
        return web.json_response({"session_id": "session-1"})

    async def finish_batch(request: web.Request) -> web.Response:
        assert await request.json() == {
            "entries": [
                {
                    "cursor": {
                        "session_id": "session-1",
                        "offset": 3,
                    },
                    "commit": {
                        "path": "/fixture.bin",
                        "mode": "add",
                        "autorename": False,
                        "mute": False,
                        "strict_conflict": False,
                    },
                },
            ],
        }
        return web.json_response(
            {
                ".tag": "async_job_id",
                "async_job_id": "job-1",
            }
        )

    async def finish_batch_check(request: web.Request) -> web.Response:
        nonlocal poll_attempts
        poll_attempts += 1

        assert await request.json() == {
            "async_job_id": "job-1",
        }

        if poll_attempts == 1:
            return web.json_response(
                {
                    ".tag": "in_progress",
                }
            )

        return web.json_response(
            {
                ".tag": "complete",
                "entries": [
                    {
                        ".tag": "success",
                        "success": {
                            ".tag": "file",
                            "name": "fixture.bin",
                        },
                    },
                ],
            }
        )

    async with client_factory(
        {
            "/2/files/upload_session/start": start,
            "/2/files/upload_session/finish_batch": finish_batch,
            "/2/files/upload_session/finish_batch/check": finish_batch_check,
        },
    ) as dbx:
        result = await dbx.files_upload_paths(
            [
                UploadPath(source, "/fixture.bin"),
            ],
        )

    assert poll_attempts == 2
    assert result == {
        ".tag": "complete",
        "entries": [
            {
                ".tag": "success",
                "success": {
                    ".tag": "file",
                    "name": "fixture.bin",
                },
            },
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("uploads", "error_type"),
    [
        pytest.param(
            [],
            ValueError,
            id="empty-upload-list",
        ),
        pytest.param(
            [object()],
            TypeError,
            id="non-upload-path-item",
        ),
        pytest.param(
            "not-a-sequence-of-upload-paths",
            TypeError,
            id="string-is-not-upload-list",
        ),
    ],
)
async def test_files_upload_paths_rejects_invalid_upload_collections(
    client_factory,
    uploads: object,
    error_type: type[BaseException],
) -> None:
    async with client_factory({}) as dbx:
        with pytest.raises(error_type):
            await dbx.files_upload_paths(uploads)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("remote_path", "source_exists", "error_type"),
    [
        pytest.param(
            "not-a-dropbox-path",
            True,
            ValueError,
            id="invalid-dropbox-path",
        ),
        pytest.param(
            "/missing.bin",
            False,
            FileNotFoundError,
            id="missing-local-source",
        ),
    ],
)
async def test_files_upload_paths_rejects_invalid_upload_items(
    client_factory,
    tmp_path: Path,
    remote_path: str,
    source_exists: bool,
    error_type: type[BaseException],
) -> None:
    source = AsyncPath(tmp_path / "fixture.bin")
    if source_exists:
        await source.write_bytes(b"content")

    async with client_factory({}) as dbx:
        with pytest.raises(error_type):
            await dbx.files_upload_paths(
                [
                    UploadPath(source, remote_path),
                ],
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "finish_batch_response",
    [
        pytest.param({}, id="missing-tag"),
        pytest.param({".tag": "unexpected"}, id="unexpected-tag"),
        pytest.param(
            {".tag": "complete"},
            id="complete-without-entries",
        ),
        pytest.param(
            {
                ".tag": "complete",
                "entries": {},
            },
            id="complete-with-non-list-entries",
        ),
        pytest.param(
            {
                ".tag": "async_job_id",
            },
            id="async-job-id-missing-value",
        ),
        pytest.param(
            {
                ".tag": "async_job_id",
                "async_job_id": 1,
            },
            id="async-job-id-non-string",
        ),
        pytest.param(
            {
                ".tag": "async_job_id",
                "async_job_id": "",
            },
            id="async-job-id-empty",
        ),
    ],
)
async def test_files_upload_paths_rejects_malformed_finish_batch_response(
    client_factory,
    tmp_path: Path,
    finish_batch_response: dict[str, Any],
) -> None:
    source = AsyncPath(tmp_path / "fixture.bin")
    await source.write_bytes(b"abc")

    async def start(request: web.Request) -> web.Response:
        assert await request.read() == b"abc"
        return web.json_response({"session_id": "session-1"})

    async def finish_batch(request: web.Request) -> web.Response:
        return web.json_response(finish_batch_response)

    async with client_factory(
        {
            "/2/files/upload_session/start": start,
            "/2/files/upload_session/finish_batch": finish_batch,
        },
    ) as dbx:
        with pytest.raises(DropboxProtocolError):
            await dbx.files_upload_paths(
                [
                    UploadPath(source, "/fixture.bin"),
                ],
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "body", "chunk_size"),
    [
        pytest.param(
            "start",
            b"abc",
            3,
            id="closed-single-chunk-start",
        ),
        pytest.param(
            "append",
            b"abcdef",
            3,
            id="closed-final-append",
        ),
    ],
)
async def test_files_upload_paths_does_not_retry_ambiguous_content_upload(
    client_factory,
    tmp_path: Path,
    operation: str,
    body: bytes,
    chunk_size: int,
) -> None:
    source = AsyncPath(tmp_path / "fixture.bin")
    await source.write_bytes(body)

    attempts = 0
    finish_batch_requests = 0

    async def failing_start(request: web.Request) -> web.Response:
        nonlocal attempts
        attempts += 1
        await request.read()
        return web.Response(status=503, text="ambiguous remote state")

    async def start(request: web.Request) -> web.Response:
        assert await request.read() == b"abc"
        return web.json_response({"session_id": "session-1"})

    async def failing_append(request: web.Request) -> web.Response:
        nonlocal attempts
        attempts += 1
        assert await request.read() == b"def"
        return web.Response(status=503, text="ambiguous remote state")

    async def finish_batch(request: web.Request) -> web.Response:
        nonlocal finish_batch_requests
        finish_batch_requests += 1
        return web.Response(status=500)

    if operation == "start":
        routes = {
            "/2/files/upload_session/start": failing_start,
            "/2/files/upload_session/finish_batch": finish_batch,
        }
    else:
        routes = {
            "/2/files/upload_session/start": start,
            "/2/files/upload_session/append_v2": failing_append,
            "/2/files/upload_session/finish_batch": finish_batch,
        }

    async with client_factory(
        routes,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0),
    ) as dbx:
        with pytest.raises(DropboxError) as caught:
            await dbx.files_upload_paths(
                [
                    UploadPath(source, "/fixture.bin"),
                ],
                chunk_size=chunk_size,
            )

    assert caught.value.status_code == 503
    assert attempts == 1
    assert finish_batch_requests == 0


@pytest.mark.asyncio
async def test_files_upload_paths_propagates_cancellation_during_append(
    client_factory,
    tmp_path: Path,
) -> None:
    source = AsyncPath(tmp_path / "fixture.bin")
    await source.write_bytes(b"abcdefghi")

    append_started = asyncio.Event()
    finish_batch_requests = 0

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

    async def finish_batch(request: web.Request) -> web.Response:
        nonlocal finish_batch_requests
        finish_batch_requests += 1
        return web.Response(status=500)

    async with client_factory(
        {
            "/2/files/upload_session/start": start,
            "/2/files/upload_session/append_v2": append,
            "/2/files/upload_session/finish_batch": finish_batch,
        },
    ) as dbx:
        task = asyncio.create_task(
            dbx.files_upload_paths(
                [
                    UploadPath(source, "/fixture.bin"),
                ],
                chunk_size=3,
            )
        )

        await append_started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    assert finish_batch_requests == 0


@pytest.mark.asyncio
async def test_files_upload_paths_retries_transient_finish_batch_check(
    client_factory,
    tmp_path: Path,
) -> None:
    source = AsyncPath(tmp_path / "fixture.bin")
    await source.write_bytes(b"abc")

    checks = 0

    async def start(request: web.Request) -> web.Response:
        return web.json_response({"session_id": "session-1"})

    async def finish_batch(request: web.Request) -> web.Response:
        return web.json_response(
            {
                ".tag": "async_job_id",
                "async_job_id": "job-1",
            }
        )

    async def finish_batch_check(request: web.Request) -> web.Response:
        nonlocal checks
        checks += 1

        assert await request.json() == {
            "async_job_id": "job-1",
        }

        if checks == 1:
            return web.Response(status=503, text="temporarily unavailable")

        return web.json_response(
            {
                ".tag": "complete",
                "entries": [],
            }
        )

    async with client_factory(
        {
            "/2/files/upload_session/start": start,
            "/2/files/upload_session/finish_batch": finish_batch,
            "/2/files/upload_session/finish_batch/check": finish_batch_check,
        },
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0),
    ) as dbx:
        result = await dbx.files_upload_paths(
            [
                UploadPath(source, "/fixture.bin"),
            ],
        )

    assert checks == 2
    assert result == {
        ".tag": "complete",
        "entries": [],
    }
