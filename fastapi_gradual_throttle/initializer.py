"""
Global initialisation helper for fastapi-gradual-throttle.

Call :func:`init_throttle` once at application startup to store a shared
configuration and backend that the middleware, dependencies, and decorators
all inherit from.

Usage (simple - direct call)::

    from fastapi import FastAPI
    from fastapi_gradual_throttle import init_throttle, ThrottleConfig, GradualThrottleMiddleware

    app = FastAPI()
    config = ThrottleConfig(rate=100, window=60)
    init_throttle(app, config=config)
    app.add_middleware(GradualThrottleMiddleware, config=config)

Usage (recommended - FastAPI lifespan context manager)::

    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    from fastapi_gradual_throttle import init_throttle, ThrottleConfig, GradualThrottleMiddleware

    config = ThrottleConfig(rate=100, window=60)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: initialize throttle
        init_throttle(app, config=config)
        yield
        # Shutdown: cleanup if needed (backends handle cleanup naturally)

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(GradualThrottleMiddleware, config=config)
"""

import logging
from typing import Any

from .config import ThrottleConfig
from .utils import import_backend

logger = logging.getLogger("fastapi_gradual_throttle")


def init_throttle(
    app: Any, config: ThrottleConfig | None = None, **kwargs: Any
) -> ThrottleConfig:
    """
    Store a global :class:`ThrottleConfig` on ``app.state.throttle_config``.

    Also instantiates and stores the backend on ``app.state.throttle_backend``
    so that @throttle() decorators and Depends(GradualThrottle()) can reuse
    it instead of creating their own connections.

    Scans app routes eagerly and caches exempt / per-route-throttled paths on
    ``app.state`` so the middleware can skip them without any global state.

    Accepts either a pre-built ``config`` object or individual
    :class:`ThrottleConfig` field kwargs (e.g. ``rate=100, window=60``).

    Returns the resolved config object.

    Examples:
        Simple direct initialization::

            app = FastAPI()
            config = ThrottleConfig(rate=100, window=60)
            init_throttle(app, config=config)
            app.add_middleware(GradualThrottleMiddleware, config=config)

        With FastAPI lifespan (recommended)::

            from contextlib import asynccontextmanager

            config = ThrottleConfig(rate=100, window=60)

            @asynccontextmanager
            async def lifespan(app: FastAPI):
                init_throttle(app, config=config)
                yield

            app = FastAPI(lifespan=lifespan)
            app.add_middleware(GradualThrottleMiddleware, config=config)

        Using kwargs instead of config object::

            init_throttle(app, rate=100, window=60, mode="gradual")
    """
    if config is not None:
        cfg = config
    elif kwargs:
        cfg = ThrottleConfig(**kwargs)
    else:
        cfg = ThrottleConfig()

    app.state.throttle_config = cfg

    # Create and store a shared backend instance
    backend_cls = import_backend(cfg.backend)
    backend_instance = backend_cls(**cfg.backend_options)
    app.state.throttle_backend = backend_instance

    # Eagerly build the per-app path cache so the middleware benefits from
    # startup-time route scanning rather than deferring to first request.
    from .middleware import GradualThrottleMiddleware

    GradualThrottleMiddleware._build_path_cache(app)

    logger.info(
        "Global throttle config initialised: mode=%s rate=%d/%ds",
        cfg.mode,
        cfg.rate,
        cfg.window,
    )
    return cfg
