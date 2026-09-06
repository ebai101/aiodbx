from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, TypeAlias

import aiohttp

from .downloads import DownloadResponse
from .errors import (
    DropboxAuthenticationError,
    DropboxConflictError,
    DropboxError,
    DropboxNotFoundError,
    DropboxPermissionError,
    DropboxProtocolError,
    DropboxRateLimitError,
    DropboxTransportError,
)
from .hosts import EndpointHosts
from .retry import RetryPolicy

ContentBody: TypeAlias = bytes | bytearray | memoryview


class DropboxTransport:
    """Private HTTP transport for Dropbox JSON-RPC endpoints."""

    def __init__(
        self,
        *,
        session: aiohttp.ClientSession,
        access_token: str,
        retry_policy: RetryPolicy,
        hosts: EndpointHosts,
    ) -> None:
        self._session = session
        self._access_token = access_token
        self._retry_policy = retry_policy
        self._hosts = hosts

    async def rpc(
        self,
        path: str,
        arg: Mapping[str, Any],
        *,
        retryable: bool = False,
    ) -> dict[str, Any]:
        """Make a JSON RPC request to the Dropbox API host."""
        if not path.startswith("/"):
            raise ValueError("Dropbox endpoint paths must start with '/'.")

        return await self._request_json(
            url=f"{self._hosts.api}{path}",
            headers={"Content-Type": "application/json"},
            json_body=arg,
            retryable=retryable,
        )

    @asynccontextmanager
    async def content_download(
        self,
        path: str,
        arg: Mapping[str, Any],
        *,
        retryable: bool = False,
    ) -> AsyncIterator[DownloadResponse]:
        """Open a Dropbox content-download response.

        The caller must consume the returned stream inside the context manager.
        """
        if not path.startswith("/"):
            raise ValueError("Dropbox endpoint paths must start with '/'.")

        headers = self._content_headers(arg)

        async with self._request_stream(
            url=f"{self._hosts.content}{path}",
            headers=headers,
            retryable=retryable,
        ) as response:
            metadata = await self._read_download_metadata(response)
            yield DownloadResponse(metadata=metadata, _response=response)

    async def content_upload(
        self,
        path: str,
        arg: Mapping[str, Any],
        *,
        data: ContentBody,
        retryable: bool = False,
    ) -> dict[str, Any]:
        """Call a Dropbox content-upload endpoint and return its JSON result."""
        if not path.startswith("/"):
            raise ValueError("Dropbox endpoint paths must start with '/'.")

        return await self._request_json(
            url=f"{self._hosts.content}{path}",
            headers=self._content_headers(
                arg,
                content_type="application/octet-stream",
            ),
            data=data,
            retryable=retryable,
        )

    async def content_upload_empty(
        self,
        path: str,
        arg: Mapping[str, Any],
        data: ContentBody,
        *,
        retryable: bool = False,
    ) -> None:
        """Call a content-upload endpoint that returns an empty success body."""
        if not path.startswith("/2/"):
            raise ValueError("Dropbox endpoint paths must start with '/2/'.")

        await self._request_empty(
            url=f"{self._hosts.content}{path}",
            headers=self._content_headers(
                arg,
                content_type="application/octet-stream",
            ),
            data=data,
            retryable=retryable,
        )

    async def _request_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None = None,
        data: ContentBody | None = None,
        retryable: bool,
    ) -> dict[str, Any]:
        if (json_body is None) == (data is None):
            raise ValueError("Pass exactly one of json_body or data.")

        request_headers = self._authorization_headers() | dict(headers)

        for attempt in range(1, self._retry_policy.max_attempts + 1):
            try:
                async with self._session.post(
                    url,
                    headers=request_headers,
                    json=json_body,
                    data=data,
                ) as response:
                    if 200 <= response.status < 300:
                        return await self._read_json(response)

                    error = await self._build_error(response)
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, TimeoutError) as exc:
                if not retryable or attempt >= self._retry_policy.max_attempts:
                    raise DropboxTransportError(
                        message=self._transport_message(exc)
                    ) from exc

                await asyncio.sleep(self._retry_policy.delay_for_attempt(attempt))
                continue

            if (
                retryable
                and self._retry_policy.should_retry_status(error.status_code or 0)
                and attempt < self._retry_policy.max_attempts
            ):
                await asyncio.sleep(
                    self._retry_policy.delay_for_attempt(
                        attempt,
                        retry_after=error.retry_after,
                    )
                )
                continue

            raise error

        raise AssertionError("Retry loop exited unexpectedly.")

    async def _request_empty(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        data: ContentBody,
        retryable: bool,
    ) -> None:
        request_headers = self._authorization_headers() | dict(headers)

        for attempt in range(1, self._retry_policy.max_attempts + 1):
            try:
                async with self._session.post(
                    url,
                    headers=request_headers,
                    data=data,
                ) as response:
                    if 200 <= response.status < 300:
                        await response.read()
                        return

                    error = await self._build_error(response)
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, TimeoutError) as exc:
                if not retryable or attempt == self._retry_policy.max_attempts:
                    raise DropboxTransportError(
                        message=self._transport_message(exc)
                    ) from exc

                await asyncio.sleep(self._retry_policy.delay_for_attempt(attempt))
                continue

            if (
                retryable
                and self._retry_policy.should_retry_status(error.status_code or 0)
                and attempt < self._retry_policy.max_attempts
            ):
                await asyncio.sleep(
                    self._retry_policy.delay_for_attempt(
                        attempt,
                        retry_after=error.retry_after,
                    )
                )
                continue

            raise error

        raise AssertionError("Retry loop exited unexpectedly.")

    @asynccontextmanager
    async def _request_stream(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        retryable: bool,
    ) -> AsyncIterator[aiohttp.ClientResponse]:
        for attempt in range(1, self._retry_policy.max_attempts + 1):
            try:
                response = await self._session.post(url, headers=headers)
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, TimeoutError) as exc:
                if not retryable or attempt >= self._retry_policy.max_attempts:
                    raise DropboxTransportError(
                        message=self._transport_message(exc)
                    ) from exc

                await asyncio.sleep(self._retry_policy.delay_for_attempt(attempt))
                continue

            if 200 <= response.status < 300:
                try:
                    yield response
                finally:
                    response.close()
                return

            try:
                error = await self._build_error(response)
            except asyncio.CancelledError:
                response.close()
                raise
            except (aiohttp.ClientError, TimeoutError) as exc:
                response.close()

                if not retryable or attempt >= self._retry_policy.max_attempts:
                    raise DropboxTransportError(
                        message=self._transport_message(exc)
                    ) from exc

                await asyncio.sleep(self._retry_policy.delay_for_attempt(attempt))
                continue
            else:
                response.close()

            if (
                retryable
                and self._retry_policy.should_retry_status(error.status_code or 0)
                and attempt < self._retry_policy.max_attempts
            ):
                await asyncio.sleep(
                    self._retry_policy.delay_for_attempt(
                        attempt,
                        retry_after=error.retry_after,
                    )
                )
                continue

            raise error

        raise AssertionError("Retry loop exited unexpectedly.")

    async def _read_download_metadata(
        self,
        response: aiohttp.ClientResponse,
    ) -> dict[str, Any]:
        raw_metadata = response.headers.get("Dropbox-API-Result")

        if raw_metadata is None:
            raise DropboxProtocolError(
                message=(
                    "Dropbox content-download response is missing the "
                    "'Dropbox-API-Result' header."
                ),
                status_code=response.status,
                request_id=self._request_id(response),
            )

        try:
            metadata = json.loads(raw_metadata)
        except json.JSONDecodeError as exc:
            raise DropboxProtocolError(
                message=(
                    "Dropbox content-download response has an invalid "
                    "'Dropbox-API-Result' header."
                ),
                status_code=response.status,
                request_id=self._request_id(response),
            ) from exc

        if not isinstance(metadata, dict):
            raise DropboxProtocolError(
                message=(
                    "Dropbox content-download response has a non-object "
                    "'Dropbox-API-Result' value."
                ),
                status_code=response.status,
                request_id=self._request_id(response),
            )

        return metadata

    async def _read_json(
        self,
        response: aiohttp.ClientResponse,
    ) -> dict[str, Any]:
        """Decode a successful Dropbox JSON-object response."""
        body = await response.text()

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise DropboxProtocolError(
                message="Dropbox returned invalid JSON in a successful response.",
                status_code=response.status,
                request_id=self._request_id(response),
                response_body=body,
            ) from exc

        if not isinstance(payload, dict):
            raise DropboxProtocolError(
                message=(
                    "Dropbox returned a successful JSON "
                    "response that was not an object."
                ),
                status_code=response.status,
                request_id=self._request_id(response),
                response_body=body,
            )

        return payload

    async def _build_error(
        self,
        response: aiohttp.ClientResponse,
    ) -> DropboxError:
        body = await response.text()
        payload = self._parse_object_or_none(body)
        error_value = payload.get("error") if payload is not None else None

        kwargs = {
            "message": "Dropbox API request failed.",
            "status_code": response.status,
            "error_summary": self._string_or_none(
                payload.get("error_summary") if payload is not None else None
            ),
            "error_tag": self._error_tag(error_value),
            "request_id": self._request_id(response),
            "retry_after": self._parse_retry_after(response.headers.get("Retry-After")),
            "response_body": body,
            "error_payload": payload,
        }

        if response.status == 401:
            return DropboxAuthenticationError(**kwargs)
        if response.status == 403:
            return DropboxPermissionError(**kwargs)
        if response.status == 404:
            return DropboxNotFoundError(**kwargs)
        if response.status == 409:
            return DropboxConflictError(**kwargs)
        if response.status == 429:
            return DropboxRateLimitError(**kwargs)
        return DropboxError(**kwargs)

    def _content_headers(
        self, arg: Mapping[str, Any], *, content_type: str | None = None
    ) -> dict[str, str]:
        headers = self._authorization_headers()
        headers["Dropbox-API-Arg"] = json.dumps(
            arg,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        if content_type is not None:
            headers["Content-Type"] = content_type

        return headers

    def _authorization_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    @staticmethod
    def _parse_object_or_none(body: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _string_or_none(value: object) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _request_id(response: aiohttp.ClientResponse) -> str | None:
        return response.headers.get("X-Dropbox-Request-Id")

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            return None

    @staticmethod
    def _transport_message(exc: BaseException) -> str:
        message = str(exc).strip()
        if message:
            return (
                "Dropbox request failed at the transport layer: "
                f"{exc.__class__.__name__}: {message}"
            )
        return (
            f"Dropbox request failed at the transport layer: {exc.__class__.__name__}"
        )

    @classmethod
    def _error_tag(cls, error_value: object) -> str | None:
        """Return a slash-delimited Dropbox tagged-error path when available."""
        if not isinstance(error_value, dict):
            return None

        tags: list[str] = []
        current: object = error_value

        while isinstance(current, dict):
            tag = current.get(".tag")
            if not isinstance(tag, str):
                break

            tags.append(tag)
            next_value = current.get(tag)

            if not isinstance(next_value, dict):
                break

            current = next_value

        return "/".join(tags) if tags else None
