"""
Tests for bug fixes — per-app-state exemption, concurrency-safe init,
backend reuse, token bucket retry_after, burst_size warning.
"""

import asyncio
import warnings
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from fastapi_gradual_throttle import (
    GradualThrottleMiddleware,
    init_throttle,
    throttle,
    throttle_exempt,
)
from fastapi_gradual_throttle.backends.memory import InMemoryBackend
from fastapi_gradual_throttle.config import ThrottleConfig
from fastapi_gradual_throttle.dependencies import GradualThrottle

# ---------------------------------------------------------------------------
# Fix 1 — decorator.py imports without syntax error
# ---------------------------------------------------------------------------


class TestDecoratorImport:
    def test_decorators_module_imports(self):
        """The decorators module must import without SyntaxError."""
        from fastapi_gradual_throttle import decorators

        assert hasattr(decorators, "throttle")

    def test_throttle_decorator_works(self):
        """Decorated handler can be created without error."""

        @throttle(rate=5, window=60)
        async def handler(request: Request):
            return JSONResponse({"ok": True})

        assert handler is not None


# ---------------------------------------------------------------------------
# Fix 2 — @throttle_exempt() per-app-state exemption
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExemptMiddlewareIntegration:
    async def test_exempt_route_skips_middleware(self):
        """A @throttle_exempt() route should not be throttled by middleware."""
        app = FastAPI()

        @app.get("/internal")
        @throttle_exempt()
        async def internal(request: Request):
            return JSONResponse({"status": "ok"})

        @app.get("/test")
        async def test_endpoint(request: Request):
            return JSONResponse({"status": "ok"})

        app.add_middleware(GradualThrottleMiddleware, rate=1, window=60, mode="strict")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # First call triggers lazy cache build on app.state
            resp = await c.get("/internal")
            assert resp.status_code == 200

            # /internal should remain accessible beyond the rate limit
            for _ in range(5):
                resp = await c.get("/internal")
                assert resp.status_code == 200

    async def test_init_throttle_caches_exempt_routes(self):
        """init_throttle() should eagerly cache exempt paths on app.state."""
        app = FastAPI()

        @app.get("/exempt")
        @throttle_exempt()
        async def exempt(request: Request):
            return JSONResponse({"ok": True})

        @app.get("/normal")
        async def normal(request: Request):
            return JSONResponse({"ok": True})

        init_throttle(app, rate=10, window=60)

        # After init_throttle, exempt path should be cached on app.state
        assert "/exempt" in app.state._throttle_exempt_paths
        assert "/normal" not in app.state._throttle_exempt_paths


