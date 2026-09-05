from __future__ import annotations

import pytest

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


@pytest.mark.parametrize(
    ("kwargs", "err_text"),
    [
        ({"max_attempts": 0}, "max_attempts must be at least 1"),
        ({"base_delay": -0.1}, "base_delay must not be negative"),
        (
            {"base_delay": 2.0, "max_delay": 1.0},
            "max_delay must be greater than or equal to base_delay",
        ),
    ],
)
def test_retry_policy_rejects_invalid_configuration(
    kwargs: dict[str, int | float],
    err_text: str,
) -> None:
    with pytest.raises(ValueError, match=err_text):
        RetryPolicy(**kwargs)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    ("attempt", "base_delay", "max_delay", "upper_bound"),
    [
        (1, 0.25, 20.0, 0.25),
        (2, 0.25, 20.0, 0.5),
        (3, 0.25, 20.0, 1.0),
        (10, 0.25, 1.0, 1.0),
    ],
)
def test_retry_policy_delay_is_bounded_by_exponential_cap(
    attempt: int,
    base_delay: float,
    max_delay: float,
    upper_bound: float,
) -> None:
    policy = RetryPolicy(
        base_delay=base_delay,
        max_delay=max_delay,
    )

    delay = policy.delay_for_attempt(attempt)

    assert 0.0 <= delay <= upper_bound


def test_retry_after_takes_priority_over_exponential_backoff() -> None:
    policy = RetryPolicy(base_delay=0.25, max_delay=10.0)

    assert policy.delay_for_attempt(4, retry_after=3.5) == 3.5
    assert policy.delay_for_attempt(4, retry_after=50.0) == 10.0
    assert policy.delay_for_attempt(4, retry_after=-1.0) == 0.0
