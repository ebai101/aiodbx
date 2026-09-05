from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from anyio import Path as AsyncPath

from aiodbx.filesystem import write_download_atomically


class FailingDownload:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_bytes(
        self,
        *,
        chunk_size: int,
    ) -> AsyncIterator[bytes]:
        assert chunk_size > 0

        for chunk in self._chunks:
            yield chunk

        raise OSError("simulated local or stream failure")


@pytest.mark.asyncio
async def test_write_download_atomically_removes_partial_file_on_failure(
    tmp_path: Path,
) -> None:
    destination = AsyncPath(tmp_path / "destination.bin")
    response = FailingDownload([b"first ", b"second "])

    with pytest.raises(OSError, match="simulated"):
        await write_download_atomically(
            response,  # ty: ignore[invalid-argument-type]
            destination,
            chunk_size=3,
            overwrite=False,
        )

    assert not await destination.exists()

    partial_files = [
        path async for path in AsyncPath(tmp_path).glob(".destination.bin.*.partial")
    ]
    assert partial_files == []


@pytest.mark.asyncio
async def test_write_download_atomically_rejects_invalid_chunk_size(
    tmp_path: Path,
) -> None:
    destination = AsyncPath(tmp_path / "destination.bin")

    with pytest.raises(ValueError, match="chunk_size"):
        await write_download_atomically(
            FailingDownload([]),  # ty: ignore[invalid-argument-type]
            destination,
            chunk_size=0,
            overwrite=False,
        )