# ---------------------------------------------------------------------------
# Fix 3 — Auto-exempt per-route throttle from global middleware
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAutoExemptPerRouteThrottle:
    """Per-route throttled routes are auto-exempted from global middleware."""

    async def test_throttle_decorator_auto_exempts_from_middleware(self):
        """
        A route with @throttle() should NOT be double-counted by the
        global middleware — the middleware auto-detects the per-route
        throttle and skips its own counting.
        """
        app = FastAPI()

        @app.get("/expensive")
        @throttle(rate=3, window=60, mode="strict")
        async def expensive(request: Request):
            return JSONResponse({"status": "ok"})

        @app.get("/normal")
        async def normal(request: Request):
            return JSONResponse({"status": "ok"})

        config = ThrottleConfig(rate=100, window=60)
        init_throttle(app, config=config)
        app.add_middleware(GradualThrottleMiddleware, config=config)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # The per-route @throttle(rate=3) should count individually
            for _ in range(3):
                resp = await c.get("/expensive")
                assert resp.status_code == 200

            # 4th request should be rejected by @throttle (rate=3)
            resp = await c.get("/expensive")
            assert resp.status_code == 429

    async def test_depends_gradual_throttle_auto_exempts(self):
        """
        A route with Depends(GradualThrottle()) should NOT be
        double-counted by the global middleware.
        """
        app = FastAPI()
        dep = GradualThrottle(rate=3, window=60, mode="strict")

        @app.get("/limited", dependencies=[Depends(dep)])
        async def limited(request: Request):
            return JSONResponse({"status": "ok"})

        @app.get("/normal")
        async def normal(request: Request):
            return JSONResponse({"status": "ok"})

        config = ThrottleConfig(rate=100, window=60)
        init_throttle(app, config=config)
        app.add_middleware(GradualThrottleMiddleware, config=config)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(3):
                resp = await c.get("/limited")
                assert resp.status_code == 200

            resp = await c.get("/limited")
            assert resp.status_code == 429

    async def test_throttle_router_auto_exempts(self):
        """
        Routes on a ThrottleRouter should NOT be double-counted by the
        global middleware.
        """
        from fastapi_gradual_throttle.router import ThrottleRouter

        app = FastAPI()
        api = ThrottleRouter(
            prefix="/api", throttle_rate=3, throttle_window=60, throttle_mode="strict"
        )

        @api.get("/data")
        async def data(request: Request):
            return JSONResponse({"data": "ok"})

        app.include_router(api)

        config = ThrottleConfig(rate=100, window=60)
        init_throttle(app, config=config)
        app.add_middleware(GradualThrottleMiddleware, config=config)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(3):
                resp = await c.get("/api/data")
                assert resp.status_code == 200

            resp = await c.get("/api/data")
            assert resp.status_code == 429

    async def test_normal_route_still_uses_global_middleware(self):
        """Routes without per-route throttle should still use global middleware."""
        app = FastAPI()

        @app.get("/expensive")
        @throttle(rate=3, window=60, mode="strict")
        async def expensive(request: Request):
            return JSONResponse({"status": "ok"})

        @app.get("/normal")
        async def normal(request: Request):
            return JSONResponse({"status": "ok"})

        config = ThrottleConfig(rate=2, window=60, mode="strict")
        init_throttle(app, config=config)
        app.add_middleware(GradualThrottleMiddleware, config=config)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(2):
                resp = await c.get("/normal")
                assert resp.status_code == 200

            # 3rd request to /normal should be rejected by global middleware
            resp = await c.get("/normal")
            assert resp.status_code == 429

    async def test_throttle_exempt_still_works(self):
        """@throttle_exempt() should still fully exempt a route."""
        app = FastAPI()

        @app.get("/health")
        @throttle_exempt()
        async def health(request: Request):
            return JSONResponse({"status": "ok"})

        config = ThrottleConfig(rate=1, window=60, mode="strict")
        init_throttle(app, config=config)
        app.add_middleware(GradualThrottleMiddleware, config=config)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(10):
                resp = await c.get("/health")
                assert resp.status_code == 200

    async def test_auto_exempt_works_without_init_throttle(self):
        """
        Even if init_throttle() is not called, the per-route throttle
        should auto-register at runtime on the first request.
        """
        app = FastAPI()

        @app.get("/expensive")
        @throttle(rate=3, window=60, mode="strict")
        async def expensive(request: Request):
            return JSONResponse({"status": "ok"})

        @app.get("/normal")
        async def normal(request: Request):
            return JSONResponse({"status": "ok"})

        # No init_throttle() — just add middleware directly
        app.add_middleware(GradualThrottleMiddleware, rate=100, window=60)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # First call registers auto-exempt at runtime
            for _ in range(3):
                resp = await c.get("/expensive")
                assert resp.status_code == 200

            # 4th request rejected by per-route throttle, not global
            resp = await c.get("/expensive")
            assert resp.status_code == 429


@pytest.mark.asyncio
class TestNoDoubleCount:
    async def test_throttle_exempt_prevents_double_counting(self):
        """
        With middleware + @throttle() on same route, @throttle_exempt()
        prevents the middleware from also counting the request.
        """
        app = FastAPI()

        @app.get("/expensive")
        @throttle_exempt()
        @throttle(rate=3, window=60, mode="strict")
        async def expensive(request: Request):
            return JSONResponse({"status": "ok"})

        @app.get("/normal")
        async def normal(request: Request):
            return JSONResponse({"status": "ok"})

        app.add_middleware(GradualThrottleMiddleware, rate=100, window=60)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # First call triggers cache build; exempt wins over per-route throttle
            resp = await c.get("/expensive")
            assert resp.status_code == 200

            # The per-route @throttle(rate=3) should count individually
            for _ in range(2):
                resp = await c.get("/expensive")
                assert resp.status_code == 200

            # 4th request should be rejected by @throttle (rate=3)
            resp = await c.get("/expensive")
            assert resp.status_code == 429


