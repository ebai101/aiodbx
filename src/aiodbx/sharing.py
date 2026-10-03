from __future__ import annotations

from typing import Any

from .files import SharedLink, _validate_shared_link
from .transport import DropboxTransport


class SharingNamespace:
    """Dropbox sharing endpoints."""

    def __init__(self, transport: DropboxTransport) -> None:
        self._transport = transport

    async def get_shared_link_metadata(
        self,
        shared_link: SharedLink,
    ) -> dict[str, Any]:
        """Call Dropbox's ``/2/sharing/get_shared_link_metadata`` endpoint."""
        _validate_shared_link(shared_link)
        return await self._transport.rpc(
            "/2/sharing/get_shared_link_metadata",
            _build_shared_link_metadata_arg(shared_link),
            retryable=True,
        )


def _build_shared_link_metadata_arg(link: SharedLink) -> dict[str, str]:
    arg = {"url": link.url}
    if link.password is not None:
        arg["link_password"] = link.password
    return arg
