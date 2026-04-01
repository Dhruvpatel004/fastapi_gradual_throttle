"""
FastAPI ``Depends()``-based throttle for per-route rate limiting.

Usage::

    from fastapi import Depends
    from fastapi_gradual_throttle import GradualThrottle

    @app.get("/expensive", dependencies=[Depends(GradualThrottle(rate=10, window=60))])
    async def expensive():
        return {"ok": True}
"""

import asyncio
import inspect
import logging
import time
from typing import Any, Callable

from fastapi import HTTPException, Request

from .config import ThrottleConfig
from .exceptions import default_429_response_body
from .utils import (
    build_cache_key,
    calculate_sliding_window_count,
    call_hook,
    get_throttle_reset_time_left,
    import_backend,
    import_callable,
    import_strategy,
)

logger = logging.getLogger("fastapi_gradual_throttle")


class GradualThrottle:
    """
    Callable class usable as a FastAPI dependency for per-route throttling.

    Reads global config from ``request.app.state.throttle_config`` (if set
    via :func:`init_throttle`) and merges with any explicit overrides
    provided here.
    """

    def __init__(self, **kwargs: Any):
        self._overrides = {k: v for k, v in kwargs.items() if v is not None}
        # Will be lazily initialised on first call if global config exists.
        self._config: ThrottleConfig | None = None
        self._key_func: Callable | None = None
        self._delay_strategy: Any = None
        self._hook_func: Callable | None = None
        self._limit_func: Callable | None = None
        self._backend: Any = None
        self._init_lock = asyncio.Lock()
        self._initialised = False

    async def _ensure_init(self, request: Request) -> None:
        """Lazy init — merge global config with per-dependency overrides."""
        if self._initialised:
            return
        async with self._init_lock:
            if self._initialised:  # double-check after acquiring lock
                return

            # Start from global config if available, else defaults
            global_config = getattr(request.app.state, "throttle_config", None)
            if global_config and isinstance(global_config, ThrottleConfig):
                base = global_config.model_dump()
            else:
                base = {}

            base.update(self._overrides)
            self._config = ThrottleConfig(**base)

            # Resolve components — cache them for reuse.
            self._key_func = import_callable(self._config.key_func)

            if self._config.mode == "strict":
                from .strategies.none import NoDelayStrategy

                self._delay_strategy = NoDelayStrategy(
                    base_delay=self._config.base_delay,
                    max_delay=self._config.max_delay,
                )
            else:
                strategy_cls = import_strategy(self._config.delay_strategy)
                self._delay_strategy = strategy_cls(
                    base_delay=self._config.base_delay,
                    max_delay=self._config.max_delay,
                )

            if self._config.hook:
                self._hook_func = import_callable(self._config.hook)

            if self._config.limit_func:
                self._limit_func = import_callable(self._config.limit_func)

            # Reuse the global backend if available and no backend override
            global_backend = getattr(request.app.state, "throttle_backend", None)
            if global_backend and "backend" not in self._overrides:
                self._backend = global_backend
            else:
                backend_cls = import_backend(self._config.backend)
                self._backend = backend_cls(**self._config.backend_options)

            self._initialised = True

    async def _resolve_rate(self, request: Request) -> int:
        """Return the effective rate from limit_func or config."""
        if self._limit_func is None:
            return self._config.rate
        try:
            if inspect.iscoroutinefunction(self._limit_func):
                rate = await self._limit_func(request)
            else:
                rate = self._limit_func(request)
            if isinstance(rate, int) and rate > 0:
                return rate
            logger.warning(
                "limit_func returned invalid rate %r; using config rate", rate
            )
        except Exception:
            logger.warning(
                "limit_func raised; falling back to config rate", exc_info=True
            )
        return self._config.rate

    async def __call__(self, request: Request) -> None:
        await self._ensure_init(request)
        config = self._config
        assert config is not None

        if not config.enabled:
            return

        # Inject trusted_proxies for default key_func
        request.state._throttle_trusted_proxies = config.trusted_proxies

        # Resolve cache key
        try:
            raw_key = self._key_func(request)  # type: ignore[misc]
        except Exception:
            logger.warning(
                "key_func raised; falling back to IP-based key", exc_info=True
            )
            from .utils import _sanitize, get_client_ip

            ip = get_client_ip(request, config.trusted_proxies)
            raw_key = f"ip:{_sanitize(ip)}"

        cache_key = build_cache_key(config.key_prefix, raw_key)
        now = time.time()

        # --- Token bucket mode (separate path) ---
        if config.window_type == "token_bucket":
            effective_rate = await self._resolve_rate(request)
            try:
                result = await self._backend.token_bucket_consume(
                    key=cache_key,
                    rate=effective_rate,
                    burst_size=config.burst_size,
                    window=config.window,
                    ttl=config.window + 60,
                    now=now,
                )
            except Exception:
                logger.error(
                    "Backend failure during token_bucket_consume", exc_info=True
                )
                if config.fail_open:
                    return
                raise HTTPException(status_code=503, detail="Service Unavailable")

            allowed = result.get("allowed", True)
            if not allowed:
                retry_after = int(result.get("retry_after_seconds", config.window))
                await call_hook(
                    self._hook_func,
                    request=request,
                    action="rate_limited",
                    current_count=config.burst_size,
                    excess_requests=1,
                )
                raise HTTPException(
                    status_code=429,
                    detail="Too Many Requests",
                    headers={"Retry-After": str(retry_after)},
                )
            return

        # Atomic increment
        try:
            data = await self._backend.increment(
                key=cache_key,
                window=config.window,
                ttl=config.window + 60,
                now=now,
            )
        except Exception:
            logger.error("Backend failure during increment", exc_info=True)
            if config.fail_open:
                return
            raise HTTPException(status_code=503, detail="Service Unavailable")

        current_count = data["count"]
        window_start = data["window_start"]
        previous_count = data.get("previous_count", 0)

        if config.window_type == "sliding":
            effective = calculate_sliding_window_count(
                current_count, previous_count, window_start, config.window
            )
        else:
            effective = current_count

        excess = max(0, effective - await self._resolve_rate(request))
        retry_after = int(get_throttle_reset_time_left(window_start, config.window))

        # Mode logic
        if config.mode == "strict" and excess > 0:
            await call_hook(
                self._hook_func,
                request=request,
                action="rate_limited",
                current_count=effective,
                excess_requests=excess,
            )
            raise HTTPException(
                status_code=429,
                detail="Too Many Requests",
                headers={"Retry-After": str(retry_after)},
            )

        if config.hard_limit > 0 and effective > config.hard_limit:
            await call_hook(
                self._hook_func,
                request=request,
                action="hard_limit_exceeded",
                current_count=effective,
                excess_requests=excess,
            )
            raise HTTPException(
                status_code=429,
                detail="Too Many Requests",
                headers={"Retry-After": str(retry_after)},
            )

        delay = self._delay_strategy.calculate_delay(excess)
        if delay > 0:
            if config.dry_run:
                logger.info(
                    "DRY RUN: Would delay request by %.2fs (excess: %d, key: %s)",
                    delay,
                    excess,
                    cache_key,
                )
            else:
                await asyncio.sleep(delay)
            await call_hook(
                self._hook_func,
                request=request,
                action="throttled",
                current_count=effective,
                excess_requests=excess,
                delay=delay,
                dry_run=config.dry_run,
            )
