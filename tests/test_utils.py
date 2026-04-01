"""
Tests for utility functions — IP extraction, key sanitisation, imports, hooks.
"""

import asyncio
import time
from unittest.mock import MagicMock

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from fastapi_gradual_throttle.utils import (
    _is_trusted_proxy,
    _sanitize,
    build_cache_key,
    calculate_sliding_window_count,
    call_hook,
    default_key_func,
    get_client_ip,
    get_throttle_reset_time_left,
    import_backend,
    import_callable,
    import_from_string,
    import_strategy,
    should_exempt_path,
)

# ---------------------------------------------------------------------------
# _is_trusted_proxy
# ---------------------------------------------------------------------------


class TestTrustedProxy:
    def test_empty_list(self):
        assert _is_trusted_proxy("10.0.0.1", []) is False

    def test_exact_match(self):
        assert _is_trusted_proxy("10.0.0.1", ["10.0.0.1"]) is True

    def test_cidr_match(self):
        assert _is_trusted_proxy("10.0.0.5", ["10.0.0.0/24"]) is True

    def test_cidr_no_match(self):
        assert _is_trusted_proxy("192.168.1.1", ["10.0.0.0/8"]) is False

    def test_invalid_ip(self):
        assert _is_trusted_proxy("not-an-ip", ["10.0.0.0/8"]) is False


# ---------------------------------------------------------------------------
# get_client_ip
# ---------------------------------------------------------------------------


def _make_request(client_host="127.0.0.1", headers=None):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
        ],
        "query_string": b"",
        "root_path": "",
        "server": ("localhost", 80),
    }
    if client_host:
        scope["client"] = (client_host, 12345)
    return Request(scope)


class TestGetClientIP:
    def test_direct_connection(self):
        req = _make_request(client_host="1.2.3.4")
        assert get_client_ip(req) == "1.2.3.4"

    def test_xff_ignored_without_trusted_proxy(self):
        req = _make_request(
            client_host="1.2.3.4",
            headers={"x-forwarded-for": "9.9.9.9, 10.0.0.1"},
        )
        # No trusted proxies → ignore XFF
        assert get_client_ip(req) == "1.2.3.4"

    def test_xff_used_with_trusted_proxy(self):
        req = _make_request(
            client_host="10.0.0.1",
            headers={"x-forwarded-for": "9.9.9.9, 10.0.0.1"},
        )
        assert get_client_ip(req, trusted_proxies=["10.0.0.0/8"]) == "9.9.9.9"

    def test_xrealip_with_trusted_proxy(self):
        req = _make_request(
            client_host="10.0.0.1",
            headers={"x-real-ip": "8.8.8.8"},
        )
        assert get_client_ip(req, trusted_proxies=["10.0.0.1"]) == "8.8.8.8"

    def test_no_client(self):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "query_string": b"",
            "root_path": "",
            "server": ("localhost", 80),
        }
        req = Request(scope)
        assert get_client_ip(req) == "127.0.0.1"


# ---------------------------------------------------------------------------
# _sanitize / build_cache_key
# ---------------------------------------------------------------------------


class TestSanitize:
    def test_normal_ip(self):
        assert _sanitize("192.168.1.1") == "192.168.1.1"

    def test_strips_specials(self):
        assert _sanitize("user\n123\t!@#") == "user123"

    def test_empty_input_uses_hash(self):
        result = _sanitize("!@#$%")
        assert len(result) == 32  # sha256 hex prefix

    def test_max_length(self):
        long_key = "a" * 200
        assert len(_sanitize(long_key)) == 128

    def test_build_cache_key(self):
        assert build_cache_key("throttle", "ip:1.2.3.4") == "throttle:ip:1.2.3.4"


# ---------------------------------------------------------------------------
# import_from_string / import_strategy / import_callable
# ---------------------------------------------------------------------------


