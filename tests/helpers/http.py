from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiohttp import web

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def make_app(routes: dict[str, Handler]) -> web.Application:
    """Create an aiohttp app with one POST handler per Dropbox endpoint path."""
    app = web.Application()

    for path, handler in routes.items():
        app.router.add_post(path, handler)

    return app
