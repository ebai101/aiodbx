from __future__ import annotations

import hashlib

import pytest

from .helpers.content_hash import DropboxContentHasher


def expected_hash(data: bytes) -> str:
    block_size = 4 * 1024 * 1024
    overall = hashlib.sha256()

    for start in range(0, len(data), block_size):
        overall.update(hashlib.sha256(data[start : start + block_size]).digest())

    return overall.hexdigest()


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"hello world",
        b"x" * (4 * 1024 * 1024 - 1),
        b"x" * (4 * 1024 * 1024),
        b"x" * (4 * 1024 * 1024 + 1),
        b"x" * (8 * 1024 * 1024 + 123),
    ],
)
def test_matches_reference_definition(data: bytes) -> None:
    hasher = DropboxContentHasher()

    for start in range(0, len(data), 333_333):
        hasher.update(data[start : start + 333_333])

    assert hasher.hexdigest() == expected_hash(data)


def test_empty_content_hash_is_sha256_of_empty_input() -> None:
    assert DropboxContentHasher().hexdigest() == hashlib.sha256(b"").hexdigest()


def test_copy_preserves_partial_state() -> None:
    original = DropboxContentHasher()
    original.update(b"before-copy")

    copied = original.copy()

    original.update(b"-first")
    copied.update(b"-second")

    assert original.hexdigest() == expected_hash(b"before-copy-first")
    assert copied.hexdigest() == expected_hash(b"before-copy-second")


def test_update_after_finalize_fails() -> None:
    hasher = DropboxContentHasher()
    hasher.update(b"data")
    hasher.hexdigest()

    with pytest.raises(ValueError, match="finalized"):
        hasher.update(b"more")
