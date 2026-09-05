from __future__ import annotations

import os

import pytest

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
            f"{TEST_DOWNLOAD_FILE_PATH_ENV} \
            is not set; skipping Dropbox download integration tests."
        )
    return path


@pytest.fixture(scope="session")
def dropbox_live_write_root() -> str:
    return test_run_root()
