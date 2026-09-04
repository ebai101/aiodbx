from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Retry behavior for transient Dropbox and transport failures.

    ``max_attempts`` includes the initial request. For example, a value of four
    permits one original request and at most three retries.
    """

    max_attempts: int = 4
    base_delay: float = 0.25
    max_delay: float = 20.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")
        if self.base_delay < 0:
            raise ValueError("base_delay must not be negative.")
        if self.max_delay < self.base_delay:
            raise ValueError("max_delay must be greater than or equal to base_delay.")

    def should_retry_status(self, status_code: int) -> bool:
        """Return whether a response status is generally transient."""
        return status_code == 429 or 500 <= status_code <= 599

    def delay_for_attempt(
        self,
        attempt: int,
        *,
        retry_after: float | None = None,
    ) -> float:
        """Return the delay before the next retry.

        Dropbox's explicit retry delay takes priority when present. Otherwise,
        use full-jitter exponential backoff bounded by ``max_delay``.
        """
        if retry_after is not None:
            return max(0.0, min(retry_after, self.max_delay))

        cap = min(
            self.max_delay,
            self.base_delay * (2 ** max(0, attempt - 1)),
        )
        return random.uniform(0.0, cap)
