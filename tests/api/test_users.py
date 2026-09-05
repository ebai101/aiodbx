from __future__ import annotations

import pytest
from aiohttp import web


@pytest.mark.asyncio
async def test_users_get_current_account_sends_json_rpc_request(client_factory) -> None:
    expected_account = {"account_id": "dbid:test"}

    async def get_current_account(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["Content-Type"].startswith("application/json")
        assert await request.json() == {}
        return web.json_response(expected_account)

    async with client_factory(
        {"/2/users/get_current_account": get_current_account},
        content_host=False,
    ) as dbx:
        account = await dbx.users_get_current_account()

    assert account == expected_account
