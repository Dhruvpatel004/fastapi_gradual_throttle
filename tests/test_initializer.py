"""
Tests for init_throttle — global initialisation helper.
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from fastapi_gradual_throttle import init_throttle
from fastapi_gradual_throttle.backends.memory import InMemoryBackend
from fastapi_gradual_throttle.config import ThrottleConfig


class TestInitThrottle:
    def test_with_config_object(self):
        app = FastAPI()
        config = ThrottleConfig(rate=100, window=30)
        result = init_throttle(app, config=config)
        assert result is config
        assert app.state.throttle_config is config
        assert app.state.throttle_config.rate == 100

    def test_with_kwargs(self):
        app = FastAPI()
        result = init_throttle(app, rate=50, window=120, mode="strict")
        assert result.rate == 50
        assert result.window == 120
        assert result.mode == "strict"
        assert app.state.throttle_config is result

    def test_with_defaults(self):
        app = FastAPI()
        result = init_throttle(app)
        assert isinstance(result, ThrottleConfig)
        assert app.state.throttle_config is result

    def test_backend_stored_on_app_state(self):
        app = FastAPI()
        init_throttle(app, rate=10, window=60)
        assert hasattr(app.state, "throttle_backend")
        assert isinstance(app.state.throttle_backend, InMemoryBackend)

    def test_returns_config(self):
        app = FastAPI()
        result = init_throttle(app, rate=10, window=60)
        assert isinstance(result, ThrottleConfig)
        assert result.rate == 10
