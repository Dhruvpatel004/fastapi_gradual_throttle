"""
Tests for the Depends()-based GradualThrottle dependency.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from fastapi_gradual_throttle.dependencies import GradualThrottle


def _make_app_with_dep(**dep_kwargs):
    app = FastAPI()
    throttle = GradualThrottle(**dep_kwargs)

    @app.get("/limited", dependencies=[Depends(throttle)])
    async def limited(request: Request):
        return JSONResponse({"status": "ok"})

    @app.get("/free")
    async def free():
        return JSONResponse({"status": "free"})

    return app


@pytest.mark.asyncio
class TestDependencyGradual:
    async def test_within_rate(self):
        app = _make_app_with_dep(rate=5, window=60)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(5):
                resp = await c.get("/limited")
                assert resp.status_code == 200

    async def test_free_endpoint_unaffected(self):
        app = _make_app_with_dep(rate=1, window=60)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.get("/limited")
            # Unlimited endpoint should always work
            for _ in range(10):
                resp = await c.get("/free")
                assert resp.status_code == 200


@pytest.mark.asyncio
class TestDependencyStrict:
    async def test_strict_returns_429(self):
        app = _make_app_with_dep(rate=2, window=60, mode="strict")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(2):
                resp = await c.get("/limited")
                assert resp.status_code == 200
            resp = await c.get("/limited")
            assert resp.status_code == 429


@pytest.mark.asyncio
class TestDependencyHardLimit:
    async def test_hard_limit_returns_429(self):
        app = _make_app_with_dep(rate=2, window=60, hard_limit=4)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(4):
                resp = await c.get("/limited")
                assert resp.status_code == 200
            resp = await c.get("/limited")
            assert resp.status_code == 429


@pytest.mark.asyncio
class TestDependencyDisabled:
    async def test_disabled_skips_throttle(self):
        app = _make_app_with_dep(rate=1, window=60, enabled=False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(5):
                resp = await c.get("/limited")
                assert resp.status_code == 200


@pytest.mark.asyncio
class TestDependencyDryRun:
    async def test_dry_run_no_block(self):
        """Dry run should not delay or block requests."""
        app = _make_app_with_dep(rate=1, window=60, dry_run=True)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.get("/limited")
            resp = await c.get("/limited")
            # dry_run: excess is logged but request passes
            assert resp.status_code == 200


@pytest.mark.asyncio
class TestDependencyFailOpen:
    async def test_fail_open_on_backend_error(self):
        """With fail_open=True, backend errors should let requests through."""
        app = _make_app_with_dep(rate=5, window=60)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            with patch(
                "fastapi_gradual_throttle.backends.memory.InMemoryBackend.increment",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Backend down"),
            ):
                resp = await c.get("/limited")
                assert resp.status_code == 200

    async def test_fail_closed_on_backend_error(self):
        """With fail_open=False, backend errors should raise 503."""
        app = _make_app_with_dep(rate=5, window=60, fail_open=False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            with patch(
                "fastapi_gradual_throttle.backends.memory.InMemoryBackend.increment",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Backend down"),
            ):
                resp = await c.get("/limited")
                assert resp.status_code == 503


@pytest.mark.asyncio
class TestDependencyTokenBucket:
    async def test_token_bucket_allows_burst(self):
        app = _make_app_with_dep(
            rate=10, window=60, window_type="token_bucket", burst_size=3
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(3):
                resp = await c.get("/limited")
                assert resp.status_code == 200
            # 4th exceeds burst
            resp = await c.get("/limited")
            assert resp.status_code == 429

    async def test_token_bucket_fail_open(self):
        """Token bucket path with fail_open=True on backend error."""
        app = _make_app_with_dep(
            rate=10, window=60, window_type="token_bucket", burst_size=3
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            with patch(
                "fastapi_gradual_throttle.backends.memory.InMemoryBackend.token_bucket_consume",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Backend down"),
            ):
                resp = await c.get("/limited")
                assert resp.status_code == 200


@pytest.mark.asyncio
class TestDependencySlidingWindow:
    async def test_sliding_window_works(self):
        app = _make_app_with_dep(rate=5, window=60, window_type="sliding")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(5):
                resp = await c.get("/limited")
                assert resp.status_code == 200
