"""
ThrottleConfig — Pydantic Settings for fastapi-gradual-throttle.

Supports configuration via:
  A) Environment variables with ``FASTAPI_GRADUAL_THROTTLE_`` prefix
  B) Direct constructor kwargs
  C) A combination of both (explicit kwargs override env vars)
"""

import ipaddress
import warnings
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from . import defaults


class ThrottleConfig(BaseSettings):
    """
    Configuration for the gradual-throttle middleware, dependency, or decorator.

    All values have sensible defaults.  Override via environment variables
    (``FASTAPI_GRADUAL_THROTTLE_*``) or by passing kwargs directly.
    """

    # --- Feature Toggles ----
    enabled: bool = Field(
        default=defaults.DEFAULT_ENABLED,
        description="Master switch — set to False to disable all throttling.",
    )
    dry_run: bool = Field(
        default=defaults.DEFAULT_DRY_RUN,
        description="Log throttle actions without actually delaying or rejecting.",
    )

    # --- Mode ---
    mode: Literal["gradual", "strict", "combined"] = Field(
        default=defaults.DEFAULT_MODE,  # type: ignore[assignment]
        description=(
            "Throttle mode: 'gradual' adds progressive delay, 'strict' "
            "rejects immediately at the limit, 'combined' delays then rejects."
        ),
    )

    # --- Rate & Window ---
    rate: int = Field(
        default=defaults.DEFAULT_RATE,
        description="Maximum number of requests allowed per window.",
    )
    window: int = Field(
        default=defaults.DEFAULT_WINDOW,
        description="Window duration in seconds.",
    )
    window_type: Literal["fixed", "sliding", "token_bucket"] = Field(
        default=defaults.DEFAULT_WINDOW_TYPE,  # type: ignore[assignment]
        description=(
            "Window algorithm: 'fixed' resets at interval boundaries, "
            "'sliding' uses weighted approximation, 'token_bucket' refills tokens."
        ),
    )

    # --- Token Bucket ---
    burst_size: int = Field(
        default=defaults.DEFAULT_BURST_SIZE,
        description="Maximum burst capacity for token-bucket mode (0 = disabled).",
    )

    # --- Delay ---
    base_delay: float = Field(
        default=defaults.DEFAULT_BASE_DELAY,
        description="Base delay in seconds per excess request (gradual/combined).",
    )
    max_delay: float = Field(
        default=defaults.DEFAULT_MAX_DELAY,
        description="Maximum delay ceiling in seconds.",
    )

    # --- Hard Limit ---
    hard_limit: int = Field(
        default=defaults.DEFAULT_HARD_LIMIT,
        description="Absolute request ceiling that triggers a 429 (0 = disabled).",
    )

    # --- Dotted Import Paths ---
    key_func: str = Field(
        default=defaults.DEFAULT_KEY_FUNC,
        description="Dotted import path to the cache-key function.",
    )
    delay_strategy: str = Field(
        default=defaults.DEFAULT_DELAY_STRATEGY,
        description="Dotted import path to the delay-strategy class.",
    )

    # --- Exemptions ---
    exempt_paths: list[str] = Field(
        default_factory=list,
        description="URL path prefixes exempt from throttling.",
    )
    exempt_func: str | None = Field(
        default=None,
        description="Dotted path to a callable(request) -> bool for custom exemption.",
    )

    # --- Hook ---
    hook: str | None = Field(
        default=None,
        description="Dotted path to an event hook called on throttle actions.",
    )

    # --- Limit Function ---
    limit_func: str | None = Field(
        default=None,
        description="Dotted path to a callable(request) -> int for dynamic rate.",
    )

    # --- Headers ---
    headers_enabled: bool = Field(
        default=defaults.DEFAULT_HEADERS_ENABLED,
        description="Inject X-Throttle-* response headers.",
    )

    # --- Security ---
    trusted_proxies: list[str] = Field(
        default_factory=list,
        description="IP addresses or CIDRs trusted for X-Forwarded-For extraction.",
    )
    fail_open: bool = Field(
        default=defaults.DEFAULT_FAIL_OPEN,
        description="Allow requests through when the backend is unreachable.",
    )

    # --- WebSocket ---
    websocket_exempt: bool = Field(
        default=defaults.DEFAULT_WEBSOCKET_EXEMPT,
        description="Skip throttle processing for WebSocket upgrade requests.",
    )

    # --- Backend ---
    backend: str = Field(
        default=defaults.DEFAULT_BACKEND,
        description="Dotted import path to the storage backend class.",
    )
    backend_options: dict = Field(
        default_factory=dict,
        description="Keyword arguments forwarded to the backend constructor.",
    )
    key_prefix: str = Field(
        default=defaults.DEFAULT_KEY_PREFIX,
        description="Namespace prefix for all cache keys.",
    )

    # --- Response ---
    response_factory: str | None = Field(
        default=None,
        description="Dotted path to a callable(retry_after) -> bytes for custom 429 body.",
    )

    model_config = SettingsConfigDict(
        env_prefix="FASTAPI_GRADUAL_THROTTLE_",
        env_nested_delimiter="__",
    )

    # ---- Validators ----------------------------------------------------

    @field_validator("rate")
    @classmethod
    def rate_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("rate must be > 0")
        return v

    @field_validator("window")
    @classmethod
    def window_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("window must be > 0")
        return v

    @field_validator("base_delay")
    @classmethod
    def base_delay_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("base_delay must be >= 0")
        return v

    @field_validator("max_delay")
    @classmethod
    def max_delay_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("max_delay must be >= 0")
        return v

    @field_validator("hard_limit")
    @classmethod
    def hard_limit_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("hard_limit must be >= 0")
        return v

    @field_validator("burst_size")
    @classmethod
    def burst_size_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("burst_size must be >= 0")
        return v

    @field_validator("key_prefix")
    @classmethod
    def key_prefix_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("key_prefix must not be empty")
        return v.strip()

    @model_validator(mode="after")
    def validate_cross_field(self) -> "ThrottleConfig":  # noqa: N805
        """Validate field combinations that cannot be checked individually."""
        if self.max_delay < self.base_delay:
            raise ValueError(
                f"max_delay ({self.max_delay}) must be >= base_delay ({self.base_delay})"
            )
        if self.hard_limit > 0 and self.hard_limit < self.rate:
            raise ValueError(
                f"hard_limit ({self.hard_limit}) must be >= rate ({self.rate}) "
                "or 0 (disabled)"
            )
        # Fix 5: hard_limit with mode="strict" is meaningless
        if self.mode == "strict" and self.hard_limit > 0:
            raise ValueError(
                "hard_limit has no effect when mode='strict'. "
                "Remove hard_limit or switch to mode='combined'."
            )
        # Warn when mode="combined" but hard_limit is disabled — behaves like gradual
        if self.mode == "combined" and self.hard_limit == 0:
            warnings.warn(
                "mode='combined' has no effect without a hard_limit. "
                "Set hard_limit to a value > 0 or switch to mode='gradual'.",
                UserWarning,
                stacklevel=2,
            )
        # Token bucket requires burst_size > 0
        if self.window_type == "token_bucket" and self.burst_size <= 0:
            raise ValueError("burst_size must be > 0 when window_type='token_bucket'")
        # Warn if burst_size is set but window_type is not token_bucket
        if self.burst_size > 0 and self.window_type != "token_bucket":
            warnings.warn(
                "burst_size is set but window_type is not 'token_bucket' — "
                "burst_size will be ignored.",
                UserWarning,
                stacklevel=2,
            )
        # Fix 3: Warn about default key_prefix with Redis backend
        if (
            self.key_prefix == defaults.DEFAULT_KEY_PREFIX
            and "redis" in self.backend.lower()
        ):
            warnings.warn(
                "key_prefix is set to the default 'throttle'. If multiple apps share "
                "this Redis instance, set a unique key_prefix to avoid counter collisions.",
                UserWarning,
                stacklevel=2,
            )
        return self

    # Fix 4: Validate trusted_proxies CIDRs at startup
    @field_validator("trusted_proxies", mode="before")
    @classmethod
    def validate_trusted_proxies(cls, v: list[str]) -> list[str]:
        if not isinstance(v, list):
            v = list(v)
        for entry in v:
            try:
                ipaddress.ip_network(entry, strict=False)
            except ValueError:
                raise ValueError(f"Invalid CIDR in trusted_proxies: {entry!r}")
        return v
