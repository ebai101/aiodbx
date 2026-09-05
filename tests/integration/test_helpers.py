from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from aiodbx import DropboxError

from ..helpers.integration import require_dropbox_iter


async def yields_two() -> AsyncIterator[int]:
    yield 1
    yield 2


async def yields_then_fails() -> AsyncIterator[int]:
    yield 1
    raise DropboxError(
        message="Dropbox API request failed.",
        status_code=409,
        error_summary="path/not_found/..",
        error_tag="path/not_found",
        request_id="test-request-id",
    )


@pytest.mark.asyncio
async def test_require_dropbox_iter_yields_values() -> None:
    values = [value async for value in require_dropbox_iter("test", yields_two())]

    assert values == [1, 2]


@pytest.mark.asyncio
async def test_require_dropbox_iter_reports_mid_iteration_dropbox_error() -> None:
    with pytest.raises(pytest.fail.Exception) as caught:
        _ = [
            value
            async for value in require_dropbox_iter(
                "files/iter_folder",
                yields_then_fails(),
            )
        ]

    message = str(caught.value)
    assert "files/iter_folder failed against Dropbox:" in message
    assert "status_code: 409" in message
    assert "error_summary: 'path/not_found/..'" in message
    assert "error_tag: 'path/not_found'" in message
    assert "request_id: 'test-request-id'" in message
    assert "entries_yielded_before_failure: 1" in message
