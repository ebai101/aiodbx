from __future__ import annotations

import pytest
from aiohttp import web

from aiodbx import AsyncDropbox


@pytest.mark.asyncio
async def test_get_current_account_sends_authorized_json_rpc_request(
    server_factory,
) -> None:
    async def get_current_account(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["Content-Type"].startswith("application/json")
        assert await request.json() == {}
        return web.json_response({"account_id": "dbid:test"})

    server = await server_factory({"/2/users/get_current_account": get_current_account})

    async with AsyncDropbox("test-token") as dbx:
        transport = dbx._transport
        assert transport is not None
        transport._api_host = server.base_url

        account = await dbx.users.get_current_account()

    assert account == {"account_id": "dbid:test"}
    assert len(server.requests) == 1
