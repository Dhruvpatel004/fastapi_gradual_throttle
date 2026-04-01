"""
Tests for ThrottleException and default_429_response_body.
"""

import json

from fastapi_gradual_throttle.exceptions import (
    ThrottleException,
    default_429_response_body,
)


class TestThrottleException:
    def test_default_attributes(self):
        exc = ThrottleException()
        assert exc.detail == "Too Many Requests"
        assert exc.retry_after is None
        assert exc.status_code == 429
        assert str(exc) == "Too Many Requests"

    def test_custom_attributes(self):
        exc = ThrottleException(
            detail="Rate limit hit", retry_after=30, status_code=503
        )
        assert exc.detail == "Rate limit hit"
        assert exc.retry_after == 30
        assert exc.status_code == 503

    def test_is_exception(self):
        exc = ThrottleException()
        assert isinstance(exc, Exception)


class TestDefault429ResponseBody:
    def test_no_retry_after(self):
        body = default_429_response_body(retry_after=0)
        data = json.loads(body)
        assert data["detail"] == "Too Many Requests"
        assert "retry_after" not in data

    def test_with_retry_after(self):
        body = default_429_response_body(retry_after=30)
        data = json.loads(body)
        assert data["detail"] == "Too Many Requests"
        assert data["retry_after"] == 30

    def test_returns_bytes(self):
        body = default_429_response_body()
        assert isinstance(body, bytes)
