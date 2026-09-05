from __future__ import annotations

from os import PathLike
from uuid import uuid4

from anyio import Path

from .downloads import DownloadResponse

LocalPath = str | PathLike[str] | Path


async def write_download_atomically(
    response: DownloadResponse,
    destination: Path,
    *,
    chunk_size: int,
    overwrite: bool,
) -> Path:
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1.")

    final_path = destination

    if await final_path.exists() and not overwrite:
        raise FileExistsError(final_path)

    await final_path.parent.mkdir(parents=True, exist_ok=True)

    partial_path = final_path.with_name(f".{final_path.name}.{uuid4().hex}.partial")

    try:
        async with await partial_path.open("xb") as file:
            async for chunk in response.iter_bytes(chunk_size=chunk_size):
                await file.write(chunk)

        if await final_path.exists() and not overwrite:
            raise FileExistsError(final_path)

        await partial_path.replace(final_path)
        return final_path
    except BaseException:
        try:
            await partial_path.unlink(missing_ok=True)
        finally:
            raise


async def ensure_destination_available(
    destination: LocalPath,
    *,
    overwrite: bool,
) -> Path:
    """Normalize a local destination and reject an existing file if needed."""
    final_path = Path(destination)

    if await final_path.exists() and not overwrite:
        raise FileExistsError(final_path)

    return final_path
