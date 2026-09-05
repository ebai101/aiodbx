from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, TypeAlias

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
        """Return metadata for a Dropbox file or folder."""
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
        """Return one page of entries in a Dropbox folder."""
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

        return await self._transport.rpc("/2/files/list_folder", arg)

    async def list_folder_continue(self, cursor: str) -> dict[str, Any]:
        """Return the next page from a ``list_folder`` cursor."""
        return await self._transport.rpc(
            "/2/files/list_folder/continue",
            {"cursor": cursor},
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
        """Stream a Dropbox file download.

        The response stream must be consumed within the context manager.

        Example:
            async with dbx.files.download("/report.csv") as response:
                async for chunk in response.iter_bytes():
                    ...
        """
        validate_non_root_path(path)

        async with self._transport.content_download(
            "/2/files/download",
            {"path": path},
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
        """Download a Dropbox file to a local destination atomically.

        The method validates that a non-overwritable local destination does not
        already exist before opening the remote Dropbox response. It then streams
        to a sibling temporary file and atomically replaces the destination only
        after transfer completion.

        Returns:
            File metadata supplied by Dropbox in ``Dropbox-API-Result``.
        """
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
