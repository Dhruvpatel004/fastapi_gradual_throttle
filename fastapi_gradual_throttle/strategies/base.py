"""
Abstract base class for delay strategies.
"""

from abc import ABC, abstractmethod


class BaseDelayStrategy(ABC):
    """
    Compute how long to delay a request based on the number of excess
    requests above the allowed rate.

    Subclasses **must** implement :meth:`calculate_delay`.
    """

    def __init__(self, base_delay: float = 0.2, max_delay: float = 5.0):
        self.base_delay = base_delay
        self.max_delay = max_delay

    @abstractmethod
    def calculate_delay(self, excess_requests: int) -> float:
        """
        Return the delay (in seconds) to impose for *excess_requests*
        above the rate limit.

        Must return ``0.0`` when *excess_requests* <= 0.
        """
        ...

    def _clamp_delay(self, delay: float) -> float:
        """Ensure *delay* does not exceed :attr:`max_delay`."""
        return min(delay, self.max_delay)
