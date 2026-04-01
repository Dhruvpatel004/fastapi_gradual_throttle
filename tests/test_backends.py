"""
Tests for InMemoryBackend — set/get, TTL expiry, LRU eviction, atomic increment.
"""

import time

import pytest

from fastapi_gradual_throttle.backends.memory import InMemoryBackend


@pytest.mark.asyncio
class TestInMemoryBackend:
    async def test_get_missing_key(self):
        b = InMemoryBackend()
        assert await b.get("nonexistent") is None

    async def test_set_and_get(self):
        b = InMemoryBackend()
        await b.set("k", {"count": 1, "window_start": 1.0}, ttl=60)
        data = await b.get("k")
        assert data is not None
        assert data["count"] == 1

    async def test_ttl_expiry(self):
        b = InMemoryBackend()
        await b.set("k", {"count": 1, "window_start": 1.0}, ttl=0)
        # TTL=0 means already expired
        import asyncio

        await asyncio.sleep(0.01)
        assert await b.get("k") is None

    async def test_lru_eviction(self):
        b = InMemoryBackend(max_entries=3)
        for i in range(5):
            await b.set(f"k{i}", {"count": i}, ttl=60)
        # Only last 3 should remain
        assert await b.get("k0") is None
        assert await b.get("k1") is None
        assert await b.get("k2") is not None
        assert await b.get("k4") is not None

    async def test_increment_new_key(self):
        b = InMemoryBackend()
        now = time.time()
        data = await b.increment("k", window=60, ttl=120, now=now)
        assert data["count"] == 1
        assert data["window_start"] == now
        assert data["previous_count"] == 0

    async def test_increment_existing_key(self):
        b = InMemoryBackend()
        now = time.time()
        await b.increment("k", window=60, ttl=120, now=now)
        data = await b.increment("k", window=60, ttl=120, now=now)
        assert data["count"] == 2

    async def test_increment_window_reset(self):
        b = InMemoryBackend()
        past = time.time() - 120
        # Seed with an old entry
        await b.set(
            "k", {"count": 42, "window_start": past, "previous_count": 0}, ttl=300
        )
        now = time.time()
        data = await b.increment("k", window=60, ttl=120, now=now)
        assert data["count"] == 1
        assert data["previous_count"] == 42  # rotated from previous window

    async def test_ping(self):
        b = InMemoryBackend()
        assert await b.ping() is True

    async def test_close(self):
        b = InMemoryBackend()
        await b.set("k", {"count": 1}, ttl=60)
        await b.close()
        assert await b.get("k") is None

    async def test_data_isolation(self):
        """Mutating returned data should not affect the stored copy."""
        b = InMemoryBackend()
        await b.set("k", {"count": 1, "window_start": 1.0}, ttl=60)
        data = await b.get("k")
        data["count"] = 999
        fresh = await b.get("k")
        assert fresh["count"] == 1

    async def test_reset_key(self):
        b = InMemoryBackend()
        await b.set("k", {"count": 5, "window_start": 1.0}, ttl=60)
        assert await b.get("k") is not None
        await b.reset("k")
        assert await b.get("k") is None

    async def test_reset_nonexistent_key(self):
        """Resetting a missing key should not raise."""
        b = InMemoryBackend()
        await b.reset("nonexistent")  # should not raise

    async def test_token_bucket_initial_consume(self):
        """First consume on a fresh key should succeed with full burst."""
        b = InMemoryBackend()
        now = time.time()
        result = await b.token_bucket_consume(
            "tb", rate=10, burst_size=5, window=60, ttl=120, now=now
        )
        assert result["allowed"] is True
        assert result["tokens_remaining"] == 4.0
        assert result["bucket_size"] == 5

    async def test_token_bucket_exhaust_tokens(self):
        """Consuming all tokens should eventually be rejected."""
        b = InMemoryBackend()
        now = time.time()
        # burst_size=2, consume twice → allowed, third → rejected
        r1 = await b.token_bucket_consume(
            "tb", rate=10, burst_size=2, window=60, ttl=120, now=now
        )
        assert r1["allowed"] is True
        r2 = await b.token_bucket_consume(
            "tb", rate=10, burst_size=2, window=60, ttl=120, now=now
        )
        assert r2["allowed"] is True
        r3 = await b.token_bucket_consume(
            "tb", rate=10, burst_size=2, window=60, ttl=120, now=now
        )
        assert r3["allowed"] is False
        assert r3["retry_after_seconds"] > 0

    async def test_token_bucket_refill_over_time(self):
        """After waiting, tokens should refill and allow consumption."""
        b = InMemoryBackend()
        now = time.time()
        # burst_size=1, rate=60/60s = 1 token/sec
        r1 = await b.token_bucket_consume(
            "tb", rate=60, burst_size=1, window=60, ttl=120, now=now
        )
        assert r1["allowed"] is True
        # Immediate retry fails
        r2 = await b.token_bucket_consume(
            "tb", rate=60, burst_size=1, window=60, ttl=120, now=now
        )
        assert r2["allowed"] is False
        # 2 seconds later, 2 tokens refilled (but capped at burst_size=1)
        r3 = await b.token_bucket_consume(
            "tb", rate=60, burst_size=1, window=60, ttl=120, now=now + 2
        )
        assert r3["allowed"] is True

    async def test_increment_expired_entry_via_ttl(self):
        """Increment with an entry that expired via TTL resets counter."""
        b = InMemoryBackend()
        await b.set(
            "k", {"count": 50, "window_start": time.time(), "previous_count": 0}, ttl=0
        )
        import asyncio

        await asyncio.sleep(0.01)
        now = time.time()
        data = await b.increment("k", window=60, ttl=120, now=now)
        assert data["count"] == 1  # reset because TTL expired

    async def test_lru_moves_accessed_to_end(self):
        """Accessing a key via get should move it to end, preventing eviction."""
        b = InMemoryBackend(max_entries=3)
        await b.set("k0", {"v": 0}, ttl=60)
        await b.set("k1", {"v": 1}, ttl=60)
        await b.set("k2", {"v": 2}, ttl=60)
        # Access k0 to make it most-recently-used
        await b.get("k0")
        # Add k3 → should evict k1 (oldest untouched), not k0
        await b.set("k3", {"v": 3}, ttl=60)
        assert await b.get("k0") is not None
        assert await b.get("k1") is None
