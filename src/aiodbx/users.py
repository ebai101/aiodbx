from __future__ import annotations

from typing import Any

from .transport import DropboxTransport


class UsersNamespace:
    """Dropbox user/account endpoints."""

    def __init__(self, transport: DropboxTransport) -> None:
        self._transport = transport

    async def get_current_account(self) -> dict[str, Any]:
        """Return metadata for the account associated with this access token."""
        return await self._transport.rpc("/2/users/get_current_account", {})
