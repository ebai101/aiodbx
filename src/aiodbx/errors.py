from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class DropboxError(Exception):
    """Base class for Dropbox API and protocol failures."""

    message: str
    status_code: int | None = None
    error_summary: str | None = None
    error_tag: str | None = None
    request_id: str | None = None
    retry_after: float | None = None
    response_body: str | None = None
    error_payload: dict[str, Any] | None = None

    def __str__(self) -> str:
        details: list[str] = []

        if self.status_code is not None:
            details.append(f"status={self.status_code}")
        if self.error_summary is not None:
            details.append(f"error={self.error_summary}")
        if self.error_tag is not None:
            details.append(f"tag={self.error_tag}")
        if self.request_id is not None:
            details.append(f"request_id={self.request_id}")

        if not details:
            return self.message
        return f"{self.message} ({', '.join(details)})"


class DropboxAuthenticationError(DropboxError):
    """Dropbox rejected the supplied access token or authorization."""


class DropboxPermissionError(DropboxError):
    """The account is not permitted to perform the requested operation."""


class DropboxNotFoundError(DropboxError):
    """The requested Dropbox resource was not found."""


class DropboxConflictError(DropboxError):
    """The requested operation conflicts with Dropbox resource state."""


class DropboxRateLimitError(DropboxError):
    """Dropbox throttled the request."""


class DropboxTransportError(DropboxError):
    """A local network, connection, or timeout failure occurred."""


class DropboxProtocolError(DropboxError):
    """Dropbox returned a successful response with an unexpected shape."""
