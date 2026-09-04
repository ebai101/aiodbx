from __future__ import annotations

import os
import re
import uuid
from collections.abc import AsyncIterable, AsyncIterator, Awaitable
from typing import TypeVar

import pytest

from aiodbx import DropboxError

T = TypeVar("T")


def test_run_root() -> str:
    raw = os.environ.get("GITHUB_RUN_ID") or uuid.uuid4().hex
    run_id = re.sub(r"[^a-zA-Z0-9_-]", "-", raw)
    return f"/aiodbx-integration/{run_id}"


async def require_dropbox(
    operation: str,
    awaitable: Awaitable[T],
) -> T:
    """Await a Dropbox operation or fail the test with safe diagnostics."""
    try:
        return await awaitable
    except DropboxError as exc:
        pytest.fail(_format_dropbox_failure(operation, exc), pytrace=False)


async def require_dropbox_iter(
    operation: str,
    iterable: AsyncIterable[T],
) -> AsyncIterator[T]:
    """Yield a Dropbox async iterable or fail with safe diagnostics.

    This catches failures that occur both when iteration starts and while
    fetching subsequent pages from Dropbox.
    """
    yielded = 0

    try:
        async for item in iterable:
            yielded += 1
            yield item
    except DropboxError as exc:
        pytest.fail(
            _format_dropbox_failure(
                operation,
                exc,
                yielded=yielded,
            ),
            pytrace=False,
        )


def _format_dropbox_failure(
    operation: str,
    exc: DropboxError,
    *,
    yielded: int | None = None,
) -> str:
    """Format safe Dropbox diagnostics for a pytest assertion failure."""
    lines = [
        f"{operation} failed against Dropbox:",
        f"  message: {exc.message}",
        f"  status_code: {exc.status_code!r}",
        f"  error_summary: {exc.error_summary!r}",
        f"  error_tag: {exc.error_tag!r}",
        f"  request_id: {exc.request_id!r}",
        f"  retry_after: {exc.retry_after!r}",
    ]

    if yielded is not None:
        lines.append(f"  entries_yielded_before_failure: {yielded}")

    return "\n".join(lines)
