from __future__ import annotations

from dataclasses import dataclass
from typing import Self

import aiohttp

from aiodbx.hosts import EndpointHosts

from .files import FilesNamespace
from .retry import RetryPolicy
from .transport import DropboxTransport
from .users import UsersNamespace


@dataclass(frozen=True, slots=True)
class ClientConfig:
    """Connection and timeout settings for :class:`AsyncDropbox`."""

    max_connections: int = 16
    max_connections_per_host: int = 8
    connect_timeout: float = 10.0
    read_timeout: float = 90.0
    total_timeout: float = 120.0
    dns_cache_ttl: int = 300

    def __post_init__(self) -> None:
        if self.max_connections < 1:
            raise ValueError("max_connections must be at least 1.")
        if self.max_connections_per_host < 1:
            raise ValueError("max_connections_per_host must be at least 1.")
        if self.connect_timeout <= 0:
            raise ValueError("connect_timeout must be greater than 0.")
        if self.read_timeout <= 0:
            raise ValueError("read_timeout must be greater than 0.")
        if self.total_timeout <= 0:
            raise ValueError("total_timeout must be greater than 0.")
        if self.dns_cache_ttl < 0:
            raise ValueError("dns_cache_ttl must not be negative.")


class AsyncDropbox:
    """An asyncio-native Dropbox API v2 client.

    The client owns an ``aiohttp.ClientSession``. Prefer using it as an async
    context manager so its connection pool is reliably closed.
    """

    def __init__(
        self,
        access_token: str,
        *,
        config: ClientConfig | None = None,
        retry_policy: RetryPolicy | None = None,
        _hosts: EndpointHosts | None = None,
    ) -> None:
        if not access_token:
            raise ValueError("access_token must not be empty.")

        self._access_token = access_token
        self._config = config or ClientConfig()
        self._retry_policy = retry_policy or RetryPolicy()
        self._session: aiohttp.ClientSession | None = None
        self._transport: DropboxTransport | None = None
        self._users: UsersNamespace | None = None
        self._files: FilesNamespace | None = None
        self._hosts = _hosts or EndpointHosts()

    @property
    def users(self) -> UsersNamespace:
        """The Dropbox users namespace.

        Raises:
            RuntimeError: If the client has not been started.
        """
        self._require_started()
        assert self._users is not None
        return self._users

    @property
    def files(self) -> FilesNamespace:
        """The Dropbox files namespace.

        Raises:
            RuntimeError: If the client has not been started.
        """
        self._require_started()
        assert self._files is not None
        return self._files

    async def start(self) -> None:
        """Open the underlying HTTP session.

        This method is idempotent. Calling it after the client is already
        started does nothing.
        """
        if self._session is not None:
            return

        timeout = aiohttp.ClientTimeout(
            total=self._config.total_timeout,
            connect=self._config.connect_timeout,
            sock_connect=self._config.connect_timeout,
            sock_read=self._config.read_timeout,
        )
        connector = aiohttp.TCPConnector(
            limit=self._config.max_connections,
            limit_per_host=self._config.max_connections_per_host,
            ttl_dns_cache=self._config.dns_cache_ttl,
        )
        session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            raise_for_status=False,
        )
        transport = DropboxTransport(
            session=session,
            access_token=self._access_token,
            retry_policy=self._retry_policy,
            hosts=self._hosts,
        )

        self._session = session
        self._transport = transport
        self._users = UsersNamespace(transport)
        self._files = FilesNamespace(transport)

    async def aclose(self) -> None:
        """Close the underlying HTTP session.

        This method is idempotent. The client may be started again after it has
        been closed.
        """
        if self._session is not None:
            await self._session.close()

        self._session = None
        self._transport = None
        self._users = None
        self._files = None

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    def _require_started(self) -> None:
        if self._transport is None:
            raise RuntimeError(
                "Client is not started. Use 'async with AsyncDropbox(...) as dbx' "
                "or call 'await dbx.start()' before making requests."
            )
