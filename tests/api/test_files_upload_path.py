import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from anyio import Path as AsyncPath

from aiodbx import DropboxError, DropboxProtocolError, RetryPolicy


@pytest.mark.asyncio
async def test_files_upload_path_uses_simple_upload_for_small_file(
    client_factory,
    tmp_path: Path,
) -> None:
    source = AsyncPath(tmp_path / "small.bin")
    body = b"small upload"

    await source.write_bytes(body)

    async def upload(request: web.Request) -> web.Response:
        assert json.loads(request.headers["Dropbox-API-Arg"]) == {
            "path": "/small.bin",
            "mode": "overwrite",
            "autorename": False,
            "mute": False,
            "strict_conflict": False,
        }
        assert await request.read() == body
        return web.json_response(
            {
                ".tag": "file",
                "name": "small.bin",
                "path_display": "/small.bin",
                "size": len(body),
            }
        )

    async with client_factory(
        {"/2/files/upload": upload},
        api_host=False,
    ) as dbx:
        result = await dbx.files_upload_path(
            source,
            "/small.bin",
            mode="overwrite",
        )

    assert result["size"] == len(body)


@pytest.mark.asyncio
async def test_files_upload_path_streams_large_file_through_session(
    client_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("aiodbx.files.SIMPLE_UPLOAD_MAX_BYTES", 3)

    source = AsyncPath(tmp_path / "large.bin")
    await source.write_bytes(b"abcdefgh")

    observed: list[tuple[str, dict[str, object], bytes]] = []

    async def start(request: web.Request) -> web.Response:
        observed.append(
            (
                "start",
                json.loads(request.headers["Dropbox-API-Arg"]),
                await request.read(),
            )
        )
        return web.json_response({"session_id": "session-1"})

    async def append(request: web.Request) -> web.Response:
        observed.append(
            (
                "append",
                json.loads(request.headers["Dropbox-API-Arg"]),
                await request.read(),
            )
        )
        return web.Response(status=200)

    async def finish(request: web.Request) -> web.Response:
        observed.append(
            (
                "finish",
                json.loads(request.headers["Dropbox-API-Arg"]),
                await request.read(),
            )
        )
        return web.json_response(
            {
                ".tag": "file",
                "name": "large.bin",
                "path_display": "/large.bin",
                "size": 8,
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
        result = await dbx.files_upload_path(
            source,
            "/large.bin",
            mode="overwrite",
            chunk_size=3,
        )

    assert result["size"] == 8
    assert observed == [
        ("start", {"close": False}, b"abc"),
        (
            "append",
            {
                "cursor": {"session_id": "session-1", "offset": 3},
                "close": False,
            },
            b"def",
        ),
        (
            "finish",
            {
                "cursor": {"session_id": "session-1", "offset": 6},
                "commit": {
                    "path": "/large.bin",
                    "mode": "overwrite",
                    "autorename": False,
                    "mute": False,
                    "strict_conflict": False,
                },
            },
            b"gh",
        ),
    ]


@pytest.mark.asyncio
async def test_files_upload_path_does_not_retry_an_ambiguous_append(
    client_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("aiodbx.files.SIMPLE_UPLOAD_MAX_BYTES", 3)

    source = AsyncPath(tmp_path / "large.bin")
    await source.write_bytes(b"abcdefghi")

    append_attempts = 0

    async def start(request: web.Request) -> web.Response:
        assert await request.read() == b"abc"
        return web.json_response({"session_id": "session-1"})

    async def append(request: web.Request) -> web.Response:
        nonlocal append_attempts
        append_attempts += 1
        assert await request.read() == b"def"
        return web.Response(status=503, text="ambiguous remote state")

    async with client_factory(
        {
            "/2/files/upload_session/start": start,
            "/2/files/upload_session/append_v2": append,
        },
        api_host=False,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0),
    ) as dbx:
        with pytest.raises(DropboxError) as caught:
            await dbx.files_upload_path(source, "/large.bin", chunk_size=3)

    assert caught.value.status_code == 503
    assert append_attempts == 1


@pytest.mark.asyncio
async def test_files_upload_path_does_not_retry_an_ambiguous_finish(
    client_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("aiodbx.files.SIMPLE_UPLOAD_MAX_BYTES", 3)

    source = AsyncPath(tmp_path / "large.bin")
    await source.write_bytes(b"abcdef")

    finish_attempts = 0

    async def start(request: web.Request) -> web.Response:
        assert await request.read() == b"abc"
        return web.json_response({"session_id": "session-1"})

    async def finish(request: web.Request) -> web.Response:
        nonlocal finish_attempts
        finish_attempts += 1

        assert json.loads(request.headers["Dropbox-API-Arg"]) == {
            "cursor": {
                "session_id": "session-1",
                "offset": 3,
            },
            "commit": {
                "path": "/large.bin",
                "mode": "add",
                "autorename": False,
                "mute": False,
                "strict_conflict": False,
            },
        }
        assert await request.read() == b"def"
        return web.Response(status=503, text="ambiguous remote state")

    async with client_factory(
        {
            "/2/files/upload_session/start": start,
            "/2/files/upload_session/finish": finish,
        },
        api_host=False,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0),
    ) as dbx:
        with pytest.raises(DropboxError) as caught:
            await dbx.files_upload_path(
                source,
                "/large.bin",
                chunk_size=3,
            )

    assert caught.value.status_code == 503
    assert finish_attempts == 1


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
            True,
            "/fixture.bin",
            TypeError,
            True,
            id="boolean-chunk-size",
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
        pytest.param(
            -1,
            "/fixture.bin",
            ValueError,
            True,
            id="negative-chunk-size",
        ),
        pytest.param(
            "1024",
            "/fixture.bin",
            TypeError,
            True,
            id="non-integer-chunk-size",
        ),
        pytest.param(
            None,
            "/fixture.bin",
            TypeError,
            True,
            id="none-chunk-size",
        ),
    ],
)
async def test_files_upload_path_raises_validation_errors(
    client_factory,
    tmp_path: Path,
    chunk_size: int,
    remote_path: str,
    error_type: type[Exception],
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
@pytest.mark.parametrize("start_response", [{}, {"session_id": 1}])
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
