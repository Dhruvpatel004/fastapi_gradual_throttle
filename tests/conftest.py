"""
Shared fixtures for fastapi-gradual-throttle tests.
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from fastapi_gradual_throttle import GradualThrottleMiddleware, init_throttle
from fastapi_gradual_throttle.backends.memory import InMemoryBackend
from fastapi_gradual_throttle.config import ThrottleConfig


@pytest.fixture
def app():
    """Create a minimal FastAPI app with a test endpoint."""
    _app = FastAPI()

    @_app.get("/test")
    async def test_endpoint(request: Request):
        return JSONResponse({"status": "ok"})

    @_app.get("/health")
    async def health():
        return JSONResponse({"status": "healthy"})

    return _app


@pytest.fixture
def throttled_app(app):
    """App with default gradual throttle middleware."""
    config = ThrottleConfig(rate=5, window=60)
    app.add_middleware(GradualThrottleMiddleware, config=config)
    return app


@pytest.fixture
def strict_app(app):
    """App with strict rate-limit mode."""
    config = ThrottleConfig(rate=5, window=60, mode="strict")
    app.add_middleware(GradualThrottleMiddleware, config=config)
    return app


@pytest.fixture
def combined_app(app):
    """App with combined mode (gradual + hard limit)."""
    config = ThrottleConfig(rate=5, window=60, mode="combined", hard_limit=10)
    app.add_middleware(GradualThrottleMiddleware, config=config)
    return app


@pytest.fixture
async def client(throttled_app):
    transport = ASGITransport(app=throttled_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def strict_client(strict_app):
    transport = ASGITransport(app=strict_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def combined_client(combined_app):
    transport = ASGITransport(app=combined_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def backend():
    """Fresh in-memory backend."""
    return InMemoryBackend()
