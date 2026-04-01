"""
Pure ASGI middleware for gradual throttling.

Supports three modes:
  - ``"gradual"``: progressive delay that increases with excess requests
  - ``"strict"``:  immediate 429 when count exceeds rate (classic rate limiter)
  - ``"combined"``: gradual delay up to hard_limit, then 429

Implements fail-open error handling so backend failures never crash
application traffic.
"""

import asyncio
import inspect
import logging
import time
from typing import Any, Callable

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import ThrottleConfig
from .exceptions import default_429_response_body, default_503_response_body
from .utils import (
    build_cache_key,
    calculate_sliding_window_count,
    call_hook,
    get_throttle_reset_time_left,
    import_backend,
    import_callable,
    import_strategy,
    should_exempt_path,
)

logger = logging.getLogger("fastapi_gradual_throttle")


class GradualThrottleMiddleware:
    """
    Pure ASGI middleware that applies gradual (or strict) throttling.

    Pass a :class:`ThrottleConfig` object so that the same config is shared by
    the middleware, ``@throttle()`` decorators, and ``Depends(GradualThrottle())``:

    .. code-block:: python

        config = ThrottleConfig(rate=100, window=60, mode="combined", hard_limit=200)
        init_throttle(app, config=config)
        app.add_middleware(GradualThrottleMiddleware, config=config)

    If no config is provided, a :class:`ThrottleConfig` is created from
    environment variables and built-in defaults automatically.

    Config resolution priority (highest → lowest):

    1. Fields passed in the :class:`ThrottleConfig` constructor kwargs
    2. ``FASTAPI_GRADUAL_THROTTLE_*`` environment variables
    3. Pydantic field defaults

    See :class:`ThrottleConfig` for the full list of supported fields.
    """

    def __init__(
        self,
        app: ASGIApp,
        config: ThrottleConfig | None = None,
        **kwargs: Any,
    ):
        self.app = app
        if config is not None:
            self.config = config
        elif kwargs:
            self.config = ThrottleConfig(**kwargs)
        else:
            self.config = ThrottleConfig()

        self._key_func: Callable | None = None
        self._delay_strategy: Any = None
        self._hook_func: Callable | None = None
        self._exempt_func: Callable | None = None
        self._limit_func: Callable | None = None
        self._response_factory: Callable | None = None
        self._backend: Any = None
        self._load_components()

    # ------------------------------------------------------------------
    # Initialisation helpers
    # ------------------------------------------------------------------

    def _load_components(self) -> None:
        """Resolve dotted-path strings into live objects."""
        # Key function
        self._key_func = import_callable(self.config.key_func)

        # Delay strategy
        if self.config.mode == "strict":
            # Strict mode: no delay — go straight to 429.
            from .strategies.none import NoDelayStrategy

            self._delay_strategy = NoDelayStrategy(
                base_delay=self.config.base_delay,
                max_delay=self.config.max_delay,
            )
        else:
            strategy_class = import_strategy(self.config.delay_strategy)
            self._delay_strategy = strategy_class(
                base_delay=self.config.base_delay,
                max_delay=self.config.max_delay,
            )

        # Optional hook
        if self.config.hook:
            self._hook_func = import_callable(self.config.hook)

        # Optional exempt function
        if self.config.exempt_func:
            self._exempt_func = import_callable(self.config.exempt_func)

        # Optional limit function
        if self.config.limit_func:
            self._limit_func = import_callable(self.config.limit_func)

        # Optional 429 response factory
        if self.config.response_factory:
            self._response_factory = import_callable(self.config.response_factory)

        # Storage backend
        backend_class = import_backend(self.config.backend)
        self._backend = backend_class(**self.config.backend_options)

        # Multi-worker warning for InMemoryBackend
        from .backends.memory import InMemoryBackend

        if isinstance(self._backend, InMemoryBackend):
            logger.info(
                "Using InMemoryBackend — throttle counts are NOT shared across "
                "workers. Use RedisBackend for production multi-worker deployments."
            )

    # ------------------------------------------------------------------
    # Per-app path cache (replaces module-level registry)
    # ------------------------------------------------------------------

    def _ensure_path_cache(self, app: Any) -> None:
        """Build per-app path cache on app.state if not already cached."""
        if not hasattr(getattr(app, "state", None), "_throttle_exempt_paths"):
            self._build_path_cache(app)

    @staticmethod
    def _build_path_cache(app: Any) -> None:
        """Scan app routes and store exempt / per-route-throttled paths on app.state.

        Results are written to:
        - ``app.state._throttle_exempt_paths`` — paths marked with ``@throttle_exempt()``
        - ``app.state._throttle_per_route_paths`` — paths that have their own throttle
          (via ``@throttle()``, ``Depends(GradualThrottle())``, or ``ThrottleRouter``)

        Called eagerly by :func:`init_throttle` and lazily on first request otherwise.
        No global mutable state is used.
        """
        from .dependencies import GradualThrottle

        exempt: set[str] = set()
        per_route: set[str] = set()

        for route in getattr(app, "routes", []):
            path = getattr(route, "path", "")
            if not path:
                continue
            endpoint = getattr(route, "endpoint", None)
            if endpoint and getattr(endpoint, "_throttle_exempt", False):
                exempt.add(path)
            if endpoint and getattr(endpoint, "_has_per_route_throttle", False):
                per_route.add(path)
            for dep in getattr(route, "dependencies", None) or []:
                if isinstance(getattr(dep, "dependency", None), GradualThrottle):
                    per_route.add(path)
                    break

        app.state._throttle_exempt_paths = exempt
        app.state._throttle_per_route_paths = per_route

    # ------------------------------------------------------------------
    # ASGI interface
    # ------------------------------------------------------------------

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket" and self.config.websocket_exempt:
            await self.app(scope, receive, send)
            return

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Skip if disabled
        if not self.config.enabled:
            await self.app(scope, receive, send)
            return

        request = Request(scope)

        # Path exemption (prefix match from exempt_paths config)
        if should_exempt_path(request.url.path, self.config.exempt_paths):
            await self.app(scope, receive, send)
            return

        # Build or retrieve the per-app path cache
        self._ensure_path_cache(request.app)
        path = request.url.path
        exempt_paths: set[str] = getattr(
            request.app.state, "_throttle_exempt_paths", set()
        )
        per_route_paths: set[str] = getattr(
            request.app.state, "_throttle_per_route_paths", set()
        )

        # Check if the route is marked as exempt via @throttle_exempt()
        if path in exempt_paths:
            await self.app(scope, receive, send)
            return

        # Auto-skip if the route has its own per-route throttle
        # (@throttle(), Depends(GradualThrottle()), or ThrottleRouter)
        if path in per_route_paths:
            await self.app(scope, receive, send)
            return

        # Custom exempt function
        if self._exempt_func:
            try:
                if inspect.iscoroutinefunction(self._exempt_func):
                    exempt = await self._exempt_func(request)
                else:
                    exempt = self._exempt_func(request)
                if exempt:
                    await self.app(scope, receive, send)
                    return
            except Exception as e:
                logger.warning(
                    "exempt_func raised an exception: %s. Treating as not exempt.", e
                )

        # --- Throttle logic (with fail-open) ---
        throttle_info = await self._evaluate_throttle(request)

        if throttle_info is None:
            # Backend failure + fail_open: pass through silently.
            await self.app(scope, receive, send)
            return

        action = throttle_info["action"]

        # Backend failure with fail_open=False → 503 Service Unavailable
        if action == "backend_error":
            await self._send_503(send)
            return

        # Handle hard-limit / strict 429
        if action == "reject":
            retry_after = int(throttle_info.get("retry_after", 0))
            await self._send_429(send, retry_after, throttle_info)
            return

        # Apply gradual delay (if any)
        delay = throttle_info.get("delay", 0.0)
        if delay > 0 and not self.config.dry_run:
            await asyncio.sleep(delay)

        # Forward request, injecting throttle headers into the response.
        if self.config.headers_enabled:
            send = self._wrap_send(send, throttle_info)

        await self.app(scope, receive, send)

    # ------------------------------------------------------------------
    # Dynamic rate resolution
    # ------------------------------------------------------------------

    async def _resolve_rate(self, request: Request) -> int:
        """Return the effective rate for this request via limit_func or config."""
        if self._limit_func is None:
            return self.config.rate
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
        return self.config.rate

    # ------------------------------------------------------------------
    # Core throttle evaluation
    # ------------------------------------------------------------------

    async def _evaluate_throttle(self, request: Request) -> dict | None:
        """
        Run the throttle logic and return an info dict, or ``None`` on
        backend failure when ``fail_open`` is enabled.
        """
        # Inject trusted_proxies for the default key_func.
        request.state._throttle_trusted_proxies = self.config.trusted_proxies

        # --- Resolve cache key ---
        try:
            raw_key = self._key_func(request)  # type: ignore[misc]
        except Exception:
            logger.warning(
                "key_func raised; falling back to IP-based key", exc_info=True
            )
            from .utils import _sanitize, get_client_ip

            ip = get_client_ip(request, self.config.trusted_proxies)
            raw_key = f"ip:{_sanitize(ip)}"

        cache_key = build_cache_key(self.config.key_prefix, raw_key)
        now = time.time()

        # --- Token bucket mode (separate path) ---
        if self.config.window_type == "token_bucket":
            return await self._evaluate_token_bucket(request, cache_key, now)

        # --- Atomic increment ---
        try:
            data = await self._backend.increment(
                key=cache_key,
                window=self.config.window,
                ttl=self.config.window + 60,
                now=now,
            )
        except Exception:
            logger.error("Backend failure during increment", exc_info=True)
            if self.config.fail_open:
                return None
            # fail_closed: signal a backend error → caller sends 503.
            return {
                "action": "backend_error",
                "retry_after": 0,
                "current_count": 0,
                "excess": 0,
                "delay": 0.0,
                "window_start": now,
                "rate": self.config.rate,
            }

        current_count = data["count"]
        window_start = data["window_start"]
        previous_count = data.get("previous_count", 0)

        # --- Sliding window adjustment ---
        if self.config.window_type == "sliding":
            effective_count = calculate_sliding_window_count(
                current_count, previous_count, window_start, self.config.window
            )
        else:
            effective_count = current_count

        # --- Dynamic rate via limit_func ---
        effective_rate = await self._resolve_rate(request)

        excess = max(0, effective_count - effective_rate)
        retry_after = get_throttle_reset_time_left(window_start, self.config.window)

        # --- Mode-specific logic ---
        if self.config.mode == "strict":
            # Strict: 429 immediately when exceeded.
            if excess > 0:
                await call_hook(
                    self._hook_func,
                    request=request,
                    action="rate_limited",
                    current_count=effective_count,
                    excess_requests=excess,
                )
                return {
                    "action": "reject",
                    "retry_after": retry_after,
                    "current_count": effective_count,
                    "excess": excess,
                    "delay": 0.0,
                    "window_start": window_start,
                    "rate": effective_rate,
                }
            return {
                "action": "allow",
                "retry_after": retry_after,
                "current_count": effective_count,
                "excess": 0,
                "delay": 0.0,
                "window_start": window_start,
                "rate": effective_rate,
            }

        # --- Gradual / Combined ---
        # Hard limit check (combined mode, or gradual with hard_limit set)
        if self.config.hard_limit > 0 and effective_count > self.config.hard_limit:
            await call_hook(
                self._hook_func,
                request=request,
                action="hard_limit_exceeded",
                current_count=effective_count,
                excess_requests=excess,
            )
            return {
                "action": "reject",
                "retry_after": retry_after,
                "current_count": effective_count,
                "excess": excess,
                "delay": 0.0,
                "window_start": window_start,
                "rate": effective_rate,
            }

        # Delay calculation
        delay = self._delay_strategy.calculate_delay(excess)

        if delay > 0:
            if self.config.dry_run:
                logger.info(
                    "DRY RUN: Would delay request by %.2fs " "(excess: %d, key: %s)",
                    delay,
                    excess,
                    cache_key,
                )
            await call_hook(
                self._hook_func,
                request=request,
                action="throttled",
                current_count=effective_count,
                excess_requests=excess,
                delay=delay,
                dry_run=self.config.dry_run,
            )

        return {
            "action": "allow",
            "retry_after": retry_after,
            "current_count": effective_count,
            "excess": excess,
            "delay": delay,
            "window_start": window_start,
            "rate": effective_rate,
        }

    # ------------------------------------------------------------------
    # Token bucket evaluation
    # ------------------------------------------------------------------

    async def _evaluate_token_bucket(
        self, request: Request, cache_key: str, now: float
    ) -> dict | None:
        """Evaluate throttle using the token-bucket algorithm.

        Returns an info dict or ``None`` on backend failure with ``fail_open``.
        """
        effective_rate = await self._resolve_rate(request)
        try:
            result = await self._backend.token_bucket_consume(
                key=cache_key,
                rate=effective_rate,
                burst_size=self.config.burst_size,
                window=self.config.window,
                ttl=self.config.window + 60,
                now=now,
            )
        except Exception:
            logger.error("Backend failure during token_bucket_consume", exc_info=True)
            if self.config.fail_open:
                return None
            # fail_closed: signal a backend error → caller sends 503.
            return {
                "action": "backend_error",
                "retry_after": 0,
                "current_count": 0,
                "excess": 0,
                "delay": 0.0,
                "window_start": now,
                "rate": effective_rate,
            }

        allowed = result.get("allowed", True)
        tokens_remaining = result.get("tokens_remaining", 0.0)

        # Calculate accurate retry_after from token refill rate
        refill_rate_per_second = (
            effective_rate / self.config.window if self.config.window > 0 else 0
        )
        if refill_rate_per_second > 0:
            retry_after_seconds = result.get(
                "retry_after_seconds",
                (
                    (1.0 - tokens_remaining) / refill_rate_per_second
                    if tokens_remaining < 1.0
                    else 0.0
                ),
            )
        else:
            retry_after_seconds = self.config.window

        if not allowed:
            await call_hook(
                self._hook_func,
                request=request,
                action="rate_limited",
                current_count=self.config.burst_size,
                excess_requests=1,
            )
            return {
                "action": "reject",
                "retry_after": retry_after_seconds,
                "current_count": self.config.burst_size,
                "excess": 1,
                "delay": 0.0,
                "window_start": now,
                "rate": effective_rate,
            }

        return {
            "action": "allow",
            "retry_after": 0,
            "current_count": max(0, int(self.config.burst_size - tokens_remaining)),
            "excess": 0,
            "delay": 0.0,
            "window_start": now,
            "rate": effective_rate,
        }

    # ------------------------------------------------------------------
    # 429 response
    # ------------------------------------------------------------------

    async def _send_429(
        self, send: Send, retry_after: int, throttle_info: dict
    ) -> None:
        """Send a 429 Too Many Requests response."""
        if self._response_factory:
            try:
                body = self._response_factory(retry_after=retry_after)
                if isinstance(body, bytes):
                    pass
                elif isinstance(body, str):
                    body = body.encode("utf-8")
                else:
                    body = str(body).encode("utf-8")
            except Exception:
                logger.warning("response_factory failed; using default", exc_info=True)
                body = default_429_response_body(retry_after)
        else:
            body = default_429_response_body(retry_after)

        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", b"application/json"),
            (b"retry-after", str(retry_after).encode()),
        ]
        if self.config.headers_enabled:
            headers.extend(
                self._build_header_pairs(throttle_info, include_retry_after=False)
            )

        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def _send_503(self, send: Send) -> None:
        """Send a 503 Service Unavailable response when the backend fails (fail_open=False)."""
        body = default_503_response_body()
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    # ------------------------------------------------------------------
    # Header injection
    # ------------------------------------------------------------------

    def _build_header_pairs(
        self, info: dict, include_retry_after: bool = True
    ) -> list[tuple[bytes, bytes]]:
        """Build throttle header pairs for the response."""
        rate = info.get("rate", self.config.rate)
        remaining = max(0, rate - info.get("current_count", 0))
        pairs: list[tuple[bytes, bytes]] = [
            (b"x-throttle-remaining", str(remaining).encode()),
            (b"x-throttle-limit", str(rate).encode()),
            (b"x-throttle-window", str(self.config.window).encode()),
        ]
        delay = info.get("delay", 0.0)
        if delay > 0:
            pairs.append((b"x-throttle-delay", f"{delay:.2f}".encode()))
        excess = info.get("excess", 0)
        if excess > 0:
            pairs.append((b"x-throttle-excess", str(excess).encode()))
            if include_retry_after:
                pairs.append(
                    (b"retry-after", str(int(info.get("retry_after", 0))).encode())
                )
        return pairs

    def _wrap_send(self, original_send: Send, throttle_info: dict) -> Send:
        """Return a ``send`` wrapper that injects throttle headers into
        the initial ``http.response.start`` message."""
        header_pairs = self._build_header_pairs(throttle_info)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = list(message.get("headers", []))
                existing.extend(header_pairs)
                message["headers"] = existing
            await original_send(message)

        return send_with_headers
