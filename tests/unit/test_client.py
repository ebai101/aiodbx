from __future__ import annotations

import pytest

from aiodbx import AsyncDropbox, ClientConfig


@pytest.mark.asyncio
async def test_rejects_empty_access_token() -> None:
    with pytest.raises(ValueError, match="access_token"):
        AsyncDropbox("")


@pytest.mark.parametrize(
    ("kwargs", "err_text"),
    [
        ({"max_connections": 0}, "max_connections must be at least 1"),
        (
            {"max_connections_per_host": 0},
            "max_connections_per_host must be at least 1",
        ),
        ({"connect_timeout": 0}, "connect_timeout must be greater than 0"),
        ({"read_timeout": 0}, "read_timeout must be greater than 0"),
        ({"total_timeout": 0}, "total_timeout must be greater than 0"),
        ({"dns_cache_ttl": -1}, "dns_cache_ttl must not be negative"),
    ],
)
def test_rejects_invalid_configuration(
    kwargs: dict[str, int | float],
    err_text: str,
) -> None:
    with pytest.raises(ValueError, match=err_text):
        ClientConfig(**kwargs)  # ty: ignore[invalid-argument-type]


@pytest.mark.asyncio
async def test_endpoint_calls_require_started_client() -> None:
    dbx = AsyncDropbox("token")

    with pytest.raises(RuntimeError, match="not started"):
        await dbx.users_get_current_account()

    with pytest.raises(RuntimeError, match="not started"):
        await dbx.files_list_folder()


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
