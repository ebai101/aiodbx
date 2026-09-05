from __future__ import annotations

import contextlib
from uuid import uuid4

import pytest

from aiodbx import AsyncDropbox, DropboxNotFoundError

from ..helpers.integration import require_dropbox

pytestmark = [pytest.mark.integration, pytest.mark.live_write]


@pytest.mark.asyncio
async def test_live_write_root_exists(
    dropbox_test_token: str,
    dropbox_live_write_root: str,
) -> None:
    async with AsyncDropbox(dropbox_test_token) as dbx:
        metadata = await require_dropbox(
            "files/get_metadata",
            dbx.files_get_metadata(dropbox_live_write_root),
        )

    assert metadata[".tag"] == "folder"
    assert metadata["path_lower"] == dropbox_live_write_root.lower()


@pytest.mark.asyncio
async def test_files_create_folder_v2_creates_a_real_folder(
    dropbox_test_token: str,
    dropbox_live_write_root: str,
) -> None:
    remote_path = f"{dropbox_live_write_root}/folder-{uuid4().hex}"

    async with AsyncDropbox(dropbox_test_token) as dbx:
        try:
            resp = await require_dropbox(
                "files/create_folder_v2",
                dbx.files_create_folder_v2(remote_path),
            )
            metadata = resp["metadata"]

            assert metadata["path_lower"] == remote_path.lower()
            assert metadata["path_display"] == remote_path
            assert isinstance(metadata.get("id"), str)

            fetched = await require_dropbox(
                "files/get_metadata",
                dbx.files_get_metadata(remote_path),
            )

            assert fetched[".tag"] == "folder"
            assert fetched["id"] == metadata["id"]
        finally:
            with contextlib.suppress(DropboxNotFoundError):
                await dbx.files_delete_v2(remote_path)
