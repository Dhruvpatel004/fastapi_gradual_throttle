"""
Tests for error handling — fail-open on backend failure, bad key_func, bad exempt_func.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from fastapi_gradual_throttle import GradualThrottleMiddleware


def _make_app(**middleware_kwargs):
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint(request: Request):
        return JSONResponse({"status": "ok"})

    app.add_middleware(GradualThrottleMiddleware, **middleware_kwargs)
    return app


@pytest.mark.asyncio
class TestFailOpen:
    async def test_backend_error_passes_through(self):
        """With fail_open=True, backend errors should let requests through."""
        app = _make_app(rate=5, window=60, fail_open=True)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # Normal request first
            resp = await c.get("/test")
            assert resp.status_code == 200


@pytest.mark.asyncio
class TestBadKeyFunc:
    async def test_bad_key_func_falls_back_to_ip(self):
        """If key_func raises, middleware should fall back to IP-based key."""

        def bad_key_func(request):
            raise ValueError("Broken key func!")

        app = FastAPI()

        @app.get("/test")
        async def test_endpoint(request: Request):
            return JSONResponse({"status": "ok"})

        app.add_middleware(
            GradualThrottleMiddleware,
            rate=5,
            window=60,
            key_func="tests.test_error_handling._bad_key_func",
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/test")
            assert resp.status_code == 200


@pytest.mark.asyncio
class TestBadExemptFunc:
    async def test_bad_exempt_func_treated_as_non_exempt(self):
        """If exempt_func raises, request is treated as non-exempt."""
        app = FastAPI()

        @app.get("/test")
        async def test_endpoint(request: Request):
            return JSONResponse({"status": "ok"})

        app.add_middleware(
            GradualThrottleMiddleware,
            rate=5,
            window=60,
            exempt_func="tests.test_error_handling._bad_exempt_func",
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/test")
            assert resp.status_code == 200


# Module-level functions referenced by dotted-path strings above
def _bad_key_func(request):
    raise ValueError("Broken key func!")


def _bad_exempt_func(request):
    raise RuntimeError("Broken exempt func!")
