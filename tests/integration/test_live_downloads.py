import pytest
from anyio import Path

from aiodbx import AsyncDropbox

from ..helpers.content_hash import dropbox_content_hash
from ..helpers.integration import require_dropbox

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_download_matches_dropbox_content_hash(
    dropbox_test_token: str,
    dropbox_test_download_file_path: str,
    tmp_path,
) -> None:
    destination = Path(tmp_path) / "download_test"

    async with AsyncDropbox(dropbox_test_token) as dbx:
        metadata = await require_dropbox(
            "files/download",
            dbx.files_download_to_path(
                dropbox_test_download_file_path,
                destination,
                chunk_size=257 * 1024,
            ),
        )

    assert metadata["name"] == "download_test"
    assert metadata["size"] == (await destination.stat()).st_size
    assert metadata["content_hash"] == await dropbox_content_hash(
        destination,
        read_size=333_333,
    )
