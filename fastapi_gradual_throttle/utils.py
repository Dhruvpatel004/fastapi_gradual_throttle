"""
Utility functions for fastapi-gradual-throttle.

Includes secure IP extraction, cache key sanitisation, dynamic imports
with type checking, exemption helpers, and async-safe hook execution.
"""

import asyncio
import hashlib
import importlib
import inspect
import ipaddress
import logging
import re
import time
from typing import Any, Callable

from starlette.requests import Request

logger = logging.getLogger("fastapi_gradual_throttle")

# Allowed characters for cache key segments — everything else is replaced.
_SAFE_KEY_RE = re.compile(r"[^a-zA-Z0-9._:-]")


# ---------------------------------------------------------------------------
# IP extraction (secure)
# ---------------------------------------------------------------------------


def _is_trusted_proxy(client_ip: str, trusted_proxies: list[str]) -> bool:
    """
    Return ``True`` if *client_ip* matches any entry in *trusted_proxies*.

    Each entry can be:
      - a single IP  (``"10.0.0.1"``)
      - a CIDR block (``"10.0.0.0/8"``)
    """
    if not trusted_proxies:
        return False
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for proxy in trusted_proxies:
        try:
            network = ipaddress.ip_network(proxy, strict=False)
            if addr in network:
                return True
        except ValueError:
            if client_ip == proxy:
                return True
    return False


def get_client_ip(request: Request, trusted_proxies: list[str] | None = None) -> str:
    """
    Extract the *real* client IP from a Starlette request.

    Security: ``X-Forwarded-For`` and ``X-Real-IP`` are only trusted when
    ``request.client.host`` matches an entry in *trusted_proxies*.
    If *trusted_proxies* is empty (the default), the direct socket IP is used.
    """
    client_host = request.client.host if request.client else "127.0.0.1"

    if trusted_proxies and _is_trusted_proxy(client_host, trusted_proxies):
        xff = request.headers.get("x-forwarded-for")
        if xff:
            # Take the first non-empty segment (leftmost = originating client).
            return xff.split(",")[0].strip()
        xri = request.headers.get("x-real-ip")
        if xri:
            return xri.strip()

    return client_host


# ---------------------------------------------------------------------------
# Default key function
# ---------------------------------------------------------------------------


def default_key_func(request: Request) -> str:
    """
    Build a throttle cache key from the request.

    Priority: authenticated user id → client IP.
    """
    user = getattr(request.state, "user", None)
    if user and getattr(user, "id", None) is not None:
        return f"user:{_sanitize(str(user.id))}"
    ip = get_client_ip(
        request,
        trusted_proxies=getattr(request.state, "_throttle_trusted_proxies", None),
    )
    return f"ip:{_sanitize(ip)}"


# ---------------------------------------------------------------------------
# Cache-key sanitisation
# ---------------------------------------------------------------------------


def _sanitize(raw: str, max_len: int = 128) -> str:
    """Strip unsafe characters and cap length."""
    cleaned = _SAFE_KEY_RE.sub("", raw)
    if not cleaned:
        # Degenerate input — use a hash so the key is still unique.
        cleaned = hashlib.sha256(raw.encode()).hexdigest()[:32]
    return cleaned[:max_len]


def build_cache_key(prefix: str, raw_key: str) -> str:
    """Assemble the final cache key with a prefix."""
    return f"{prefix}:{raw_key}"


# ---------------------------------------------------------------------------
# Dynamic import with type validation
# ---------------------------------------------------------------------------


def import_from_string(import_string: str) -> Any:
    """
    Import a class or function from a dotted path string.

    Raises ``ImportError`` if the path cannot be resolved.
    """
    try:
        module_path, attr_name = import_string.rsplit(".", 1)
        module = importlib.import_module(module_path)
        return getattr(module, attr_name)
    except (ValueError, ImportError, AttributeError) as exc:
        raise ImportError(f"Could not import '{import_string}': {exc}") from exc


