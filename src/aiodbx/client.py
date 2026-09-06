from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Self

import aiohttp

from .downloads import DownloadResponse
from .files import DEFAULT_UPLOAD_CHUNK_SIZE, FilesNamespace, UploadPath
from .filesystem import LocalPath
from .hosts import EndpointHosts
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

    Endpoint wrappers use the official Dropbox Python SDK's flattened naming
    style. For example, Dropbox's ``/2/files/list_folder`` endpoint is
    available as :meth:`files_list_folder`.

    The client owns an ``aiohttp.ClientSession``. Prefer an async context
    manager so its connection pool is reliably closed.
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
        self._hosts = _hosts or EndpointHosts()
        self._session: aiohttp.ClientSession | None = None
        self._transport: DropboxTransport | None = None
        self._users: UsersNamespace | None = None
        self._files: FilesNamespace | None = None

    async def start(self) -> None:
        """Open the underlying HTTP session if it is not already open."""
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

        This method is idempotent. The client can be started again after it is
        closed.
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

    # Direct Dropbox endpoint wrappers

    async def rpc(
        self,
        endpoint: str,
        arg: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Call an unstructured Dropbox JSON-RPC endpoint.

        This escape hatch supports Dropbox's JSON-RPC-style API endpoints on
        ``api.dropboxapi.com``. It applies the same authorization, timeout,
        retry, and error-handling behavior as named ``aiodbx`` methods.

        Args:
            endpoint: Absolute Dropbox API v2 endpoint path, for example
                ``"/2/files/move_v2"``.
            arg: JSON-object request payload for the endpoint.

        Returns:
            The decoded Dropbox JSON-object response.

        Raises:
            RuntimeError: If the client has not been started.
            ValueError: If ``endpoint`` is not an API v2 absolute path.
            DropboxError: If Dropbox rejects the request or returns an
                unexpected response.

        This method does not support content-upload, content-download, or
        long-poll endpoints. Use direct endpoint wrappers for those categories.
        """
        self._validate_rpc_endpoint(endpoint)

        if not isinstance(arg, Mapping):
            raise TypeError("arg must be a mapping representing a JSON object.")

        return await self._require_transport().rpc(endpoint, arg)

    async def users_get_current_account(self) -> dict[str, Any]:
        """Return metadata for the account associated with this access token."""
        return await self._require_users().get_current_account()

    async def files_get_metadata(
        self,
        path: str,
        *,
        include_media_info: bool = False,
        include_deleted: bool = False,
        include_has_explicit_shared_members: bool = False,
    ) -> dict[str, Any]:
        """Return metadata for a Dropbox file or folder."""
        return await self._require_files().get_metadata(
            path,
            include_media_info=include_media_info,
            include_deleted=include_deleted,
            include_has_explicit_shared_members=include_has_explicit_shared_members,
        )

    async def files_list_folder(
        self,
        path: str = "",
        *,
        recursive: bool = False,
        include_media_info: bool = False,
        include_deleted: bool = False,
        include_has_explicit_shared_members: bool = False,
        include_mounted_folders: bool = True,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Return one page of entries in a Dropbox folder."""
        return await self._require_files().list_folder(
            path,
            recursive=recursive,
            include_media_info=include_media_info,
            include_deleted=include_deleted,
            include_has_explicit_shared_members=include_has_explicit_shared_members,
            include_mounted_folders=include_mounted_folders,
            limit=limit,
        )

    async def files_list_folder_continue(self, cursor: str) -> dict[str, Any]:
        """Return the next page from a ``list_folder`` cursor."""
        return await self._require_files().list_folder_continue(cursor)

    @asynccontextmanager
    async def files_download(
        self,
        path: str,
    ) -> AsyncIterator[DownloadResponse]:
        """Stream a Dropbox file download.

        The response stream must be consumed within the context manager.

        Example:
            async with dbx.files_download("/report.csv") as response:
                async for chunk in response.iter_bytes():
                    ...
        """
        async with self._require_files().download(path) as response:
            yield response

    async def files_upload(
        self,
        f: bytes | bytearray | memoryview,
        path: str,
        *,
        mode: str | dict[str, Any] = "add",
        autorename: bool = False,
        client_modified: str | None = None,
        mute: bool = False,
        property_groups: list[dict[str, Any]] | None = None,
        strict_conflict: bool = False,
        content_hash: str | None = None,
    ) -> dict[str, Any]:
        """Upload raw ContentBytes using the simple upload endpoint.

        Content must not be larger than SIMPLE_UPLOAD_MAX_BYTES.
        """
        return await self._require_files().upload(
            f,
            path,
            mode=mode,
            autorename=autorename,
            client_modified=client_modified,
            mute=mute,
            property_groups=property_groups,
            strict_conflict=strict_conflict,
            content_hash=content_hash,
        )

    async def files_upload_session_start(
        self,
        f: bytes | bytearray | memoryview,
        *,
        close: bool = False,
        session_type: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call Dropbox's /2/files/upload_session/start endpoint."""
        return await self._require_files().upload_session_start(
            f,
            close=close,
            session_type=session_type,
        )

    async def files_upload_session_append_v2(
        self,
        cursor: dict[str, Any],
        f: bytes | bytearray | memoryview,
        *,
        close: bool = False,
    ) -> None:
        """Call Dropbox's /2/files/upload_session/append_v2 endpoint."""
        await self._require_files().upload_session_append_v2(
            cursor,
            f,
            close=close,
        )

    async def files_upload_session_finish(
        self,
        cursor: dict[str, Any],
        commit: dict[str, Any],
        f: bytes | bytearray | memoryview,
    ) -> dict[str, Any]:
        """Call Dropbox's /2/files/upload_session/finish endpoint."""
        return await self._require_files().upload_session_finish(
            cursor,
            commit,
            f,
        )

    async def files_upload_session_finish_batch(
        self,
        entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Call Dropbox's /2/files/upload_session/finish_batch endpoint."""
        return await self._require_files().upload_session_finish_batch(entries)

    async def files_upload_session_finish_batch_check(
        self,
        async_job_id: str,
    ) -> dict[str, Any]:
        """Call Dropbox's /2/files/upload_session/finish_batch/check endpoint."""
        return await self._require_files().upload_session_finish_batch_check(
            async_job_id
        )

    async def files_delete_v2(
        self,
        path: str,
        *,
        parent_rev: str | None = None,
    ) -> dict[str, Any]:
        """Call Dropbox's ``/2/files/delete_v2`` endpoint."""
        return await self._require_files().delete_v2(
            path,
            parent_rev=parent_rev,
        )

    async def files_create_folder_v2(
        self,
        path: str,
        *,
        autorename: bool = False,
    ) -> dict[str, Any]:
        """Call Dropbox's ``/2/files/create_folder_v2`` endpoint."""
        return await self._require_files().create_folder_v2(
            path,
            autorename=autorename,
        )

    # Convenience helpers

    async def files_list_folder_iter(
        self,
        path: str = "",
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield every result from ``files_list_folder`` pagination."""
        async for entry in self._require_files().iter_folder(path, **kwargs):
            yield entry

    async def files_download_to_path(
        self,
        path: str,
        destination: LocalPath,
        *,
        chunk_size: int = 1024 * 1024,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Download a Dropbox file to a local destination atomically.

        The method validates that a non-overwritable local destination does not
        already exist before opening the remote Dropbox response. It then streams
        to a sibling temporary file and atomically replaces the destination only
        after transfer completion.

        Returns:
            File metadata supplied by Dropbox in ``Dropbox-API-Result``.
        """
        return await self._require_files().download_to_path(
            path,
            destination,
            chunk_size=chunk_size,
            overwrite=overwrite,
        )

    async def files_upload_path(
        self,
        source: LocalPath,
        path: str,
        *,
        mode: str | dict[str, Any] = "add",
        autorename: bool = False,
        client_modified: str | None = None,
        mute: bool = False,
        property_groups: list[dict[str, Any]] | None = None,
        strict_conflict: bool = False,
        content_hash: str | None = None,
        chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE,
    ) -> dict[str, Any]:
        """Upload a local file with simple upload or a managed upload session.

        Files no larger than Dropbox's simple-upload limit are read once and sent
        through ``files/upload``. Larger files are read in bounded chunks and sent
        through an upload session. The helper owns the session cursor only for the
        lifetime of this call; it does not persist resumable upload state.

        Upload blocks are deliberately not retried after ambiguous transport or
        server failures, because Dropbox may have accepted a block even when the
        client did not receive a response.
        """
        return await self._require_files().upload_path(
            source,
            path,
            mode=mode,
            autorename=autorename,
            client_modified=client_modified,
            mute=mute,
            property_groups=property_groups,
            strict_conflict=strict_conflict,
            content_hash=content_hash,
            chunk_size=chunk_size,
        )

    async def files_upload_paths(
        self,
        uploads: Sequence[UploadPath],
        *,
        mode: str | dict[str, Any] = "add",
        autorename: bool = False,
        client_modified: str | None = None,
        mute: bool = False,
        property_groups: list[dict[str, Any]] | None = None,
        strict_conflict: bool = False,
        chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE,
    ) -> dict[str, Any]:
        """Stream and batch-commit multiple local files through upload sessions."""
        return await self._require_files().upload_paths(
            uploads,
            mode=mode,
            autorename=autorename,
            client_modified=client_modified,
            mute=mute,
            property_groups=property_groups,
            strict_conflict=strict_conflict,
            chunk_size=chunk_size,
        )

    def _require_users(self) -> UsersNamespace:
        self._require_started()
        assert self._users is not None
        return self._users

    def _require_files(self) -> FilesNamespace:
        self._require_started()
        assert self._files is not None
        return self._files

    def _require_transport(self) -> DropboxTransport:
        self._require_started()
        assert self._transport is not None
        return self._transport

    def _require_started(self) -> None:
        if self._transport is None:
            raise RuntimeError(
                "Client is not started. Use 'async with AsyncDropbox(...) as dbx' "
                "or call 'await dbx.start()' before making requests."
            )

    @staticmethod
    def _validate_rpc_endpoint(endpoint: str) -> None:
        if not endpoint.startswith("/2/"):
            raise ValueError(
                "endpoint must be an absolute Dropbox API v2 path starting with '/2/'."
            )

        if endpoint.endswith("/"):
            raise ValueError("endpoint must not end with '/'.")
