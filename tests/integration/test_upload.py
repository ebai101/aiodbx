from __future__ import annotations

from uuid import uuid4

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


@pytest.mark.asyncio
async def test_upload_session_finish_uploads_multiple_blocks(
    dropbox_test_token: str,
    dropbox_live_write_root: str,
) -> None:
    first = b"upload-session-first-block-"
    last = b"upload-session-final-block"
    payload = first + last
    remote_path = f"{dropbox_live_write_root}/upload-session-{uuid4().hex}.bin"

    hasher = DropboxContentHasher()
    hasher.update(payload)

    async with AsyncDropbox(dropbox_test_token) as dbx:
        try:
            started = await require_dropbox(
                "files_upload_session_start",
                dbx.files_upload_session_start(first),
            )
            session_id = started["session_id"]

            digest = hasher.hexdigest()
            metadata = await require_dropbox(
                "files_upload_session_finish",
                dbx.files_upload_session_finish(
                    {
                        "session_id": session_id,
                        "offset": len(first),
                    },
                    {
                        "path": remote_path,
                        "mode": "overwrite",
                        "autorename": False,
                        "mute": False,
                        "strict_conflict": False,
                        "content_hash": digest,
                    },
                    last,
                ),
            )

            assert metadata["path_lower"] == remote_path.lower()
            assert metadata["size"] == len(payload)
            assert metadata["content_hash"] == digest
        finally:
            await require_dropbox(
                "files_delete_v2",
                dbx.files_delete_v2(remote_path),
            )
