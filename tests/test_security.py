"""
Security-focused tests — IP spoofing, cache key injection, import validation.
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from fastapi_gradual_throttle import GradualThrottleMiddleware
from fastapi_gradual_throttle.utils import (
    _sanitize,
    build_cache_key,
    get_client_ip,
    import_callable,
    import_strategy,
)


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


class TestIPSpoofing:
    def test_xff_ignored_by_default(self):
        """Without trusted_proxies, XFF header is ignored."""
        req = _make_request(
            client_host="1.1.1.1",
            headers={"x-forwarded-for": "9.9.9.9"},
        )
        assert get_client_ip(req) == "1.1.1.1"

    def test_xff_not_trusted_from_arbitrary_client(self):
        """XFF from a non-trusted client is ignored."""
        req = _make_request(
            client_host="1.1.1.1",
            headers={"x-forwarded-for": "9.9.9.9"},
        )
        assert get_client_ip(req, trusted_proxies=["10.0.0.0/8"]) == "1.1.1.1"

    def test_xff_trusted_from_known_proxy(self):
        """XFF from a trusted proxy is accepted."""
        req = _make_request(
            client_host="10.0.0.1",
            headers={"x-forwarded-for": "9.9.9.9"},
        )
        assert get_client_ip(req, trusted_proxies=["10.0.0.0/8"]) == "9.9.9.9"


class TestCacheKeyInjection:
    def test_special_chars_stripped(self):
        assert _sanitize("user\n123\t!@#") == "user123"

    def test_unicode_stripped(self):
        assert _sanitize("user_\u202e_test") == "user__test"

    def test_empty_becomes_hash(self):
        result = _sanitize("!@#$%^&*()")
        assert len(result) == 32
        assert result.isalnum()

    def test_very_long_key_truncated(self):
        long = "a" * 500
        assert len(_sanitize(long)) == 128


class TestImportValidation:
    def test_strategy_must_have_calculate_delay(self):
        with pytest.raises(ImportError, match="not a valid delay strategy"):
            import_strategy("fastapi_gradual_throttle.utils.get_client_ip")

    def test_callable_must_be_callable(self):
        with pytest.raises(ImportError, match="not callable"):
            import_callable("fastapi_gradual_throttle.defaults.DEFAULT_RATE")

    def test_invalid_path_raises(self):
        with pytest.raises(ImportError, match="Could not import"):
            import_callable("nonexistent.module.func")


@pytest.mark.asyncio
class TestMiddlewareSecurity:
    async def test_spoofed_xff_same_rate_limit(self):
        """
        Attacker rotates X-Forwarded-For but trusted_proxies is empty,
        so all requests are keyed by the actual client IP.
        """
        app = FastAPI()

        @app.get("/test")
        async def test_endpoint(request: Request):
            return JSONResponse({"status": "ok"})

        app.add_middleware(
            GradualThrottleMiddleware,
            rate=3,
            window=60,
            mode="strict",
            # No trusted_proxies — XFF is ignored
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for i in range(3):
                resp = await c.get(
                    "/test",
                    headers={"x-forwarded-for": f"10.0.0.{i}"},
                )
                assert resp.status_code == 200

            # 4th request exceeds despite different XFF
            resp = await c.get(
                "/test",
                headers={"x-forwarded-for": "10.0.0.99"},
            )
            assert resp.status_code == 429
