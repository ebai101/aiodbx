from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiohttp import web

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def make_app(
    routes: dict[str, Handler],
    *,
    requests: list[web.Request] | None = None,
) -> web.Application:
    """Create an app with POST routes and optional request recording."""
    app = web.Application()
    request_log = requests if requests is not None else []

    for path, handler in routes.items():

        async def wrapped(
            request: web.Request,
            *,
            handler: Handler = handler,
        ) -> web.StreamResponse:
            request_log.append(request)
            return await handler(request)

        app.router.add_post(path, wrapped)

    return app
