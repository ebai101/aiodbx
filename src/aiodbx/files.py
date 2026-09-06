from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, TypeAlias

from anyio import Path

from .downloads import DownloadResponse
from .errors import DropboxProtocolError
from .filesystem import (
    LocalPath,
    ensure_destination_available,
    write_download_atomically,
)
from .paths import validate_non_root_path
from .transport import DropboxTransport

ContentBytes: TypeAlias = bytes | bytearray | memoryview

SIMPLE_UPLOAD_MAX_BYTES = 150 * 1024 * 1024
DEFAULT_UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class UploadPath:
    """One local source and its Dropbox destination for files_upload_paths()."""

    source: LocalPath
    path: str


class FilesNamespace:
    """Dropbox files endpoints."""

    def __init__(self, transport: DropboxTransport) -> None:
        self._transport = transport

    async def get_metadata(
        self,
        path: str,
        *,
        include_media_info: bool = False,
        include_deleted: bool = False,
        include_has_explicit_shared_members: bool = False,
    ) -> dict[str, Any]:
        """Call Dropbox's ``/2/files/get_metadata`` endpoint."""
        validate_non_root_path(path)
        return await self._transport.rpc(
            "/2/files/get_metadata",
            {
                "path": path,
                "include_media_info": include_media_info,
                "include_deleted": include_deleted,
                "include_has_explicit_shared_members": (
                    include_has_explicit_shared_members
                ),
            },
            retryable=True,
        )

    async def list_folder(
        self,
        path: str = "",
        *,
        recursive: bool = False,
        include_media_info: bool = False,
        include_deleted: bool = False,
        include_has_explicit_shared_members: bool = False,
        include_mounted_folders: bool = True,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Call Dropbox's ``/2/files/list_folder`` endpoint."""
        arg: dict[str, Any] = {
            "path": path,
            "recursive": recursive,
            "include_media_info": include_media_info,
            "include_deleted": include_deleted,
            "include_has_explicit_shared_members": (
                include_has_explicit_shared_members
            ),
            "include_mounted_folders": include_mounted_folders,
        }
        if limit is not None:
            arg["limit"] = limit

        return await self._transport.rpc("/2/files/list_folder", arg, retryable=True)

    async def list_folder_continue(self, cursor: str) -> dict[str, Any]:
        """Call Dropbox's ``/2/files/list_folder/continue`` endpoint."""
        return await self._transport.rpc(
            "/2/files/list_folder/continue", {"cursor": cursor}, retryable=True
        )

    async def iter_folder(
        self,
        path: str = "",
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield all entries in a folder, following Dropbox pagination."""
        page = await self.list_folder(path, **kwargs)

        while True:
            entries = page.get("entries", [])
            if not isinstance(entries, list):
                raise DropboxProtocolError(
                    "Dropbox returned a non-list value for list_folder entries."
                )

            for entry in entries:
                if not isinstance(entry, dict):
                    raise DropboxProtocolError(
                        "Dropbox returned a non-object folder entry."
                    )
                yield entry

            has_more = page.get("has_more", False)
            if has_more is not True:
                return

            cursor = page.get("cursor")
            if not isinstance(cursor, str):
                raise DropboxProtocolError(
                    "Dropbox indicated additional results without a string cursor."
                )

            page = await self.list_folder_continue(cursor)

    @asynccontextmanager
    async def download(
        self,
        path: str,
    ) -> AsyncIterator[DownloadResponse]:
        """Call Dropbox's ``/2/files/download`` endpoint.

        Consume the result inside the returned async context manager.
        """
        validate_non_root_path(path)

        async with self._transport.content_download(
            "/2/files/download", {"path": path}, retryable=True
        ) as response:
            yield response

    async def download_to_path(
        self,
        path: str,
        destination: LocalPath,
        *,
        chunk_size: int = 1024 * 1024,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Download a Dropbox file to a local path atomically."""
        final_path = await ensure_destination_available(
            destination,
            overwrite=overwrite,
        )

        async with self.download(path) as response:
            await write_download_atomically(
                response,
                final_path,
                chunk_size=chunk_size,
                overwrite=overwrite,
            )
            return response.metadata

    async def upload(
        self,
        f: ContentBytes,
        path: str,
        *,
        mode: str | dict[str, Any] = "add",
        autorename: bool = False,
        client_modified: str | None = None,
        mute: bool = False,
        property_groups: list[dict[str, Any]] | None = None,
        strict_conflict: bool = False,
        content_hash: str | None = None,
    ) -> dict[str, Any]:
        """Call Dropbox's ``/2/files/upload`` endpoint."""
        validate_non_root_path(path)
        _validate_content_bytes(f)
        _validate_simple_upload_size(f)

        arg: dict[str, Any] = {
            "path": path,
            "mode": mode,
            "autorename": autorename,
            "mute": mute,
            "strict_conflict": strict_conflict,
        }
        if client_modified is not None:
            arg["client_modified"] = client_modified
        if property_groups is not None:
            arg["property_groups"] = property_groups
        if content_hash is not None:
            arg["content_hash"] = content_hash

        return await self._transport.content_upload(
            "/2/files/upload",
            arg,
            data=f,
        )

    async def upload_path(
        self,
        source: LocalPath,
        path: str,
        *,
        mode: str | dict[str, Any] = "add",
        autorename: bool = False,
        client_modified: str | None = None,
        mute: bool = False,
        property_groups: list[dict[str, Any]] | None = None,
        strict_conflict: bool = False,
        content_hash: str | None = None,
        chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE,
    ) -> dict[str, Any]:
        """Upload one local file using simple upload or a managed session."""
        validate_non_root_path(path)
        _validate_upload_chunk_size(chunk_size)

        source_path = Path(source)
        size = (await source_path.stat()).st_size

        if size <= SIMPLE_UPLOAD_MAX_BYTES:
            async with await source_path.open("rb") as file:
                content = await file.read()

            return await self.upload(
                content,
                path,
                mode=mode,
                autorename=autorename,
                client_modified=client_modified,
                mute=mute,
                property_groups=property_groups,
                strict_conflict=strict_conflict,
                content_hash=content_hash,
            )

        commit = _build_upload_commit(
            path,
            mode=mode,
            autorename=autorename,
            client_modified=client_modified,
            mute=mute,
            property_groups=property_groups,
            strict_conflict=strict_conflict,
            content_hash=content_hash,
        )

        cursor, final_chunk = await self._upload_path_to_session(
            source_path,
            chunk_size=chunk_size,
        )
        return await self.upload_session_finish(cursor, commit, final_chunk)

    async def upload_paths(
        self,
        uploads: Sequence[UploadPath],
        *,
        mode: str | dict[str, Any] = "add",
        autorename: bool = False,
        client_modified: str | None = None,
        mute: bool = False,
        property_groups: list[dict[str, Any]] | None = None,
        strict_conflict: bool = False,
        chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE,
    ) -> dict[str, Any]:
        """Stream and batch-commit multiple local files through upload sessions."""
        _validate_upload_paths(uploads)
        _validate_upload_chunk_size(chunk_size)

        entries: list[dict[str, Any]] = []

        for upload in uploads:
            validate_non_root_path(upload.path)

            source = Path(upload.source)
            await source.stat()

            cursor = await self._upload_path_to_closed_session(
                source,
                chunk_size=chunk_size,
            )
            entries.append(
                {
                    "cursor": cursor,
                    "commit": _build_upload_commit(
                        upload.path,
                        mode=mode,
                        autorename=autorename,
                        client_modified=client_modified,
                        mute=mute,
                        property_groups=property_groups,
                        strict_conflict=strict_conflict,
                        content_hash=None,
                    ),
                }
            )

        response = await self.upload_session_finish_batch(entries)
        return await self._finish_batch_until_complete(response)

    async def delete_v2(
        self,
        path: str,
        *,
        parent_rev: str | None = None,
    ) -> dict[str, Any]:
        """Call Dropbox's ``/2/files/delete_v2`` endpoint."""
        validate_non_root_path(path)

        arg: dict[str, Any] = {"path": path}
        if parent_rev is not None:
            arg["parent_rev"] = parent_rev

        return await self._transport.rpc("/2/files/delete_v2", arg)

    async def create_folder_v2(
        self,
        path: str,
        *,
        autorename: bool = False,
    ) -> dict[str, Any]:
        """Call Dropbox's ``/2/files/create_folder_v2`` endpoint."""
        validate_non_root_path(path)

        return await self._transport.rpc(
            "/2/files/create_folder_v2",
            {
                "path": path,
                "autorename": autorename,
            },
        )

    async def upload_session_start(
        self,
        f: ContentBytes,
        *,
        close: bool = False,
        session_type: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call Dropbox's /2/files/upload_session/start endpoint."""
        _validate_content_bytes(f)

        arg: dict[str, Any] = {"close": close}
        if session_type is not None:
            arg["session_type"] = session_type

        return await self._transport.content_upload(
            "/2/files/upload_session/start",
            arg,
            data=f,
        )

    async def upload_session_append_v2(
        self,
        cursor: dict[str, Any],
        f: ContentBytes,
        *,
        close: bool = False,
    ) -> None:
        """Call Dropbox's /2/files/upload_session/append_v2 endpoint."""
        _validate_upload_session_cursor(cursor)
        _validate_content_bytes(f)

        await self._transport.content_upload_empty(
            "/2/files/upload_session/append_v2",
            {
                "cursor": cursor,
                "close": close,
            },
            f,
        )

    async def upload_session_finish(
        self,
        cursor: dict[str, Any],
        commit: dict[str, Any],
        f: ContentBytes,
    ) -> dict[str, Any]:
        """Call Dropbox's /2/files/upload_session/finish endpoint."""
        _validate_upload_session_cursor(cursor)
        _validate_upload_commit_info(commit)
        _validate_content_bytes(f)

        return await self._transport.content_upload(
            "/2/files/upload_session/finish",
            {
                "cursor": cursor,
                "commit": commit,
            },
            data=f,
        )

    async def upload_session_finish_batch(
        self,
        entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Call Dropbox's /2/files/upload_session/finish_batch endpoint."""
        _validate_upload_session_finish_batch_entries(entries)

        return await self._transport.rpc(
            "/2/files/upload_session/finish_batch",
            {"entries": entries},
        )

    async def upload_session_finish_batch_check(
        self,
        async_job_id: str,
    ) -> dict[str, Any]:
        """Call Dropbox's /2/files/upload_session/finish_batch/check endpoint."""
        if not async_job_id:
            raise ValueError("async_job_id must not be empty.")

        return await self._transport.rpc(
            "/2/files/upload_session/finish_batch/check",
            {"async_job_id": async_job_id},
            retryable=True,
        )

    async def _upload_path_to_session(
        self,
        source: Path,
        *,
        chunk_size: int,
    ) -> tuple[dict[str, Any], bytes]:
        """Stream a source into an open upload session.

        Return the confirmed cursor and the final chunk, which the caller must send
        to upload_session_finish() with its commit payload. Body-bearing requests
        deliberately remain non-retryable because Dropbox may have accepted bytes
        despite a missing client response.
        """
        async with await source.open("rb") as file:
            first_chunk = await file.read(chunk_size)
            started = await self.upload_session_start(first_chunk)
            session_id = _validate_upload_session_start_result(started)
            cursor: dict[str, Any] = {
                "session_id": session_id,
                "offset": len(first_chunk),
            }

            pending = await file.read(chunk_size)

            while pending:
                following = await file.read(chunk_size)

                if not following:
                    return cursor, pending

                await self.upload_session_append_v2(cursor, pending)
                cursor["offset"] += len(pending)
                pending = following

            return cursor, b""

    async def _upload_path_to_closed_session(
        self,
        source: Path,
        *,
        chunk_size: int,
    ) -> dict[str, Any]:
        """Stream a local source into a closed Dropbox upload session.

        The returned cursor is suitable for upload_session_finish_batch(). The
        session is closed but uncommitted. Every body-bearing request remains
        non-retryable because Dropbox may have accepted its bytes.
        """
        async with await source.open("rb") as file:
            first_chunk = await file.read(chunk_size)
            pending = await file.read(chunk_size)

            if not pending:
                started = await self.upload_session_start(first_chunk, close=True)
                session_id = _validate_upload_session_start_result(started)
                return {
                    "session_id": session_id,
                    "offset": len(first_chunk),
                }

            started = await self.upload_session_start(first_chunk)
            session_id = _validate_upload_session_start_result(started)
            cursor: dict[str, Any] = {
                "session_id": session_id,
                "offset": len(first_chunk),
            }

            while True:
                following = await file.read(chunk_size)

                if not following:
                    await self.upload_session_append_v2(
                        cursor,
                        pending,
                        close=True,
                    )
                    cursor["offset"] += len(pending)
                    return cursor

                await self.upload_session_append_v2(cursor, pending)
                cursor["offset"] += len(pending)
                pending = following

    async def _finish_batch_until_complete(
        self,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        tag = response.get(".tag")
        async_job_id: str | None = None

        if tag == "async_job_id":
            value = response.get("async_job_id")
            if not isinstance(value, str) or not value:
                raise DropboxProtocolError(
                    "Dropbox upload-session finish-batch response is missing "
                    "an async_job_id."
                )
            async_job_id = value
            response = await self.upload_session_finish_batch_check(async_job_id)
            tag = response.get(".tag")

        while tag == "in_progress":
            if async_job_id is None:
                raise DropboxProtocolError(
                    "Dropbox upload-session finish-batch check returned in_progress "
                    "without an async job ID."
                )
            response = await self.upload_session_finish_batch_check(async_job_id)
            tag = response.get(".tag")

        if tag != "complete":
            raise DropboxProtocolError(
                "Dropbox upload-session finish-batch response has an unexpected tag."
            )

        entries = response.get("entries")
        if not isinstance(entries, list):
            raise DropboxProtocolError(
                "Dropbox upload-session finish-batch completion is missing entries."
            )

        return response


def _validate_content_bytes(f: ContentBytes) -> None:
    if not isinstance(f, ContentBytes):
        raise TypeError("f must be bytes, bytearray, or memoryview.")


def _validate_simple_upload_size(f: ContentBytes) -> None:
    if len(f) > SIMPLE_UPLOAD_MAX_BYTES:
        raise ValueError(
            "files_upload supports content up to 150 MiB. "
            "Use files_upload_session_start, "
            "files_upload_session_append_v2, and "
            "files_upload_session_finish for larger content."
        )


def _validate_upload_session_cursor(cursor: dict[str, Any]) -> None:
    if not isinstance(cursor, dict):
        raise TypeError("cursor must be a mapping.")

    session_id = cursor.get("session_id")
    offset = cursor.get("offset")

    if not isinstance(session_id, str) or not session_id:
        raise ValueError("cursor.session_id must be a non-empty string.")

    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("cursor.offset must be a non-negative integer.")


def _validate_upload_commit_info(commit: dict[str, Any]) -> None:
    if not isinstance(commit, dict):
        raise TypeError("commit must be a mapping.")

    path = commit.get("path")
    if not isinstance(path, str):
        raise ValueError("commit.path must be a Dropbox path string.")

    validate_non_root_path(path)


def _validate_upload_session_finish_batch_entries(
    entries: list[dict[str, Any]],
) -> None:
    if not isinstance(entries, list):
        raise TypeError("entries must be a list of upload-session finish entries.")

    if not entries:
        raise ValueError("entries must not be empty.")

    for entry in entries:
        if not isinstance(entry, dict):
            raise TypeError(
                "entries must contain upload-session finish entry mappings."
            )

        cursor = entry.get("cursor")
        commit = entry.get("commit")

        if not isinstance(cursor, dict):
            raise ValueError("each entry must include a cursor mapping.")
        if not isinstance(commit, dict):
            raise ValueError("each entry must include a commit mapping.")

        _validate_upload_session_cursor(cursor)
        _validate_upload_commit_info(commit)


def _validate_upload_chunk_size(chunk_size: int) -> None:
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool):
        raise TypeError("chunk_size must be an integer.")
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1.")


def _validate_upload_session_start_result(result: dict[str, Any]) -> str:
    session_id = result.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise DropboxProtocolError(
            "Dropbox upload-session start response is missing a session_id."
        )
    return session_id


def _validate_upload_paths(uploads: Sequence[UploadPath]) -> None:
    if not isinstance(uploads, Sequence):
        raise TypeError("uploads must be a sequence of UploadPath values.")
    if not uploads:
        raise ValueError("uploads must not be empty.")
    for upload in uploads:
        if not isinstance(upload, UploadPath):
            raise TypeError("uploads must contain UploadPath values.")


def _build_upload_commit(
    path: str,
    *,
    mode: str | dict[str, Any],
    autorename: bool,
    client_modified: str | None,
    mute: bool,
    property_groups: list[dict[str, Any]] | None,
    strict_conflict: bool,
    content_hash: str | None,
) -> dict[str, Any]:
    commit: dict[str, Any] = {
        "path": path,
        "mode": mode,
        "autorename": autorename,
        "mute": mute,
        "strict_conflict": strict_conflict,
    }

    if client_modified is not None:
        commit["client_modified"] = client_modified
    if property_groups is not None:
        commit["property_groups"] = property_groups
    if content_hash is not None:
        commit["content_hash"] = content_hash

    return commit
