"""
Abstract base class for storage backends.

All concrete backends must subclass :class:`BaseBackend` and implement
the ``get``, ``set``, and ``increment`` abstract methods.  The optional
``token_bucket_consume``, ``reset``, ``ping``, and ``close`` methods have
default implementations that subclasses may override.
"""

from abc import ABC, abstractmethod


class BaseBackend(ABC):
    """
    Storage backend for request-count tracking.

    All methods are async to align with FastAPI's async middleware.
    """

    @abstractmethod
    async def get(self, key: str) -> dict | None:
        """
        Return throttle data for *key*, or ``None`` if not found / expired.

        Expected shape: ``{"count": int, "window_start": float}``
        Optionally: ``{"count": int, "window_start": float, "previous_count": int}``
        """
        ...

    @abstractmethod
    async def set(self, key: str, data: dict, ttl: int) -> None:
        """Store *data* under *key* with a TTL of *ttl* seconds."""
        ...

    @abstractmethod
    async def increment(
        self,
        key: str,
        window: int,
        ttl: int,
        now: float,
    ) -> dict:
        """
        Atomically read-increment-write the counter for *key*.

        Returns the **post-increment** data:
        ``{"count": int, "window_start": float, "previous_count": int}``

        If the window has expired, reset the counter and rotate
        ``previous_count``.

        **previous_count contract** (required for sliding window):

        ``previous_count`` is the request count from the PREVIOUS fixed
        window.  When the current window expires, the implementation must
        set ``previous_count = <old count>`` and reset ``count = 1``.
        The sliding window algorithm computes::

            elapsed = now - window_start
            weighted = previous_count * (1 - elapsed / window) + count

        The *weighted* value is used as the effective count for throttle
        decisions.  For first-ever requests (no prior window) set
        ``previous_count = 0``.
        """
        ...

    async def ping(self) -> bool:
        """
        Check backend connectivity.  Returns ``True`` if healthy.
        Default implementation always returns ``True``.
        """
        return True

    async def reset(self, key: str) -> None:
        """Delete the throttle counter for *key*."""
        raise NotImplementedError("Subclass must implement reset()")

    async def token_bucket_consume(
        self,
        key: str,
        rate: int,
        burst_size: int,
        window: int,
        ttl: int,
        now: float,
    ) -> dict:
        """
        Token bucket algorithm: attempt to consume one token.

        Returns::

            {
                "allowed": bool,
                "tokens_remaining": float,
                "bucket_size": int,
                "retry_after_seconds": float,
            }

        *rate* tokens are refilled per *window* seconds, up to *burst_size*.
        *retry_after_seconds* is the time until the next token becomes
        available, calculated from the refill rate.
        """
        raise NotImplementedError("Subclass must implement token_bucket_consume()")

    async def close(self) -> None:
        """Release resources (close connections).  Called on app shutdown."""
