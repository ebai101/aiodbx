from __future__ import annotations

import pytest

from aiodbx import AsyncDropbox
from tests.helpers.content_hash import DropboxContentHasher
from tests.helpers.integration import require_dropbox

pytestmark = [pytest.mark.integration, pytest.mark.live_write]


@pytest.mark.asyncio
async def test_files_upload_returns_matching_metadata(
    dropbox_test_token: str,
    dropbox_live_write_root: str,
) -> None:
    payload = b"aiodbx simple upload integration test\n"
    remote_path = f"{dropbox_live_write_root}/simple-upload.txt"

    hasher = DropboxContentHasher()
    hasher.update(payload)
    expected_content_hash = hasher.hexdigest()

    async with AsyncDropbox(dropbox_test_token) as dbx:
        try:
            metadata = await require_dropbox(
                "files/upload",
                dbx.files_upload(
                    payload,
                    remote_path,
                    mode="overwrite",
                    content_hash=expected_content_hash,
                ),
            )

            assert metadata["path_lower"] == remote_path.lower()
            assert metadata["size"] == len(payload)
            assert metadata["content_hash"] == expected_content_hash
        finally:
            await require_dropbox(
                "files/delete_v2",
                dbx.files_delete_v2(remote_path),
            )
