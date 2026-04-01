"""
``ThrottleRouter`` — an ``APIRouter`` sub-class that applies a shared
throttle configuration to every route registered on it.

Usage::

    from fastapi_gradual_throttle import ThrottleRouter

    api = ThrottleRouter(prefix="/api/v1", throttle_rate=50, throttle_window=30)

    @api.get("/users")
    async def list_users():
        return []

    app.include_router(api)

Routes in this router inherit the throttle settings specified here.
The throttle is applied via a ``Depends()`` dependency so per-route
overrides are still possible.
"""

from typing import Any

from fastapi import APIRouter, Depends

from .dependencies import GradualThrottle


class ThrottleRouter(APIRouter):
    """
    An APIRouter that automatically applies throttle settings as a
    dependency to every route.

    Pass throttle config kwargs prefixed with ``throttle_`` or directly:

    - ``throttle_rate`` / ``rate``
    - ``throttle_window`` / ``window``
    - ``throttle_mode`` / ``mode``
    - any other :class:`ThrottleConfig` field

    Non-throttle kwargs are forwarded to ``APIRouter.__init__``.
    """

    # ThrottleConfig field names
    _THROTTLE_FIELDS = {
        "enabled",
        "dry_run",
        "mode",
        "rate",
        "window",
        "window_type",
        "base_delay",
        "max_delay",
        "hard_limit",
        "key_func",
        "delay_strategy",
        "exempt_paths",
        "exempt_func",
        "hook",
        "headers_enabled",
        "trusted_proxies",
        "fail_open",
        "backend",
        "backend_options",
        "key_prefix",
        "response_factory",
        "limit_func",
        "websocket_exempt",
        "burst_size",
    }

    def __init__(self, **kwargs: Any):
        throttle_kwargs: dict[str, Any] = {}
        router_kwargs: dict[str, Any] = {}

        for key, value in kwargs.items():
            # Accept both ``throttle_rate`` and ``rate`` forms.
            stripped = key.removeprefix("throttle_")
            if stripped in self._THROTTLE_FIELDS:
                throttle_kwargs[stripped] = value
            else:
                router_kwargs[key] = value

        # Create the shared dependency.
        throttle_dep = GradualThrottle(**throttle_kwargs)

        # Merge with any pre-existing dependencies.
        existing_deps = list(router_kwargs.pop("dependencies", []))
        existing_deps.append(Depends(throttle_dep))
        router_kwargs["dependencies"] = existing_deps

        super().__init__(**router_kwargs)
