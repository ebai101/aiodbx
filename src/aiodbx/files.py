from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from .errors import DropboxProtocolError
from .transport import DropboxTransport


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
