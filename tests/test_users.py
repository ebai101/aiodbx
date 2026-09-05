from __future__ import annotations

import pytest
from aiohttp import web

from aiodbx import AsyncDropbox
from aiodbx.hosts import EndpointHosts

from .helpers.local import make_app


@pytest.mark.asyncio
async def test_get_current_account_sends_authorized_json_rpc_request(
    aiohttp_server,
) -> None:
    requests: list[web.Request] = []

    async def get_current_account(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["Content-Type"].startswith("application/json")
        assert await request.json() == {}
        return web.json_response({"account_id": "dbid:test"})

    app = make_app(
        {"/2/users/get_current_account": get_current_account},
        requests=requests,
    )
    server = await aiohttp_server(app)
    hosts = EndpointHosts(api=str(server.make_url("/")).rstrip("/"))

    async with AsyncDropbox(
        "test-token",
        _hosts=hosts,
    ) as dbx:
        account = await dbx.users_get_current_account()

    assert account == {"account_id": "dbid:test"}
    assert len(requests) == 1
