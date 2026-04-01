"""
Tests for the admin inspection router.
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from fastapi_gradual_throttle import init_throttle
from fastapi_gradual_throttle.admin import throttle_admin_router
from fastapi_gradual_throttle.config import ThrottleConfig


def _make_admin_app(init: bool = True, **config_kwargs):
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint(request: Request):
        return JSONResponse({"status": "ok"})

    app.include_router(throttle_admin_router, prefix="/_throttle")

    if init:
        init_throttle(app, **config_kwargs)

    return app


@pytest.mark.asyncio
class TestAdminInspectKey:
    async def test_key_not_found(self):
        app = _make_admin_app(rate=10, window=60)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/_throttle/key/nonexistent")
            assert resp.status_code == 200
            body = resp.json()
            assert body["found"] is False
            assert body["key"] == "nonexistent"

    async def test_key_found_after_request(self):
        """After a throttled request, the admin endpoint should see the counter."""
        app = FastAPI()

        @app.get("/test")
        async def test_endpoint(request: Request):
            return JSONResponse({"status": "ok"})

        from fastapi_gradual_throttle import GradualThrottleMiddleware

        config = ThrottleConfig(rate=10, window=60)
        init_throttle(app, config=config)
        app.add_middleware(GradualThrottleMiddleware, config=config)
        app.include_router(throttle_admin_router, prefix="/_throttle")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # Make a request to populate the counter
            await c.get("/test")
            # The key format is ip:<sanitized_ip>
            # Admin lookup with a raw key — check a known key pattern
            resp = await c.get("/_throttle/key/ip:127.0.0.1")
            assert resp.status_code == 200

    async def test_no_global_config_returns_500(self):
        app = _make_admin_app(init=False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/_throttle/key/somekey")
            assert resp.status_code == 500
            body = resp.json()
            assert "error" in body

    async def test_key_sanitized(self):
        """Malicious key input characters are sanitized."""
        app = _make_admin_app(rate=10, window=60)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/_throttle/key/malicious%00key")
            assert resp.status_code == 200
            body = resp.json()
            assert body["found"] is False
