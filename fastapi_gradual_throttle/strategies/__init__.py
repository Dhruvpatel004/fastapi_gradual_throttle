"""Delay-calculation strategies.

- :class:`LinearDelayStrategy` — ``delay = base_delay * excess``.
- :class:`ExponentialDelayStrategy` — ``delay = base_delay * multiplier ** (excess - 1)``.
- :class:`NoDelayStrategy` — always 0 (used by strict mode).
"""

from .base import BaseDelayStrategy
from .exponential import ExponentialDelayStrategy
from .linear import LinearDelayStrategy
from .none import NoDelayStrategy

__all__ = [
    "BaseDelayStrategy",
    "LinearDelayStrategy",
    "ExponentialDelayStrategy",
    "NoDelayStrategy",
]
