"""No-delay strategy.

Used internally by ``mode="strict"`` to skip delay calculation entirely
and proceed straight to a 429 response.
"""

from .base import BaseDelayStrategy


class NoDelayStrategy(BaseDelayStrategy):
    """Always returns 0.0 — no gradual delay."""

    def calculate_delay(self, excess_requests: int) -> float:
        return 0.0
