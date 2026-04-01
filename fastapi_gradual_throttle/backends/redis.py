"""
Redis backend — production-grade storage with atomic increment via Lua script.

Requires ``redis[hiredis] >= 4.2``  (``pip install fastapi-gradual-throttle[redis]``).
"""

import json
import logging
import time

from .base import BaseBackend

logger = logging.getLogger("fastapi_gradual_throttle")

# Lua script executed atomically on the Redis server.
# KEYS[1] = cache key
# ARGV[1] = window (seconds)
# ARGV[2] = ttl (seconds)
# ARGV[3] = current timestamp (float as string)
#
# Returns a JSON string: {"count": N, "window_start": T, "previous_count": P}
_LUA_INCREMENT = """
local key = KEYS[1]
local window = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

local raw = redis.call('GET', key)
local data

if raw then
    data = cjson.decode(raw)
    if now - data.window_start >= window then
        -- Window expired: rotate counts.
        data = {count = 1, window_start = now, previous_count = data.count}
    else
        data.count = data.count + 1
    end
else
    data = {count = 1, window_start = now, previous_count = 0}
end

redis.call('SETEX', key, ttl, cjson.encode(data))
return cjson.encode(data)
"""

# Lua script for token bucket algorithm
# KEYS[1] = cache key
# ARGV[1] = rate (tokens per window)
# ARGV[2] = burst_size (max tokens)
# ARGV[3] = window (seconds)
# ARGV[4] = ttl (seconds)
# ARGV[5] = current timestamp (float)
_LUA_TOKEN_BUCKET = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local burst_size = tonumber(ARGV[2])
local window = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local now = tonumber(ARGV[5])

local refill_rate = rate / window
local raw = redis.call('GET', key)
local tokens, last_refill

if raw then
    local data = cjson.decode(raw)
    tokens = data.tokens
    last_refill = data.last_refill
    local elapsed = now - last_refill
    tokens = math.min(burst_size, tokens + elapsed * refill_rate)
    last_refill = now
else
    tokens = burst_size
    last_refill = now
end

local allowed = tokens >= 1
if allowed then
    tokens = tokens - 1
end

local retry_after_seconds = 0
if not allowed and refill_rate > 0 then
    retry_after_seconds = (1.0 - tokens) / refill_rate
end

local result = {tokens = tokens, last_refill = last_refill}
redis.call('SETEX', key, ttl, cjson.encode(result))
return cjson.encode({allowed = allowed, tokens_remaining = tokens, bucket_size = burst_size, retry_after_seconds = retry_after_seconds})
"""


class RedisBackend(BaseBackend):
    """
    Async Redis backend using ``redis.asyncio`` (redis-py >= 4.2).

    Uses a Lua script for atomic read-increment-write, preventing
    race conditions under high concurrency.
    """

    def __init__(
        self, url: str = "redis://localhost:6379/0", **kwargs: object
    ):  # noqa: S104
        try:
            import redis.asyncio as aioredis
        except ImportError as exc:
            raise ImportError(
                "Redis backend requires the 'redis' package. "
                "Install it with: pip install fastapi-gradual-throttle[redis]"
            ) from exc

        self._redis = aioredis.from_url(
            url, decode_responses=True, **kwargs  # type: ignore[arg-type]
        )
        self._lua_sha: str | None = None
        self._token_lua_sha: str | None = None

    async def _ensure_script(self) -> str:
        """Load the Lua script once and cache its SHA."""
        if self._lua_sha is None:
            self._lua_sha = await self._redis.script_load(_LUA_INCREMENT)
        return self._lua_sha

    # --- public API -------------------------------------------------------

    async def get(self, key: str) -> dict | None:
        raw = await self._redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def set(self, key: str, data: dict, ttl: int) -> None:
        await self._redis.setex(key, ttl, json.dumps(data))

    async def increment(
        self,
        key: str,
        window: int,
        ttl: int,
        now: float,
    ) -> dict:
        sha = await self._ensure_script()
        try:
            raw = await self._redis.evalsha(
                sha, 1, key, str(window), str(ttl), str(now)
            )
        except Exception:
            # Script may have been flushed (e.g. SCRIPT FLUSH, failover).
            # Reload once and retry.
            self._lua_sha = await self._redis.script_load(_LUA_INCREMENT)
            raw = await self._redis.evalsha(
                self._lua_sha, 1, key, str(window), str(ttl), str(now)
            )
        return json.loads(raw)

    async def ping(self) -> bool:
        try:
            return await self._redis.ping()
        except Exception:
            return False

    async def reset(self, key: str) -> None:
        await self._redis.delete(key)

    async def token_bucket_consume(
        self,
        key: str,
        rate: int,
        burst_size: int,
        window: int,
        ttl: int,
        now: float,
    ) -> dict:
        sha = await self._ensure_token_bucket_script()
        try:
            raw = await self._redis.evalsha(
                sha,
                1,
                key,
                str(rate),
                str(burst_size),
                str(window),
                str(ttl),
                str(now),
            )
        except Exception:
            self._token_lua_sha = await self._redis.script_load(_LUA_TOKEN_BUCKET)
            raw = await self._redis.evalsha(
                self._token_lua_sha,
                1,
                key,
                str(rate),
                str(burst_size),
                str(window),
                str(ttl),
                str(now),
            )
        return json.loads(raw)

    async def _ensure_token_bucket_script(self) -> str:
        if self._token_lua_sha is None:
            self._token_lua_sha = await self._redis.script_load(_LUA_TOKEN_BUCKET)
        return self._token_lua_sha

    async def close(self) -> None:
        await self._redis.aclose()
