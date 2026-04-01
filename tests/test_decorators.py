"""
Tests for the @throttle() decorator.
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from fastapi_gradual_throttle.decorators import throttle


def _make_app_decorator(**dec_kwargs):
    app = FastAPI()

    @app.get("/limited")
    @throttle(**dec_kwargs)
    async def limited(request: Request):
        return JSONResponse({"status": "ok"})

    @app.get("/free")
    async def free():
        return JSONResponse({"status": "free"})

    return app


@pytest.mark.asyncio
class TestDecoratorGradual:
    async def test_within_rate(self):
        app = _make_app_decorator(rate=5, window=60)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(5):
                resp = await c.get("/limited")
                assert resp.status_code == 200

    async def test_headers_injected(self):
        app = _make_app_decorator(rate=10, window=60)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/limited")
            assert resp.status_code == 200
            assert "x-throttle-remaining" in resp.headers
            assert "x-throttle-limit" in resp.headers

    async def test_free_endpoint_unaffected(self):
        app = _make_app_decorator(rate=1, window=60)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.get("/limited")
            for _ in range(10):
                resp = await c.get("/free")
                assert resp.status_code == 200


@pytest.mark.asyncio
class TestDecoratorStrict:
    async def test_strict_returns_429(self):
        app = _make_app_decorator(rate=2, window=60, mode="strict")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(2):
                await c.get("/limited")
            resp = await c.get("/limited")
            assert resp.status_code == 429


@pytest.mark.asyncio
class TestDecoratorHardLimit:
    async def test_hard_limit_returns_429(self):
        app = _make_app_decorator(rate=2, window=60, hard_limit=4)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(4):
                await c.get("/limited")
            resp = await c.get("/limited")
            assert resp.status_code == 429


@pytest.mark.asyncio
class TestDecoratorDryRun:
    async def test_dry_run_no_block(self):
        app = _make_app_decorator(rate=1, window=60, dry_run=True)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.get("/limited")
            resp = await c.get("/limited")
            assert resp.status_code == 200  # not blocked in dry-run


class TestDecoratorRequiresRequest:
    def test_raises_if_no_request_param(self):
        with pytest.raises(TypeError, match="request: Request"):

            @throttle(rate=5, window=60)
            async def bad_handler():
                pass


@pytest.mark.asyncio
class TestDecoratorDisabled:
    async def test_disabled_skips_throttle(self):
        app = _make_app_decorator(rate=1, window=60, enabled=False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(5):
                resp = await c.get("/limited")
                assert resp.status_code == 200


@pytest.mark.asyncio
class TestDecoratorTokenBucket:
    async def test_token_bucket_allows_burst(self):
        app = _make_app_decorator(
            rate=10, window=60, window_type="token_bucket", burst_size=3
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(3):
                resp = await c.get("/limited")
                assert resp.status_code == 200
            resp = await c.get("/limited")
            assert resp.status_code == 429

    async def test_token_bucket_headers(self):
        app = _make_app_decorator(
            rate=10, window=60, window_type="token_bucket", burst_size=5
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/limited")
            assert resp.status_code == 200
            assert "x-throttle-remaining" in resp.headers
            assert "x-throttle-limit" in resp.headers


@pytest.mark.asyncio
class TestDecoratorSlidingWindow:
    async def test_sliding_window_works(self):
        app = _make_app_decorator(rate=5, window=60, window_type="sliding")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(5):
                resp = await c.get("/limited")
                assert resp.status_code == 200


@pytest.mark.asyncio
class TestDecoratorCombinedMode:
    async def test_combined_delay_then_reject(self):
        app = _make_app_decorator(rate=2, window=60, mode="combined", hard_limit=4)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(4):
                resp = await c.get("/limited")
                assert resp.status_code == 200
            resp = await c.get("/limited")
            assert resp.status_code == 429


@pytest.mark.asyncio
class TestDecoratorBackendFailure:
    async def test_fail_open_on_backend_error(self):
        from unittest.mock import AsyncMock, patch

        app = _make_app_decorator(rate=5, window=60)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # First request initializes the backend
            resp = await c.get("/limited")
            assert resp.status_code == 200
            # Now break the backend
            with patch(
                "fastapi_gradual_throttle.backends.memory.InMemoryBackend.increment",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Backend down"),
            ):
                resp = await c.get("/limited")
                assert resp.status_code == 200  # fail_open default is True

    async def test_fail_closed_on_backend_error(self):
        from unittest.mock import AsyncMock, patch

        app = _make_app_decorator(rate=5, window=60, fail_open=False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # First request initializes the backend
            resp = await c.get("/limited")
            assert resp.status_code == 200
            # Now break the backend
            with patch(
                "fastapi_gradual_throttle.backends.memory.InMemoryBackend.increment",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Backend down"),
            ):
                resp = await c.get("/limited")
                assert resp.status_code == 503