class TestImports:
    def test_valid_import(self):
        cls = import_from_string(
            "fastapi_gradual_throttle.strategies.linear.LinearDelayStrategy"
        )
        from fastapi_gradual_throttle.strategies.linear import LinearDelayStrategy

        assert cls is LinearDelayStrategy

    def test_invalid_import(self):
        with pytest.raises(ImportError, match="Could not import"):
            import_from_string("nonexistent.module.Class")

    def test_import_strategy_valid(self):
        cls = import_strategy(
            "fastapi_gradual_throttle.strategies.linear.LinearDelayStrategy"
        )
        assert hasattr(cls, "calculate_delay")

    def test_import_strategy_invalid(self):
        with pytest.raises(ImportError, match="not a valid delay strategy"):
            import_strategy("fastapi_gradual_throttle.utils.get_client_ip")

    def test_import_callable_valid(self):
        fn = import_callable("fastapi_gradual_throttle.utils.get_client_ip")
        assert callable(fn)

    def test_import_callable_invalid(self):
        with pytest.raises(ImportError, match="not callable"):
            import_callable("fastapi_gradual_throttle.defaults.DEFAULT_RATE")


# ---------------------------------------------------------------------------
# should_exempt_path
# ---------------------------------------------------------------------------


class TestExemptPath:
    def test_match(self):
        assert should_exempt_path("/admin/dashboard", ["/admin/"]) is True

    def test_no_match(self):
        assert should_exempt_path("/api/users", ["/admin/"]) is False

    def test_empty_list(self):
        assert should_exempt_path("/anything", []) is False

    def test_exact_prefix(self):
        assert should_exempt_path("/health", ["/health"]) is True


# ---------------------------------------------------------------------------
# call_hook
# ---------------------------------------------------------------------------


class TestCallHook:
    @pytest.mark.asyncio
    async def test_none_hook(self):
        await call_hook(None)  # should not raise

    @pytest.mark.asyncio
    async def test_sync_hook(self):
        calls = []

        def hook(**kw):
            calls.append(kw)

        await call_hook(hook, action="test")
        assert len(calls) == 1
        assert calls[0]["action"] == "test"

    @pytest.mark.asyncio
    async def test_async_hook(self):
        calls = []

        async def hook(**kw):
            calls.append(kw)

        await call_hook(hook, action="async_test")
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_hook_exception_swallowed(self):
        def bad_hook(**kw):
            raise RuntimeError("boom")

        await call_hook(bad_hook)  # should not raise


# ---------------------------------------------------------------------------
# Window helpers
# ---------------------------------------------------------------------------


class TestWindowHelpers:
    def test_reset_time_left(self):
        now = time.time()
        left = get_throttle_reset_time_left(now, 60)
        assert 59 <= left <= 60

    def test_reset_time_expired(self):
        past = time.time() - 120
        left = get_throttle_reset_time_left(past, 60)
        assert left == 0.0

    def test_sliding_window_count_beginning(self):
        now = time.time()
        # Just started: 100% overlap from previous window
        result = calculate_sliding_window_count(5, 10, now, 60)
        assert result >= 14  # 5 + ~10

    def test_sliding_window_count_end(self):
        now = time.time()
        # Near end of window: minimal overlap
        result = calculate_sliding_window_count(5, 10, now - 59, 60)
        assert result <= 6  # ~5 + small fraction


# ---------------------------------------------------------------------------
# default_key_func
# ---------------------------------------------------------------------------


class TestDefaultKeyFunc:
    def test_ip_based_key(self):
        """Without user state, key should be IP-based."""
        req = _make_request(client_host="1.2.3.4")
        key = default_key_func(req)
        assert key.startswith("ip:")
        assert "1.2.3.4" in key

    def test_user_based_key(self):
        """With user.id on request.state, key should be user-based."""
        req = _make_request(client_host="1.2.3.4")

        class FakeUser:
            id = 42

        req.state.user = FakeUser()
        key = default_key_func(req)
        assert key.startswith("user:")
        assert "42" in key

    def test_user_with_no_id(self):
        """User object without id attribute falls back to IP."""
        req = _make_request(client_host="1.2.3.4")
        req.state.user = object()  # no .id attribute
        key = default_key_func(req)
        assert key.startswith("ip:")

    def test_user_with_none_id(self):
        """User with id=None falls back to IP."""
        req = _make_request(client_host="1.2.3.4")

        class FakeUser:
            id = None

        req.state.user = FakeUser()
        key = default_key_func(req)
        assert key.startswith("ip:")


