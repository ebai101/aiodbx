from __future__ import annotations

import pytest

from aiodbx import AsyncDropbox, ClientConfig


@pytest.mark.asyncio
async def test_rejects_empty_access_token() -> None:
    with pytest.raises(ValueError, match="access_token"):
        AsyncDropbox("")


@pytest.mark.asyncio
async def test_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="max_connections"):
        ClientConfig(max_connections=0)


@pytest.mark.asyncio
async def test_start_and_close_are_idempotent() -> None:
    dbx = AsyncDropbox("token")

    await dbx.start()
    first_session = dbx._session
    await dbx.start()
    assert dbx._session is first_session

    await dbx.aclose()
    await dbx.aclose()
    assert dbx._session is None


@pytest.mark.asyncio
async def test_context_manager_starts_and_closes_client() -> None:
    dbx = AsyncDropbox("token")

    async with dbx:
        assert dbx._session is not None
        assert dbx._session.closed is False

    assert dbx._session is None
