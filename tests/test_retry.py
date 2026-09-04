from __future__ import annotations

from aiodbx import RetryPolicy


def test_retry_policy_retries_429_and_5xx() -> None:
    policy = RetryPolicy()

    assert policy.should_retry_status(429) is True
    assert policy.should_retry_status(500) is True
    assert policy.should_retry_status(599) is True
    assert policy.should_retry_status(400) is False
    assert policy.should_retry_status(404) is False


def test_retry_after_is_bounded() -> None:
    policy = RetryPolicy(max_delay=10)

    assert policy.delay_for_attempt(1, retry_after=20) == 10
    assert policy.delay_for_attempt(1, retry_after=-1) == 0
