from __future__ import annotations

import pytest

from aiodbx import AsyncDropbox
from tests.helpers.integration import require_dropbox

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_get_current_account_returns_an_account_id(
    dropbox_test_token: str,
) -> None:
    async with AsyncDropbox(dropbox_test_token) as dbx:
        account = await require_dropbox(
            "users/get_current_account", dbx.users_get_current_account()
        )

    assert isinstance(account.get("account_id"), str)
    assert account["account_id"].startswith("dbid:")
    assert isinstance(account.get("name"), dict)
