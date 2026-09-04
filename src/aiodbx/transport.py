from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import aiohttp

from .errors import (
    DropboxAuthenticationError,
    DropboxConflictError,
    DropboxError,
    DropboxNotFoundError,
    DropboxPermissionError,
    DropboxRateLimitError,
    DropboxTransportError,
)
from .retry import RetryPolicy

API_HOST = "https://api.dropboxapi.com"


class DropboxTransport:
    """Private HTTP transport for Dropbox JSON-RPC endpoints."""

    def __init__(
        self,
        *,
        session: aiohttp.ClientSession,
        access_token: str,
        retry_policy: RetryPolicy,
        api_host: str = API_HOST,
    ) -> None:
        self._session = session
        self._access_token = access_token
        self._retry_policy = retry_policy
        self._api_host = api_host.rstrip("/")

    async def rpc(
        self,
        path: str,
        arg: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Make a JSON RPC request to the Dropbox API host."""
        if not path.startswith("/"):
            raise ValueError("Dropbox endpoint paths must start with '/'.")

        return await self._request_json(
            url=f"{self._api_host}{path}",
            headers={"Content-Type": "application/json"},
            json_body=arg,
        )

    async def _request_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
    ) -> dict[str, Any]:
        request_headers = {
            "Authorization": f"Bearer {self._access_token}",
            **headers,
        }

        for attempt in range(1, self._retry_policy.max_attempts + 1):
            try:
                async with self._session.post(
                    url,
                    headers=request_headers,
                    json=json_body,
                ) as response:
                    if 200 <= response.status < 300:
                        return await self._read_json(response)

                    error = await self._build_error(response)
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, TimeoutError) as exc:
                if attempt >= self._retry_policy.max_attempts:
                    raise DropboxTransportError(
                        message="Dropbox request failed at the transport layer."
                    ) from exc

                await asyncio.sleep(self._retry_policy.delay_for_attempt(attempt))
                continue

            if (
                self._retry_policy.should_retry_status(error.status_code or 0)
                and attempt < self._retry_policy.max_attempts
            ):
                delay = self._retry_policy.delay_for_attempt(
                    attempt,
                    retry_after=error.retry_after,
                )
                await asyncio.sleep(delay)
                continue

            raise error

        raise AssertionError("Retry loop exited unexpectedly.")

    async def _read_json(
        self,
        response: aiohttp.ClientResponse,
    ) -> dict[str, Any]:
        body = await response.text()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise DropboxError(
                message="Dropbox returned invalid JSON.",
                status_code=response.status,
                request_id=self._request_id(response),
                response_body=body,
            ) from exc

        if not isinstance(payload, dict):
            raise DropboxError(
                message="Dropbox returned a JSON response that was not an object.",
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
        error_tag = error_value.get(".tag") if isinstance(error_value, dict) else None

        kwargs = {
            "message": "Dropbox API request failed.",
            "status_code": response.status,
            "error_summary": self._string_or_none(
                payload.get("error_summary") if payload is not None else None
            ),
            "error_tag": self._string_or_none(error_tag),
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
