from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from aiodbx import AsyncDropbox, DropboxError

from ..helpers.integration import test_run_root

TOKEN_ENV = "AIODBX_TEST_ACCESS_TOKEN"
TEST_FOLDER_PATH_ENV = "AIODBX_TEST_FOLDER_PATH"
TEST_DOWNLOAD_FILE_PATH_ENV = "AIODBX_TEST_DOWNLOAD_FILE_PATH"


@pytest.fixture(scope="session")
def dropbox_test_token() -> str:
    token = os.environ.get(TOKEN_ENV)
    if not token:
        pytest.skip(f"{TOKEN_ENV} is not set; skipping Dropbox integration tests.")
    return token


@pytest.fixture(scope="session")
def dropbox_test_folder_path() -> str:
    path = os.environ.get(TEST_FOLDER_PATH_ENV)
    if not path:
        pytest.skip(
            f"{TEST_FOLDER_PATH_ENV} is not set; skipping Dropbox integration tests."
        )
    if path in {"", "/"}:
        raise pytest.UsageError(
            f"{TEST_FOLDER_PATH_ENV} must name a non-root Dropbox file or folder."
        )
    if not path.startswith("/"):
        raise pytest.UsageError(
            f"{TEST_FOLDER_PATH_ENV} must start with '/'; got {path!r}."
        )
    if path.endswith("/"):
        raise pytest.UsageError(
            f"{TEST_FOLDER_PATH_ENV} must not end with '/'; got {path!r}."
        )
    return path


@pytest.fixture(scope="session")
def dropbox_test_download_file_path() -> str:
    path = os.environ.get(TEST_DOWNLOAD_FILE_PATH_ENV)

    if not path:
        pytest.skip(
            f"{TEST_DOWNLOAD_FILE_PATH_ENV} is not set; "
            "skipping Dropbox download integration tests."
        )

    if path in {"", "/"} or not path.startswith("/") or path.endswith("/"):
        raise pytest.UsageError(
            f"{TEST_DOWNLOAD_FILE_PATH_ENV} must be a non-root Dropbox file path; "
            f"got {path!r}."
        )

    return path


@pytest_asyncio.fixture(scope="session")
async def dropbox_live_write_root(
    dropbox_test_token: str,
) -> AsyncGenerator[str]:
    """Create and eventually remove this run's disposable Dropbox test root.

    Any test requiring this fixture is necessarily a state-changing Dropbox
    integration test. The root is unique per test session so simultaneous
    local/CI executions do not collide.
    """
    root = test_run_root()

    async with AsyncDropbox(dropbox_test_token) as dbx:
        try:
            await dbx.files_create_folder_v2(root, autorename=False)
        except DropboxError as exc:
            pytest.fail(
                "Could not create the Dropbox live-write test root:\n"
                f"  root: {root!r}\n"
                f"  message: {exc.message}\n"
                f"  status_code: {exc.status_code!r}\n"
                f"  error_summary: {exc.error_summary!r}\n"
                f"  error_tag: {exc.error_tag!r}\n"
                f"  request_id: {exc.request_id!r}",
                pytrace=False,
            )

    yield root

    async with AsyncDropbox(dropbox_test_token) as dbx:
        try:
            await dbx.files_delete_v2(root)
        except DropboxError as exc:
            raise RuntimeError(
                "Could not remove Dropbox live-write test root "
                f"{root!r}; Dropbox request_id={exc.request_id!r}, "
                f"error_summary={exc.error_summary!r}."
            ) from exc
