from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import aiohttp


@dataclass(slots=True)
class DownloadResponse:
    """A streaming Dropbox content-download response.

    The response is valid only inside the context manager returned by
    ``FilesNamespace.download()``. Consume the stream before leaving that
    context.
    """

    metadata: dict[str, Any]
    _response: aiohttp.ClientResponse

    async def iter_bytes(
        self,
        *,
        chunk_size: int = 1024 * 1024,
    ) -> AsyncIterator[bytes]:
        """Yield response bytes without buffering the entire file in memory."""
        if chunk_size < 1:
            raise ValueError("chunk_size must be at least 1.")

        async for chunk in self._response.content.iter_chunked(chunk_size):
            if chunk:
                yield chunk
