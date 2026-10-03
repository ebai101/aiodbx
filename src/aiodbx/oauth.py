from __future__ import annotations

import asyncio
import base64
import hashlib
import math
import string
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeAlias
from urllib.parse import urlencode

import aiohttp

from .errors import (
    DropboxError,
    DropboxOAuthError,
    DropboxProtocolError,
    DropboxRateLimitError,
    DropboxTransportError,
)
from .hosts import EndpointHosts
from .retry import RetryPolicy


@dataclass(frozen=True, slots=True)
class _StaticToken:
    value: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class _RefreshCredentials:
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)


@dataclass(frozen=True, slots=True, eq=False)
class _AccessLease:
    value: str = field(repr=False)
    expires_at: float
    refresh_at: float

    def is_fresh(self, now: float) -> bool:
        return now < self.refresh_at


_Credential: TypeAlias = _StaticToken | _RefreshCredentials
_BearerSnapshot: TypeAlias = _StaticToken | _AccessLease


def _validate_nonempty(value: str, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string.")


def _validate_verifier(verifier: str) -> None:
    allowed = string.ascii_letters + string.digits + "-._~"
    if (
        not isinstance(verifier, str)
        or not 43 <= len(verifier) <= 128
        or any(c not in allowed for c in verifier)
    ):
        raise ValueError("code_verifier must be 43-128 unreserved ASCII characters.")


def oauth_authorization_url(
    app_key: str,
    *,
    state: str,
    redirect_uri: str | None = None,
    scopes: Sequence[str] | None = None,
    code_verifier: str | None = None,
) -> str:
    _validate_nonempty(app_key, "app_key")
    _validate_nonempty(state, "state")
    params = {
        "client_id": app_key,
        "response_type": "code",
        "token_access_type": "offline",
        "state": state,
    }
    if redirect_uri is not None:
        _validate_nonempty(redirect_uri, "redirect_uri")
        params["redirect_uri"] = redirect_uri
    if scopes is not None:
        if isinstance(scopes, str) or any(
            not isinstance(s, str) or not s or any(c.isspace() for c in s)
            for s in scopes
        ):
            raise ValueError("scopes must be a sequence of non-empty scope names.")
        if scopes:
            params["scope"] = " ".join(scopes)
    if code_verifier is not None:
        _validate_verifier(code_verifier)
        challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(code_verifier.encode("ascii")).digest()
            )
            .decode("ascii")
            .rstrip("=")
        )
        params.update(code_challenge=challenge, code_challenge_method="S256")
    return "https://www.dropbox.com/oauth2/authorize?" + urlencode(params)


def _credential_from_arguments(
    access_token: str | None,
    *,
    app_key: str | None,
    app_secret: str | None,
    refresh_token: str | None,
) -> _Credential:
    if access_token is not None:
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("access_token must not be empty.")
        if any(x is not None for x in (app_key, app_secret, refresh_token)):
            raise ValueError("access_token cannot be combined with OAuth credentials.")
        return _StaticToken(access_token)
    if app_key is None or app_secret is None or refresh_token is None:
        raise ValueError("app_key, app_secret, and refresh_token are required.")
    _validate_nonempty(app_key, "app_key")
    _validate_nonempty(app_secret, "app_secret")
    _validate_nonempty(refresh_token, "refresh_token")
    return _RefreshCredentials(app_key, app_secret, refresh_token)


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _basic_auth_header(app_key: str, app_secret: str) -> str:
    credentials = f"{app_key}:{app_secret}".encode()
    return "Basic " + base64.b64encode(credentials).decode("ascii")


def _now() -> float:
    return asyncio.get_running_loop().time()


def _parse_access_lease(
    payload: Mapping[str, Any], *, started_at: float
) -> _AccessLease:
    token, lifetime, kind = (
        payload.get("access_token"),
        payload.get("expires_in"),
        payload.get("token_type"),
    )
    if (
        not isinstance(token, str)
        or not token
        or not isinstance(kind, str)
        or kind.lower() != "bearer"
        or isinstance(lifetime, bool)
        or not isinstance(lifetime, int)
        or lifetime <= 0
    ):
        raise DropboxProtocolError(
            message="Dropbox OAuth response is invalid."
        ) from None
    expires = started_at + lifetime
    refresh = expires - min(60.0, lifetime / 10.0)
    if not math.isfinite(expires) or not math.isfinite(refresh) or _now() >= refresh:
        raise DropboxProtocolError(
            message="Dropbox OAuth response is stale or invalid."
        )
    return _AccessLease(token, expires, refresh)


