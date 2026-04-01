"""Default configuration constants for fastapi-gradual-throttle.

Each constant is annotated with :data:`~typing.Final` so that type checkers
flag accidental re-assignment.  The names mirror :class:`ThrottleConfig`
field names with a ``DEFAULT_`` prefix.
"""

from typing import Final

# --- Rate & Window ---
DEFAULT_RATE: Final[int] = 60  # requests per window
DEFAULT_WINDOW: Final[int] = 60  # seconds

# --- Delay ---
DEFAULT_BASE_DELAY: Final[float] = 0.2  # seconds per excess request
DEFAULT_MAX_DELAY: Final[float] = 5.0  # seconds (ceiling)

# --- Mode ---
DEFAULT_MODE: Final[str] = "gradual"  # "gradual" | "strict" | "combined"

# --- Feature Toggles ---
DEFAULT_ENABLED: Final[bool] = True
DEFAULT_DRY_RUN: Final[bool] = False
DEFAULT_HEADERS_ENABLED: Final[bool] = True
DEFAULT_FAIL_OPEN: Final[bool] = True  # pass requests through on backend failure

# --- Hard Limit ---
DEFAULT_HARD_LIMIT: Final[int] = 0  # 0 = disabled

# --- Window Algorithm ---
DEFAULT_WINDOW_TYPE: Final[str] = "fixed"  # "fixed" | "sliding" | "token_bucket"

# --- Token Bucket ---
DEFAULT_BURST_SIZE: Final[int] = 0  # 0 = disabled, only for token_bucket

# --- Dotted Import Paths ---
DEFAULT_KEY_FUNC: Final[str] = "fastapi_gradual_throttle.utils.default_key_func"
DEFAULT_DELAY_STRATEGY: Final[str] = (
    "fastapi_gradual_throttle.strategies.linear.LinearDelayStrategy"
)
DEFAULT_BACKEND: Final[str] = "fastapi_gradual_throttle.backends.memory.InMemoryBackend"

# --- Exemptions ---
DEFAULT_EXEMPT_PATHS: Final[list[str]] = []
DEFAULT_EXEMPT_FUNC: Final[None] = None

# --- Hooks ---
DEFAULT_HOOK: Final[None] = None

# --- Limit Function ---
DEFAULT_LIMIT_FUNC: Final[None] = None

# --- Security ---
DEFAULT_TRUSTED_PROXIES: Final[list[str]] = []  # IPs/CIDRs trusted for XFF

# --- WebSocket ---
DEFAULT_WEBSOCKET_EXEMPT: Final[bool] = True  # skip throttling for WS upgrades

# --- Backend ---
DEFAULT_KEY_PREFIX: Final[str] = "throttle"  # cache key namespace

# --- Response ---
DEFAULT_RESPONSE_FACTORY: Final[None] = None  # custom 429 response callable
