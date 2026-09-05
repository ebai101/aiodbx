from __future__ import annotations

import hashlib
from os import PathLike
from typing import Final

from anyio import Path

_BLOCK_SIZE: Final = 4 * 1024 * 1024
LocalPath = str | PathLike[str] | Path


class DropboxContentHasher:
    """Incrementally calculate Dropbox's ``content_hash`` value.

    Dropbox splits content into 4 MiB blocks, SHA-256 hashes each block, then
    SHA-256 hashes the concatenation of those block digests.

    Calling ``digest()`` or ``hexdigest()`` finalizes the instance. Use
    ``copy()`` first if the hasher must remain usable afterwards.
    """

    block_size: Final[int] = _BLOCK_SIZE
    digest_size: Final[int] = hashlib.sha256().digest_size

    def __init__(self) -> None:
        self._overall_hasher: hashlib._Hash | None = hashlib.sha256()
        self._block_hasher: hashlib._Hash | None = hashlib.sha256()
        self._block_pos = 0

    def update(self, data: bytes | bytearray | memoryview) -> None:
        """Add bytes to the content hash."""
        if self._overall_hasher is None or self._block_hasher is None:
            raise ValueError(
                "Cannot update a finalized DropboxContentHasher. "
                "Create a new hasher or call copy() before finalizing."
            )

        view = memoryview(data)
        if view.ndim != 1 or view.format not in {"B", "b", "c"}:
            raise TypeError("data must be a one-dimensional bytes-like object.")

        position = 0
        while position < len(view):
            if self._block_pos == self.block_size:
                self._overall_hasher.update(self._block_hasher.digest())
                self._block_hasher = hashlib.sha256()
                self._block_pos = 0

            remaining = self.block_size - self._block_pos
            part = view[position : position + remaining]

            self._block_hasher.update(part)
            self._block_pos += len(part)
            position += len(part)

    def digest(self) -> bytes:
        """Finalize and return the raw 32-byte digest."""
        return self._finish().digest()

    def hexdigest(self) -> str:
        """Finalize and return the lowercase hexadecimal Dropbox content hash."""
        return self._finish().hexdigest()

    def copy(self) -> DropboxContentHasher:
        """Return an independent copy of this unfinished hasher."""
        if self._overall_hasher is None or self._block_hasher is None:
            raise ValueError("Cannot copy a finalized DropboxContentHasher.")

        copied = type(self).__new__(type(self))
        copied._overall_hasher = self._overall_hasher.copy()
        copied._block_hasher = self._block_hasher.copy()
        copied._block_pos = self._block_pos
        return copied

    def _finish(self) -> hashlib._Hash:
        if self._overall_hasher is None or self._block_hasher is None:
            raise ValueError("Cannot finalize a DropboxContentHasher more than once.")

        if self._block_pos > 0:
            self._overall_hasher.update(self._block_hasher.digest())

        overall_hasher = self._overall_hasher
        self._overall_hasher = None
        self._block_hasher = None
        return overall_hasher


async def dropbox_content_hash(
    path: LocalPath,
    *,
    read_size: int = 1024 * 1024,
) -> str:
    """Return Dropbox's content hash for a local file.

    ``read_size`` need not divide 4 MiB. DropboxContentHasher handles block
    boundaries independently from filesystem read boundaries.
    """
    if read_size < 1:
        raise ValueError("read_size must be at least 1.")

    source = Path(path)
    hasher = DropboxContentHasher()

    async with await source.open("rb") as file:
        while chunk := await file.read(read_size):
            hasher.update(chunk)

    return hasher.hexdigest()
