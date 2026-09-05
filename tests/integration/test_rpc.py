from __future__ import annotations

import pytest

from aiodbx import AsyncDropbox
from tests.helpers.integration import require_dropbox

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_rpc_can_call_unwrapped_readonly_endpoint(
    dropbox_test_token: str,
) -> None:
    async with AsyncDropbox(dropbox_test_token) as dbx:
        result = await require_dropbox(
            "users/get_space_usage",
            dbx.rpc("/2/users/get_space_usage", {}),
        )

    assert isinstance(result.get("used"), int)
    assert isinstance(result.get("allocation"), dict)
