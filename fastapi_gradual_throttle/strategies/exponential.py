"""Exponential delay strategy.

Delay grows exponentially with each excess request::

    delay = base_delay * (multiplier ** (excess_requests - 1))

Capped at ``max_delay``.
"""

from .base import BaseDelayStrategy


class ExponentialDelayStrategy(BaseDelayStrategy):
    """Increase delay exponentially with each excess request.

    Args:
        base_delay: Starting delay in seconds for the first excess request.
        max_delay: Ceiling — delay will never exceed this value.
        multiplier: Growth factor applied per additional excess request.
    """

    def __init__(
        self,
        base_delay: float = 0.2,
        max_delay: float = 5.0,
        multiplier: float = 2.0,
    ):
        super().__init__(base_delay, max_delay)
        self.multiplier = multiplier

    def calculate_delay(self, excess_requests: int) -> float:
        if excess_requests <= 0:
            return 0.0
        delay = self.base_delay * (self.multiplier ** (excess_requests - 1))
        return self._clamp_delay(delay)
