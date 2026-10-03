from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from aiodbx import AsyncDropbox, RetryPolicy
from aiodbx.hosts import EndpointHosts
from tests.helpers.http import make_app

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


@pytest.fixture
def client_factory(aiohttp_server):
    @asynccontextmanager
    async def create(
        routes: dict[str, Handler],
        *,
        api_host: bool = True,
        content_host: bool = True,
        retry_policy: RetryPolicy | None = None,
        app_key: str | None = None,
        app_secret: str | None = None,
        refresh_token: str | None = None,
    ) -> AsyncIterator[AsyncDropbox]:
        server = await aiohttp_server(make_app(routes))
        root = str(server.make_url("/")).rstrip("/")

        defaults = EndpointHosts()
        hosts = EndpointHosts(
            api=root if api_host else defaults.api,
            content=root if content_host else defaults.content,
            notify=defaults.notify,
        )

        if app_key is None and app_secret is None and refresh_token is None:
            dbx = AsyncDropbox(
                "test-token",
                retry_policy=retry_policy,
                _hosts=hosts,
            )
        else:
            if app_key is None or app_secret is None or refresh_token is None:
                raise ValueError("client_factory needs all OAuth fields together.")
            dbx = AsyncDropbox(
                app_key=app_key,
                app_secret=app_secret,
                refresh_token=refresh_token,
                retry_policy=retry_policy,
                _hosts=hosts,
            )

        async with dbx as dbx:
            yield dbx

    return create
