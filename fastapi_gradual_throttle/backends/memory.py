"""
In-memory backend — suitable for development and single-process deployments.

Features:
  - asyncio.Lock for safe concurrent access
  - LRU-style eviction when ``max_entries`` is reached
  - Periodic expired-entry cleanup
"""

import asyncio
import logging
import time
from collections import OrderedDict

from .base import BaseBackend

logger = logging.getLogger("fastapi_gradual_throttle")

_CLEANUP_EVERY_N_WRITES = 100  # purge expired entries every N set/increment calls


class InMemoryBackend(BaseBackend):
    """
    Thread-safe in-memory backend with TTL expiration.

    **Warning**: data is not shared across OS processes.
    With ``uvicorn --workers N`` (N > 1), each worker maintains its own
    independent counters.  Use :class:`RedisBackend` for production
    multi-worker deployments.
    """

    def __init__(self, max_entries: int = 10_000, **kwargs: object):
        self._max_entries = max_entries
        # OrderedDict for LRU eviction: most-recently-used at the end.
        self._store: OrderedDict[str, tuple[dict, float]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._write_count = 0

    # --- public API -------------------------------------------------------

    async def get(self, key: str) -> dict | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            data, expires_at = entry
            if time.time() > expires_at:
                del self._store[key]
                return None
            # Move to end (most-recently-used).
            self._store.move_to_end(key)
            return data.copy()

    async def set(self, key: str, data: dict, ttl: int) -> None:
        async with self._lock:
            self._store[key] = (data.copy(), time.time() + ttl)
            self._store.move_to_end(key)
            self._write_count += 1
            self._maybe_cleanup()

    async def increment(
        self,
        key: str,
        window: int,
        ttl: int,
        now: float,
    ) -> dict:
        async with self._lock:
            entry = self._store.get(key)
            previous_count = 0

            if entry is not None:
                data, expires_at = entry
                if time.time() > expires_at:
                    # Entry expired — treat as new.
                    data = None
                else:
                    data = data.copy()

            if entry is None or data is None:
                data = {"count": 1, "window_start": now, "previous_count": 0}
            elif now - data["window_start"] >= window:
                # Window expired — rotate counts.
                previous_count = data.get("count", 0)
                data = {
                    "count": 1,
                    "window_start": now,
                    "previous_count": previous_count,
                }
            else:
                data["count"] = data.get("count", 0) + 1
                previous_count = data.get("previous_count", 0)

            self._store[key] = (data.copy(), time.time() + ttl)
            self._store.move_to_end(key)
            self._write_count += 1
            self._maybe_cleanup()
            return data.copy()

    async def ping(self) -> bool:
        return True

    async def reset(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def token_bucket_consume(
        self,
        key: str,
        rate: int,
        burst_size: int,
        window: int,
        ttl: int,
        now: float,
    ) -> dict:
        refill_rate = rate / window  # tokens per second
        async with self._lock:
            entry = self._store.get(key)
            if entry is not None:
                data, expires_at = entry
                if time.time() > expires_at:
                    data = None
                else:
                    data = data.copy()

            if entry is None or data is None:
                tokens = float(burst_size)
                last_refill = now
            else:
                tokens = data.get("tokens", float(burst_size))
                last_refill = data.get("last_refill", now)
                elapsed = now - last_refill
                tokens = min(float(burst_size), tokens + elapsed * refill_rate)
                last_refill = now

            allowed = tokens >= 1.0
            if allowed:
                tokens -= 1.0

            # Calculate accurate retry_after based on refill rate
            if not allowed and refill_rate > 0:
                retry_after_seconds = (1.0 - tokens) / refill_rate
            else:
                retry_after_seconds = 0.0

            bucket_data = {"tokens": tokens, "last_refill": last_refill}
            self._store[key] = (bucket_data.copy(), time.time() + ttl)
            self._store.move_to_end(key)
            self._write_count += 1
            self._maybe_cleanup()

            return {
                "allowed": allowed,
                "tokens_remaining": tokens,
                "bucket_size": burst_size,
                "retry_after_seconds": retry_after_seconds,
            }

    async def close(self) -> None:
        async with self._lock:
            self._store.clear()

    # --- internal helpers -------------------------------------------------

    def _maybe_cleanup(self) -> None:
        """Evict LRU entries if over capacity; purge expired on schedule."""
        # LRU eviction
        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)  # remove oldest

        # Periodic expired-entry purge
        if self._write_count % _CLEANUP_EVERY_N_WRITES == 0:
            now = time.time()
            expired = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]
