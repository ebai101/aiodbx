from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class DropboxError(Exception):
    """Base class for Dropbox API, response-protocol, and transport failures.

    Attributes:
        message: A stable, human-readable library error message.
        status_code: HTTP response status, when Dropbox returned a response.
        error_summary: Dropbox's machine-oriented error summary, if supplied.
        error_tag: The outer Dropbox tagged-union error tag, if available.
        request_id: Dropbox request identifier, useful in support requests.
        retry_after: Suggested retry delay in seconds, if supplied by Dropbox.
        response_body: Unparsed response body retained for programmatic debugging.
        error_payload: Parsed response object retained for programmatic debugging.
    """

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
        if self.error_summary:
            details.append(f"error={self.error_summary}")
        if self.error_tag:
            details.append(f"tag={self.error_tag}")
        if self.request_id:
            details.append(f"request_id={self.request_id}")
        if self.retry_after is not None:
            details.append(f"retry_after={self.retry_after:g}s")

        if not details:
            return self.message

        return f"{self.message} ({', '.join(details)})"

    def diagnostic_details(self) -> dict[str, object]:
        """Return safe structured details suitable for application diagnostics.

        This deliberately excludes ``response_body``: a body can be large or
        may contain application-sensitive information. Consumers that need it
        can inspect ``response_body`` explicitly.
        """
        return {
            "status_code": self.status_code,
            "error_summary": self.error_summary,
            "error_tag": self.error_tag,
            "request_id": self.request_id,
            "retry_after": self.retry_after,
        }


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