async def _post_grant(
    session: aiohttp.ClientSession,
    *,
    hosts: EndpointHosts,
    app_key: str,
    app_secret: str,
    form: Mapping[str, str],
) -> dict[str, Any]:
    try:
        async with session.post(
            f"{hosts.api}/oauth2/token",
            data=dict(form),
            headers={"Authorization": _basic_auth_header(app_key, app_secret)},
        ) as response:
            try:
                payload = await response.json(content_type=None)
            except Exception:
                raise DropboxProtocolError(
                    message="Dropbox OAuth response is invalid.",
                    status_code=response.status,
                ) from None
            if not isinstance(payload, dict):
                raise DropboxProtocolError(
                    message="Dropbox OAuth response is invalid.",
                    status_code=response.status,
                ) from None
            if response.status in (400, 401):
                code = payload.get("error")
                known = {
                    "invalid_grant",
                    "invalid_client",
                    "invalid_request",
                    "invalid_scope",
                    "unauthorized_client",
                    "unsupported_grant_type",
                    "access_denied",
                    "server_error",
                }
                raise DropboxOAuthError(
                    message="Dropbox OAuth grant failed.",
                    status_code=response.status,
                    error_tag=code
                    if isinstance(code, str) and code in known
                    else "unknown_oauth_error",
                ) from None
            if response.status == 429:
                raise DropboxRateLimitError(
                    message="Dropbox OAuth grant failed.",
                    status_code=429,
                    retry_after=_parse_retry_after(response.headers.get("Retry-After")),
                ) from None
            if response.status >= 500:
                raise DropboxError(
                    message="Dropbox OAuth grant failed.", status_code=response.status
                ) from None
            if response.status < 200 or response.status >= 300:
                raise DropboxProtocolError(
                    message="Dropbox OAuth response is invalid.",
                    status_code=response.status,
                ) from None
            return payload
    except asyncio.CancelledError:
        raise
    except DropboxError:
        raise
    except (aiohttp.ClientError, TimeoutError):
        raise DropboxTransportError(message="Dropbox OAuth transport failed.") from None


async def oauth_exchange_code(
    code: str,
    *,
    app_key: str,
    app_secret: str,
    redirect_uri: str | None = None,
    code_verifier: str | None = None,
    _hosts: EndpointHosts | None = None,
) -> str:
    for value, name in (
        (code, "code"),
        (app_key, "app_key"),
        (app_secret, "app_secret"),
    ):
        _validate_nonempty(value, name)
    if redirect_uri is not None:
        _validate_nonempty(redirect_uri, "redirect_uri")
    if code_verifier is not None:
        _validate_verifier(code_verifier)
    form = {"grant_type": "authorization_code", "code": code}
    if redirect_uri is not None:
        form["redirect_uri"] = redirect_uri
    if code_verifier is not None:
        form["code_verifier"] = code_verifier
    timeout = aiohttp.ClientTimeout(total=120, connect=10, sock_read=90)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        started = _now()
        payload = await _post_grant(
            session,
            hosts=_hosts or EndpointHosts(),
            app_key=app_key,
            app_secret=app_secret,
            form=form,
        )
        _parse_access_lease(payload, started_at=started)
        token = payload.get("refresh_token")
        if not isinstance(token, str) or not token:
            raise DropboxProtocolError(
                message="Dropbox OAuth response has no refresh token."
            ) from None
        return token


class _TokenManager:
    def __init__(
        self,
        *,
        credentials: _RefreshCredentials,
        session: aiohttp.ClientSession,
        hosts: EndpointHosts,
        retry_policy: RetryPolicy,
    ) -> None:
        self._credentials = credentials
        self._session = session
        self._hosts = hosts
        self._retry_policy = retry_policy
        self._lease: _AccessLease | None = None
        self._flight: asyncio.Task[_AccessLease] | None = None
        self._closed = False

    async def acquire(self) -> _AccessLease:
        if self._closed:
            raise RuntimeError("OAuth token owner is closed.")
        if self._lease is not None and self._lease.is_fresh(_now()):
            return self._lease
        if self._flight is None or self._flight.done():
            self._flight = asyncio.create_task(self._refresh())
            self._flight.add_done_callback(self._observe_completion)
        return await asyncio.shield(self._flight)

    async def after_expired_rejection(self, rejected: _AccessLease) -> _AccessLease:
        if self._closed:
            raise RuntimeError("OAuth token owner is closed.")
        if self._lease is rejected:
            self._lease = None
        return await self.acquire()

    async def _refresh(self) -> _AccessLease:
        credentials = self._credentials
        form = {
            "grant_type": "refresh_token",
            "refresh_token": credentials.refresh_token,
        }
        for attempt in range(1, self._retry_policy.max_attempts + 1):
            started = _now()
            try:
                payload = await _post_grant(
                    self._session,
                    hosts=self._hosts,
                    app_key=credentials.app_key,
                    app_secret=credentials.app_secret,
                    form=form,
                )
            except DropboxError as error:
                if attempt >= self._retry_policy.max_attempts or not (
                    isinstance(error, DropboxTransportError)
                    or self._retry_policy.should_retry_status(error.status_code or 0)
                ):
                    raise
                await asyncio.sleep(
                    self._retry_policy.delay_for_attempt(
                        attempt, retry_after=error.retry_after
                    )
                )
                continue
            lease = _parse_access_lease(payload, started_at=started)
            if self._closed:
                raise RuntimeError("OAuth token owner is closed.")
            self._lease = lease
            return lease
        raise AssertionError("Retry loop exited unexpectedly.")

    def _observe_completion(self, task: asyncio.Task[_AccessLease]) -> None:
        if not task.cancelled():
            task.exception()

    async def aclose(self) -> None:
        self._closed = True
        task = self._flight
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._lease = None
        self._flight = None
