from __future__ import annotations

import pytest

from aiodbx import AsyncDropbox

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_get_current_account_returns_an_account_id(
    dropbox_test_token: str,
) -> None:
    async with AsyncDropbox(dropbox_test_token) as dbx:
        account = await dbx.users.get_current_account()

    assert isinstance(account.get("account_id"), str)
    assert account["account_id"].startswith("dbid:")
    assert isinstance(account.get("name"), dict)


@pytest.mark.asyncio
async def test_get_metadata_for_configured_folder(
    dropbox_test_token: str,
    dropbox_test_folder_path: str,
) -> None:
    async with AsyncDropbox(dropbox_test_token) as dbx:
        metadata = await dbx.files.get_metadata(dropbox_test_folder_path)

    assert metadata[".tag"] == "folder"
    assert isinstance(metadata.get("id"), str)
    assert metadata["path_lower"] == dropbox_test_folder_path.lower()


@pytest.mark.asyncio
async def test_root_listing_has_a_cursor_and_entries_list(
    dropbox_test_token: str,
) -> None:
    async with AsyncDropbox(dropbox_test_token) as dbx:
        page = await dbx.files.list_folder("")

    assert isinstance(page.get("entries"), list)
    assert isinstance(page.get("cursor"), str)
    assert isinstance(page.get("has_more"), bool)


@pytest.mark.asyncio
async def test_iter_folder_matches_single_page_entries(
    dropbox_test_token: str,
) -> None:
    async with AsyncDropbox(dropbox_test_token) as dbx:
        page = await dbx.files.list_folder("")
        entries = [entry async for entry in dbx.files.iter_folder("")]

    assert entries[: len(page["entries"])] == page["entries"]
