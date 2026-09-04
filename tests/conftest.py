from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

import pytest_asyncio
from aiohttp import web


@dataclass(frozen=True, slots=True)
class TestServer:
    base_url: str
    requests: list[web.Request]


Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


@pytest_asyncio.fixture
async def server_factory() -> AsyncIterator[
    Callable[[dict[str, Handler]], Awaitable[TestServer]]
]:
    runners: list[web.AppRunner] = []

    async def make_server(routes: dict[str, Handler]) -> TestServer:
        app = web.Application()
        requests: list[web.Request] = []

        for path, handler in routes.items():

            async def wrapped(
                request: web.Request,
                handler: Handler = handler,
            ) -> web.StreamResponse:
                requests.append(request)
                return await handler(request)

            app.router.add_post(path, wrapped)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        runners.append(runner)

        sockets = site._server.sockets
        assert sockets is not None
        port = sockets[0].getsockname()[1]
        return TestServer(
            base_url=f"http://127.0.0.1:{port}",
            requests=requests,
        )

    yield make_server

    for runner in runners:
        await runner.cleanup()