def import_strategy(import_string: str) -> Any:
    """Import a delay-strategy class and validate it has ``calculate_delay``."""
    cls = import_from_string(import_string)
    if not (isinstance(cls, type) and hasattr(cls, "calculate_delay")):
        raise ImportError(
            f"'{import_string}' is not a valid delay strategy "
            "(must be a class with a calculate_delay method)"
        )
    return cls


def import_backend(import_string: str) -> Any:
    """Import a backend class and validate it has ``get`` and ``set``."""
    cls = import_from_string(import_string)
    if not (isinstance(cls, type) and hasattr(cls, "get") and hasattr(cls, "set")):
        raise ImportError(
            f"'{import_string}' is not a valid backend "
            "(must be a class with async get/set methods)"
        )
    return cls


def import_callable(import_string: str) -> Callable:
    """Import a callable and verify it is indeed callable."""
    obj = import_from_string(import_string)
    if not callable(obj):
        raise ImportError(f"'{import_string}' is not callable")
    return obj


# ---------------------------------------------------------------------------
# Exemption checks
# ---------------------------------------------------------------------------


def should_exempt_path(request_path: str, exempt_paths: list[str]) -> bool:
    """Return ``True`` if *request_path* starts with any exempt prefix."""
    return any(request_path.startswith(p) for p in exempt_paths)


# ---------------------------------------------------------------------------
# Hook execution (async-safe)
# ---------------------------------------------------------------------------


async def call_hook(hook_func: Callable | None, **kwargs: Any) -> None:
    """
    Call *hook_func* (sync or async) if provided.

    Exceptions are logged and swallowed to avoid crashing the middleware.
    """
    if hook_func is None:
        return
    try:
        if inspect.iscoroutinefunction(hook_func):
            await hook_func(**kwargs)
        else:
            hook_func(**kwargs)
    except Exception:
        logger.warning("Hook function failed", exc_info=True)


# ---------------------------------------------------------------------------
# Window helpers
# ---------------------------------------------------------------------------


def get_throttle_reset_time_left(window_start: float, window_seconds: int) -> float:
    """Seconds remaining until the current throttle window resets."""
    return max(0.0, (window_start + window_seconds) - time.time())


async def reset_throttle_key(
    raw_key: str,
    *,
    app: Any = None,
    backend: Any = None,
    key_prefix: str | None = None,
) -> None:
    """
    Reset (delete) the throttle counter for *raw_key*.

    Resolves the backend and key prefix from the app's global config
    (set via ``init_throttle``) or from explicit arguments.

    Usage::

        await reset_throttle_key("ip:1.2.3.4", app=app)
    """
    if backend is None:
        if app is None:
            raise ValueError("Provide either 'app' or 'backend'")
        # Reuse the shared backend from init_throttle() if available
        shared_backend = getattr(app.state, "throttle_backend", None)
        if shared_backend is not None:
            backend = shared_backend
        else:
            cfg = getattr(app.state, "throttle_config", None)
            if cfg is None:
                raise RuntimeError(
                    "No global throttle config found on app. "
                    "Call init_throttle(app, ...) first or pass backend explicitly."
                )
            from .utils import import_backend as _import_backend

            backend_cls = _import_backend(cfg.backend)
            backend = backend_cls(**cfg.backend_options)
        if key_prefix is None:
            cfg = getattr(app.state, "throttle_config", None)
            if cfg is not None:
                key_prefix = cfg.key_prefix

    if key_prefix is None:
        from . import defaults

        key_prefix = defaults.DEFAULT_KEY_PREFIX

    full_key = build_cache_key(key_prefix, raw_key)
    await backend.reset(full_key)


def calculate_sliding_window_count(
    current_count: int,
    previous_count: int,
    window_start: float,
    window_seconds: int,
) -> int:
    """
    Weighted sliding-window approximation.

    ``effective = current_count + previous_count * overlap_ratio``

    This smooths burst spikes at fixed-window boundaries.
    """
    elapsed = time.time() - window_start
    if elapsed >= window_seconds or window_seconds == 0:
        return current_count
    overlap_ratio = 1.0 - (elapsed / window_seconds)
    return current_count + int(previous_count * overlap_ratio)
