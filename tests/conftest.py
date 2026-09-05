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
    ) -> AsyncIterator[AsyncDropbox]:
        server = await aiohttp_server(make_app(routes))
        root = str(server.make_url("/")).rstrip("/")

        defaults = EndpointHosts()
        hosts = EndpointHosts(
            api=root if api_host else defaults.api,
            content=root if content_host else defaults.content,
            notify=defaults.notify,
        )

        async with AsyncDropbox(
            "test-token",
            retry_policy=retry_policy,
            _hosts=hosts,
        ) as dbx:
            yield dbx

    return create
