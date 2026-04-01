"""
Tests for ThrottleRouter — router-level throttle configuration.
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from fastapi_gradual_throttle.router import ThrottleRouter


@pytest.mark.asyncio
class TestThrottleRouter:
    async def test_router_applies_throttle(self):
        app = FastAPI()
        api = ThrottleRouter(prefix="/api", throttle_rate=3, throttle_window=60)

        @api.get("/data")
        async def data(request: Request):
            return JSONResponse({"data": "ok"})

        app.include_router(api)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(3):
                resp = await c.get("/api/data")
                assert resp.status_code == 200

    async def test_router_strict_mode(self):
        app = FastAPI()
        api = ThrottleRouter(
            prefix="/api", throttle_rate=2, throttle_window=60, throttle_mode="strict"
        )

        @api.get("/data")
        async def data(request: Request):
            return JSONResponse({"data": "ok"})

        app.include_router(api)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(2):
                await c.get("/api/data")
            resp = await c.get("/api/data")
            assert resp.status_code == 429

    async def test_non_router_endpoints_unaffected(self):
        app = FastAPI()
        api = ThrottleRouter(prefix="/api", throttle_rate=1, throttle_window=60)

        @api.get("/data")
        async def data(request: Request):
            return JSONResponse({"data": "ok"})

        @app.get("/free")
        async def free():
            return JSONResponse({"status": "free"})

        app.include_router(api)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # Use up the router's limit
            await c.get("/api/data")
            # Free endpoint is outside the router
            for _ in range(10):
                resp = await c.get("/free")
                assert resp.status_code == 200

    async def test_combined_mode(self):
        app = FastAPI()
        api = ThrottleRouter(
            prefix="/api",
            throttle_rate=2,
            throttle_window=60,
            throttle_mode="combined",
            throttle_hard_limit=4,
        )

        @api.get("/data")
        async def data(request: Request):
            return JSONResponse({"data": "ok"})

        app.include_router(api)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(4):
                resp = await c.get("/api/data")
                assert resp.status_code == 200
            resp = await c.get("/api/data")
            assert resp.status_code == 429

    async def test_existing_dependencies_preserved(self):
        """Router-level dependencies from the user should be kept."""
        from fastapi import Depends

        call_log = []

        async def custom_dep():
            call_log.append("called")

        app = FastAPI()
        api = ThrottleRouter(
            prefix="/api",
            throttle_rate=10,
            throttle_window=60,
            dependencies=[Depends(custom_dep)],
        )

        @api.get("/data")
        async def data(request: Request):
            return JSONResponse({"data": "ok"})

        app.include_router(api)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/data")
            assert resp.status_code == 200
            assert len(call_log) == 1

    async def test_rate_kwarg_without_prefix(self):
        """ThrottleRouter accepts 'rate' without 'throttle_' prefix."""
        app = FastAPI()
        api = ThrottleRouter(prefix="/api", rate=2, window=60, mode="strict")

        @api.get("/data")
        async def data(request: Request):
            return JSONResponse({"data": "ok"})

        app.include_router(api)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(2):
                await c.get("/api/data")
            resp = await c.get("/api/data")
            assert resp.status_code == 429
