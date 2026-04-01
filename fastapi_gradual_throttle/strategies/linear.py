"""Linear delay strategy.

Delay grows linearly with each excess request::

    delay = base_delay * excess_requests

Capped at ``max_delay``.
"""

from .base import BaseDelayStrategy


class LinearDelayStrategy(BaseDelayStrategy):
    """Increase delay linearly with each excess request."""

    def calculate_delay(self, excess_requests: int) -> float:
        if excess_requests <= 0:
            return 0.0
        return self._clamp_delay(self.base_delay * excess_requests)