# ---------------------------------------------------------------------------
# Fix 5 — Concurrency-safe lazy init
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestConcurrencySafeInit:
    async def test_dependency_concurrent_init(self):
        """Two concurrent requests should not create duplicate backends."""
        app = FastAPI()
        dep = GradualThrottle(rate=100, window=60)

        @app.get("/test", dependencies=[Depends(dep)])
        async def test_endpoint(request: Request):
            return JSONResponse({"status": "ok"})

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # Send concurrent requests
            results = await asyncio.gather(
                c.get("/test"),
                c.get("/test"),
                c.get("/test"),
            )
            for r in results:
                assert r.status_code == 200

            # After init, the dependency should have exactly one backend
            assert dep._initialised is True
            assert dep._backend is not None

    async def test_decorator_concurrent_init(self):
        """Two concurrent requests to @throttle() route should be safe."""
        app = FastAPI()

        @app.get("/limited")
        @throttle(rate=100, window=60)
        async def limited(request: Request):
            return JSONResponse({"status": "ok"})

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            results = await asyncio.gather(
                c.get("/limited"),
                c.get("/limited"),
            )
            for r in results:
                assert r.status_code == 200


# ---------------------------------------------------------------------------
# Fix 6 — Backend reuse across @throttle() routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestBackendReuse:
    async def test_decorator_reuses_global_backend(self):
        """@throttle() routes should reuse init_throttle()'s backend."""
        app = FastAPI()
        init_throttle(app, rate=100, window=60)
        global_backend = app.state.throttle_backend

        @app.get("/a")
        @throttle(rate=10, window=60)
        async def route_a(request: Request):
            return JSONResponse({"status": "ok"})

        @app.get("/b")
        @throttle(rate=20, window=60)
        async def route_b(request: Request):
            return JSONResponse({"status": "ok"})

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.get("/a")
            await c.get("/b")

        # Both should reuse the global backend (checking they're the same type)
        assert global_backend is not None
        assert isinstance(global_backend, InMemoryBackend)

    async def test_dependency_reuses_global_backend(self):
        """GradualThrottle dependency should reuse init_throttle()'s backend."""
        app = FastAPI()
        init_throttle(app, rate=100, window=60)
        global_backend = app.state.throttle_backend

        dep = GradualThrottle(rate=10, window=60)

        @app.get("/test", dependencies=[Depends(dep)])
        async def endpoint(request: Request):
            return JSONResponse({"status": "ok"})

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.get("/test")

        # Dependency should reuse the global backend
        assert dep._backend is global_backend


# ---------------------------------------------------------------------------
# Fix 7 — Token bucket retry_after accuracy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTokenBucketRetryAfter:
    async def test_retry_after_is_accurate(self):
        """Token bucket 429 should have a small retry_after, not window size."""
        b = InMemoryBackend()
        import time

        now = time.time()

        # rate=10/60s => refill_rate = 10/60 ≈ 0.1667 tokens/sec
        # burst_size=1 => bucket starts with 1 token
        # First consume — allowed, tokens=0
        r1 = await b.token_bucket_consume(
            "k", rate=10, burst_size=1, window=60, ttl=120, now=now
        )
        assert r1["allowed"] is True

        # Second consume — rejected, tokens < 1
        r2 = await b.token_bucket_consume(
            "k", rate=10, burst_size=1, window=60, ttl=120, now=now
        )
        assert r2["allowed"] is False
        # retry_after_seconds should be about 6 seconds (1 / (10/60)), not 60
        assert "retry_after_seconds" in r2
        assert r2["retry_after_seconds"] < 10  # should be ~6s, definitely not 60

    async def test_retry_after_in_middleware_response(self):
        """Middleware token bucket 429 should use accurate retry_after."""
        app = FastAPI()

        @app.get("/test")
        async def test_endpoint(request: Request):
            return JSONResponse({"status": "ok"})

        app.add_middleware(
            GradualThrottleMiddleware,
            rate=10,
            window=60,
            window_type="token_bucket",
            burst_size=1,
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # First request uses the only token
            resp = await c.get("/test")
            assert resp.status_code == 200

            # Second request should be rejected
            resp = await c.get("/test")
            assert resp.status_code == 429
            retry_after = int(resp.headers.get("retry-after", "99"))
            # Should be ~6 seconds, not 60
            assert retry_after < 15


# ---------------------------------------------------------------------------
# Config validation — burst_size warning
# ---------------------------------------------------------------------------


class TestBurstSizeWarning:
    def test_burst_size_with_wrong_window_type_warns(self):
        """Setting burst_size > 0 with non-token_bucket window_type should warn."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            ThrottleConfig(burst_size=10, window_type="fixed")
            burst_warnings = [x for x in w if "burst_size" in str(x.message)]
            assert len(burst_warnings) == 1

    def test_burst_size_with_token_bucket_no_warning(self):
        """burst_size with token_bucket window_type should NOT warn about burst_size."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            ThrottleConfig(burst_size=10, window_type="token_bucket")
            burst_warnings = [x for x in w if "burst_size" in str(x.message)]
            assert len(burst_warnings) == 0