# ---------------------------------------------------------------------------
# import_backend
# ---------------------------------------------------------------------------


class TestImportBackend:
    def test_valid_backend(self):
        cls = import_backend("fastapi_gradual_throttle.backends.memory.InMemoryBackend")
        from fastapi_gradual_throttle.backends.memory import InMemoryBackend

        assert cls is InMemoryBackend

    def test_invalid_backend(self):
        with pytest.raises(ImportError, match="not a valid backend"):
            import_backend("fastapi_gradual_throttle.utils.get_client_ip")


# ---------------------------------------------------------------------------
# reset_throttle_key
# ---------------------------------------------------------------------------


class TestResetThrottleKey:
    @pytest.mark.asyncio
    async def test_reset_with_app(self):
        from fastapi import FastAPI

        from fastapi_gradual_throttle import init_throttle
        from fastapi_gradual_throttle.utils import reset_throttle_key

        app = FastAPI()
        init_throttle(app, rate=10, window=60)
        backend = app.state.throttle_backend

        # Set a key, then reset it
        await backend.set(
            "throttle:ip:1.2.3.4", {"count": 5, "window_start": 1.0}, ttl=60
        )
        assert await backend.get("throttle:ip:1.2.3.4") is not None

        await reset_throttle_key("ip:1.2.3.4", app=app)
        assert await backend.get("throttle:ip:1.2.3.4") is None

    @pytest.mark.asyncio
    async def test_reset_with_explicit_backend(self):
        from fastapi_gradual_throttle.backends.memory import InMemoryBackend
        from fastapi_gradual_throttle.utils import reset_throttle_key

        backend = InMemoryBackend()
        await backend.set("throttle:ip:5.6.7.8", {"count": 3}, ttl=60)
        await reset_throttle_key("ip:5.6.7.8", backend=backend)
        assert await backend.get("throttle:ip:5.6.7.8") is None

    @pytest.mark.asyncio
    async def test_reset_no_app_or_backend_raises(self):
        from fastapi_gradual_throttle.utils import reset_throttle_key

        with pytest.raises(ValueError, match="Provide either"):
            await reset_throttle_key("somekey")

    @pytest.mark.asyncio
    async def test_reset_app_without_init_raises(self):
        from fastapi import FastAPI

        from fastapi_gradual_throttle.utils import reset_throttle_key

        app = FastAPI()
        with pytest.raises(RuntimeError, match="No global throttle config"):
            await reset_throttle_key("somekey", app=app)


# ---------------------------------------------------------------------------
# calculate_sliding_window_count edge cases
# ---------------------------------------------------------------------------


class TestSlidingWindowEdgeCases:
    def test_zero_window_seconds(self):
        """With window_seconds=0, should return current_count."""
        result = calculate_sliding_window_count(5, 10, time.time(), 0)
        assert result == 5

    def test_fully_elapsed_window(self):
        """When elapsed >= window_seconds, only current_count matters."""
        past = time.time() - 120  # 120 seconds ago
        result = calculate_sliding_window_count(5, 100, past, 60)
        assert result == 5


# ---------------------------------------------------------------------------
# _is_trusted_proxy edge cases
# ---------------------------------------------------------------------------


class TestTrustedProxyEdgeCases:
    def test_invalid_client_ip_returns_false(self):
        """Non-IP client address returns False."""
        assert _is_trusted_proxy("not-an-ip", ["10.0.0.0/8"]) is False

    def test_ipv6_address(self):
        assert _is_trusted_proxy("::1", ["::1/128"]) is True

    def test_ipv6_loopback_exact(self):
        assert _is_trusted_proxy("::1", ["::1"]) is True
