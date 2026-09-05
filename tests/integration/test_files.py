from __future__ import annotations

import contextlib
from uuid import uuid4

import pytest

from aiodbx import AsyncDropbox, DropboxNotFoundError
from tests.helpers.integration import require_dropbox, require_dropbox_iter

pytestmark = [pytest.mark.integration, pytest.mark.live_write]


@pytest.mark.asyncio
async def test_get_metadata_for_configured_folder(
    dropbox_test_token: str,
    dropbox_test_folder_path: str,
) -> None:
    async with AsyncDropbox(dropbox_test_token) as dbx:
        metadata = await require_dropbox(
            "files/get_metadata", dbx.files_get_metadata(dropbox_test_folder_path)
        )

    assert metadata[".tag"] == "folder"
    assert isinstance(metadata.get("id"), str)
    assert metadata["path_lower"] == dropbox_test_folder_path.lower()


@pytest.mark.asyncio
async def test_root_listing_has_a_cursor_and_entries_list(
    dropbox_test_token: str,
) -> None:
    async with AsyncDropbox(dropbox_test_token) as dbx:
        page = await require_dropbox("files/list_folder", dbx.files_list_folder(""))

    assert isinstance(page.get("entries"), list)
    assert isinstance(page.get("cursor"), str)
    assert isinstance(page.get("has_more"), bool)


@pytest.mark.asyncio
async def test_iter_folder_yields_valid_entries(
    dropbox_test_token: str,
) -> None:
    seen = 0

    async with AsyncDropbox(dropbox_test_token) as dbx:
        async for entry in require_dropbox_iter(
            "files/list_folder_iter",
            dbx.files_list_folder_iter("", recursive=True, limit=25),
        ):
            assert entry[".tag"] in {"file", "folder", "deleted"}
            seen += 1

            if seen == 100:
                break

    assert seen > 0


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
