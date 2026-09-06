from __future__ import annotations

import contextlib
from uuid import uuid4

import pytest
from anyio import Path

from aiodbx import AsyncDropbox, DropboxNotFoundError, UploadPath
from tests.helpers.content_hash import DropboxContentHasher, dropbox_content_hash
from tests.helpers.integration import require_dropbox

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_write,
    pytest.mark.asyncio,
]


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            b"aiodbx files_upload_path integration payload",
            id="small-source",
        ),
        pytest.param(
            b"",
            id="empty-source",
        ),
    ],
)
async def test_files_upload_path_returns_matching_metadata(
    dropbox_test_token: str,
    dropbox_live_write_root: str,
    tmp_path,
    payload: bytes,
) -> None:
    source = Path(tmp_path / "source.bin")
    await source.write_bytes(payload)

    remote_path = f"{dropbox_live_write_root}/upload-path-{uuid4().hex}.bin"

    hasher = DropboxContentHasher()
    hasher.update(payload)
    expected_content_hash = hasher.hexdigest()

    async with AsyncDropbox(dropbox_test_token) as dbx:
        try:
            metadata = await require_dropbox(
                "files_upload_path",
                dbx.files_upload_path(
                    source,
                    remote_path,
                    mode="overwrite",
                    content_hash=expected_content_hash,
                ),
            )

            assert metadata["path_lower"] == remote_path.lower()
            assert metadata["path_display"] == remote_path
            assert metadata["size"] == len(payload)
            assert metadata["content_hash"] == expected_content_hash

            fetched = await require_dropbox(
                "files_get_metadata",
                dbx.files_get_metadata(remote_path),
            )
            assert fetched[".tag"] == "file"
            assert fetched["size"] == len(payload)
            assert fetched["content_hash"] == expected_content_hash
        finally:
            with contextlib.suppress(DropboxNotFoundError):
                await require_dropbox(
                    "files_delete_v2",
                    dbx.files_delete_v2(remote_path),
                )


async def test_files_upload_paths_batch_commits_matching_files(
    dropbox_test_token: str,
    dropbox_live_write_root: str,
    tmp_path,
) -> None:
    first_payload = b"first batch source"
    second_payload = b"second batch source with more bytes"

    first_source = Path(tmp_path / "first.bin")
    second_source = Path(tmp_path / "second.bin")
    await first_source.write_bytes(first_payload)
    await second_source.write_bytes(second_payload)

    prefix = f"{dropbox_live_write_root}/upload-paths-{uuid4().hex}"
    first_remote_path = f"{prefix}-first.bin"
    second_remote_path = f"{prefix}-second.bin"

    uploads = [
        UploadPath(first_source, first_remote_path),
        UploadPath(second_source, second_remote_path),
    ]

    expected_hashes = {
        first_remote_path: await dropbox_content_hash(first_source),
        second_remote_path: await dropbox_content_hash(second_source),
    }
    expected_sizes = {
        first_remote_path: len(first_payload),
        second_remote_path: len(second_payload),
    }

    async with AsyncDropbox(dropbox_test_token) as dbx:
        try:
            result = await require_dropbox(
                "files_upload_paths",
                dbx.files_upload_paths(
                    uploads,
                    mode="overwrite",
                    chunk_size=3,
                ),
            )

            assert result[".tag"] == "complete"

            entries = result["entries"]
            assert isinstance(entries, list)
            assert len(entries) == len(uploads)

            for entry in entries:
                assert entry[".tag"] == "success"

            for remote_path in (first_remote_path, second_remote_path):
                metadata = await require_dropbox(
                    "files_get_metadata",
                    dbx.files_get_metadata(remote_path),
                )

                assert metadata["path_lower"] == remote_path.lower()
                assert metadata["path_display"] == remote_path
                assert metadata["size"] == expected_sizes[remote_path]
                assert metadata["content_hash"] == expected_hashes[remote_path]
        finally:
            for remote_path in (first_remote_path, second_remote_path):
                with contextlib.suppress(DropboxNotFoundError):
                    await require_dropbox(
                        "files_delete_v2",
                        dbx.files_delete_v2(remote_path),
                    )
