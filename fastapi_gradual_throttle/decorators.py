"""
``@throttle()`` decorator for per-route throttling.

Usage::

    from fastapi import Request
    from fastapi.responses import JSONResponse
    from fastapi_gradual_throttle import throttle

    @app.get("/search")
    @throttle(rate=10, window=60)
    async def search(request: Request):
        return JSONResponse({"results": []})

The decorated handler **must** accept ``request: Request`` as a parameter
(positional or keyword) so the decorator can extract it for key generation
and IP extraction.
"""

import asyncio
import functools
import inspect
import logging
import time
from typing import Any, Callable

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from .config import ThrottleConfig
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


async def _resolve_limit(request: Request, config: ThrottleConfig, state: dict) -> int:
    """Return effective rate from limit_func or config."""
    limit_func = state.get("limit_func")
    if limit_func is None:
        return config.rate
    try:
        if inspect.iscoroutinefunction(limit_func):
            rate = await limit_func(request)
        else:
            rate = limit_func(request)
        if isinstance(rate, int) and rate > 0:
            return rate
        logger.warning("limit_func returned invalid rate %r; using config rate", rate)
    except Exception:
        logger.warning("limit_func raised; falling back to config rate", exc_info=True)
    return config.rate


def throttle(**config_kwargs: Any) -> Callable:
    """
    Decorator factory that applies throttle logic to a single route handler.

    Accepts the same kwargs as :class:`ThrottleConfig`.
    Merges with global config (from ``init_throttle``) if available.
    """

    def decorator(func: Callable) -> Callable:
        # Pre-resolve the Request parameter position in the handler signature.
        sig = inspect.signature(func)
        request_param: str | None = None
        for name, param in sig.parameters.items():
            annotation = param.annotation
            if annotation is Request or (
                isinstance(annotation, type) and issubclass(annotation, Request)
            ):
                request_param = name
                break
            if name == "request":
                request_param = name
                break

        if request_param is None:
            raise TypeError(
                f"@throttle requires the handler '{func.__name__}' to accept "
                "a 'request: Request' parameter."
            )

        # Lazy-init state (populated on first call)
        state: dict[str, Any] = {"initialised": False}
        _init_lock = asyncio.Lock()

        async def _ensure_init(request: Request) -> None:
            if state["initialised"]:
                return
            async with _init_lock:
                if state["initialised"]:  # double-check after acquiring lock
                    return
                overrides = {k: v for k, v in config_kwargs.items() if v is not None}
                global_cfg = getattr(request.app.state, "throttle_config", None)
                if global_cfg and isinstance(global_cfg, ThrottleConfig):
                    base = global_cfg.model_dump()
                else:
                    base = {}
                base.update(overrides)
                cfg = ThrottleConfig(**base)
                state["config"] = cfg

                state["key_func"] = import_callable(cfg.key_func)

                if cfg.mode == "strict":
                    from .strategies.none import NoDelayStrategy

                    state["strategy"] = NoDelayStrategy(
                        base_delay=cfg.base_delay, max_delay=cfg.max_delay
                    )
                else:
                    cls = import_strategy(cfg.delay_strategy)
                    state["strategy"] = cls(
                        base_delay=cfg.base_delay, max_delay=cfg.max_delay
                    )

                state["hook"] = import_callable(cfg.hook) if cfg.hook else None
                state["limit_func"] = (
                    import_callable(cfg.limit_func) if cfg.limit_func else None
                )

                # Reuse the global backend if available and no backend override
                global_backend = getattr(request.app.state, "throttle_backend", None)
                if global_backend and "backend" not in config_kwargs:
                    state["backend"] = global_backend
                else:
                    backend_cls = import_backend(cfg.backend)
                    state["backend"] = backend_cls(**cfg.backend_options)

                state["initialised"] = True

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Extract Request from args/kwargs.
            request: Request | None = kwargs.get(request_param)
            if request is None:
                # Check positional args
                params = list(sig.parameters.keys())
                idx = params.index(request_param)
                if idx < len(args):
                    request = args[idx]

            if request is None or not isinstance(request, Request):
                raise RuntimeError(
                    "@throttle could not resolve the Request object. "
                    f"Ensure '{request_param}' is passed to the handler."
                )

            # Register this path for auto-exemption from global middleware
            # by marking the wrapper; the middleware scans app.routes on first
            # request and detects the ``_has_per_route_throttle`` attribute.

            await _ensure_init(request)
            config: ThrottleConfig = state["config"]

            if not config.enabled:
                return await func(*args, **kwargs)

            # Inject trusted_proxies
            request.state._throttle_trusted_proxies = config.trusted_proxies

            # Resolve key
            try:
                raw_key = state["key_func"](request)
            except Exception:
                logger.warning("key_func raised; falling back to IP", exc_info=True)
                from .utils import _sanitize, get_client_ip

                ip = get_client_ip(request, config.trusted_proxies)
                raw_key = f"ip:{_sanitize(ip)}"

            cache_key = build_cache_key(config.key_prefix, raw_key)
            now = time.time()

            # --- Token bucket mode (separate path) ---
            if config.window_type == "token_bucket":
                effective_rate = await _resolve_limit(request, config, state)
                try:
                    result = await state["backend"].token_bucket_consume(
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
                        return await func(*args, **kwargs)
                    raise HTTPException(status_code=503, detail="Service Unavailable")

                allowed = result.get("allowed", True)
                if not allowed:
                    retry_after = int(result.get("retry_after_seconds", config.window))
                    await call_hook(
                        state["hook"],
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
                response = await func(*args, **kwargs)
                if config.headers_enabled and isinstance(response, Response):
                    tokens_remaining = result.get("tokens_remaining", 0.0)
                    response.headers["X-Throttle-Remaining"] = str(
                        int(tokens_remaining)
                    )
                    response.headers["X-Throttle-Limit"] = str(effective_rate)
                    response.headers["X-Throttle-Window"] = str(config.window)
                return response

            # Atomic increment
            try:
                data = await state["backend"].increment(
                    key=cache_key,
                    window=config.window,
                    ttl=config.window + 60,
                    now=now,
                )
            except Exception:
                logger.error("Backend failure during increment", exc_info=True)
                if config.fail_open:
                    return await func(*args, **kwargs)
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

            effective_limit = await _resolve_limit(request, config, state)
            excess = max(0, effective - effective_limit)
            retry_after = int(get_throttle_reset_time_left(window_start, config.window))

            # Strict mode
            if config.mode == "strict" and excess > 0:
                await call_hook(
                    state["hook"],
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

            # Hard limit
            if config.hard_limit > 0 and effective > config.hard_limit:
                await call_hook(
                    state["hook"],
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

            # Gradual delay
            delay = state["strategy"].calculate_delay(excess)
            if delay > 0:
                if config.dry_run:
                    logger.info(
                        "DRY RUN: Would delay by %.2fs (excess: %d, key: %s)",
                        delay,
                        excess,
                        cache_key,
                    )
                else:
                    await asyncio.sleep(delay)
                await call_hook(
                    state["hook"],
                    request=request,
                    action="throttled",
                    current_count=effective,
                    excess_requests=excess,
                    delay=delay,
                    dry_run=config.dry_run,
                )

            # Execute the actual handler
            response = await func(*args, **kwargs)

            # Inject headers if response is a Starlette Response
            if config.headers_enabled and isinstance(response, Response):
                remaining = max(0, effective_limit - effective)
                response.headers["X-Throttle-Remaining"] = str(remaining)
                response.headers["X-Throttle-Limit"] = str(effective_limit)
                response.headers["X-Throttle-Window"] = str(config.window)
                if delay > 0:
                    response.headers["X-Throttle-Delay"] = f"{delay:.2f}"
                if excess > 0:
                    response.headers["X-Throttle-Excess"] = str(excess)
                    response.headers["Retry-After"] = str(retry_after)

            return response

        # Mark the wrapper so the middleware can auto-detect per-route throttle
        wrapper._has_per_route_throttle = True  # type: ignore[attr-defined]
        return wrapper

    return decorator
