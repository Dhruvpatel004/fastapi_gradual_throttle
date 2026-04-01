"""
FastAPI Gradual Throttle — progressive request throttling middleware.

Provides three throttle modes:
  - **gradual**: progressive delay that increases with excess requests
  - **strict**: immediate 429 when rate is exceeded (classic rate limiter)
  - **combined**: gradual delay with a hard-limit ceiling that triggers 429

Supports global, router-level, and per-route configuration.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("fastapi-gradual-throttle")
except PackageNotFoundError:  # package not installed (e.g. running from source)
    __version__ = "0.0.0.dev0"

from .backends.base import BaseBackend
from .backends.memory import InMemoryBackend
from .backends.redis import RedisBackend
from .config import ThrottleConfig
from .decorators import throttle
from .dependencies import GradualThrottle
from .exceptions import ThrottleException
from .exempt import throttle_exempt
from .initializer import init_throttle
from .middleware import GradualThrottleMiddleware
from .router import ThrottleRouter
from .strategies.base import BaseDelayStrategy
from .strategies.exponential import ExponentialDelayStrategy
from .strategies.linear import LinearDelayStrategy
from .strategies.none import NoDelayStrategy
from .utils import reset_throttle_key

__all__ = [
    # Middleware
    "GradualThrottleMiddleware",
    # Config
    "ThrottleConfig",
    "init_throttle",
    # Per-route
    "GradualThrottle",
    "throttle",
    "throttle_exempt",
    "ThrottleRouter",
    # Strategies
    "BaseDelayStrategy",
    "LinearDelayStrategy",
    "ExponentialDelayStrategy",
    "NoDelayStrategy",
    # Backends
    "BaseBackend",
    "InMemoryBackend",
    "RedisBackend",
    # Exceptions
    "ThrottleException",
    # Utilities
    "reset_throttle_key",
]
