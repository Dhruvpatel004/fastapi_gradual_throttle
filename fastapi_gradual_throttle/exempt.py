"""
``@throttle_exempt()`` decorator to opt individual routes out of global
middleware throttling.

Usage::

    from fastapi import Request
    from fastapi_gradual_throttle import throttle_exempt

    @app.get("/internal")
    @throttle_exempt()
    async def internal_endpoint(request: Request):
        return {"status": "ok"}

Routes decorated with ``@throttle_exempt()`` are detected by the middleware
through a marker attribute (``_throttle_exempt = True``) on the endpoint
function.  On first request (or eagerly via :func:`init_throttle`), the
middleware scans ``app.routes`` and caches exempt paths on ``app.state``.
No global mutable state is used.
"""

import asyncio
import functools
import inspect
from typing import Callable


def throttle_exempt() -> Callable:
    """Mark a route handler as exempt from global middleware throttling.

    The middleware detects this marker via ``endpoint._throttle_exempt`` when
    it builds its per-app path cache on the first request (or at
    ``init_throttle`` time).  No global mutable state is used.

    Supports both sync and async route handlers.
    """

    def decorator(func: Callable) -> Callable:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                return await func(*args, **kwargs)

            async_wrapper._throttle_exempt = True  # type: ignore[attr-defined]
            return async_wrapper
        else:

            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                return func(*args, **kwargs)

            sync_wrapper._throttle_exempt = True  # type: ignore[attr-defined]
            return sync_wrapper

    return decorator
