"""
Exception classes and default response factories for fastapi-gradual-throttle.
"""

import json


class ThrottleException(Exception):
    """Raised when a request exceeds the throttle limit.

    Attributes:
        detail: Human-readable error message.
        retry_after: Seconds the client should wait before retrying, or ``None``.
        status_code: HTTP status code (default ``429``).
    """

    def __init__(
        self,
        detail: str = "Too Many Requests",
        retry_after: int | None = None,
        status_code: int = 429,
    ):
        self.detail = detail
        self.retry_after = retry_after
        self.status_code = status_code
        super().__init__(detail)


def default_429_response_body(retry_after: int = 0) -> bytes:
    """
    Build the default JSON body for a 429 response.

    Returns:
        bytes: UTF-8 encoded JSON body.
    """
    body = {"detail": "Too Many Requests"}
    if retry_after > 0:
        body["retry_after"] = retry_after
    return json.dumps(body).encode("utf-8")


def default_503_response_body() -> bytes:
    """
    Build the default JSON body for a 503 response (backend failure).

    Returns:
        bytes: UTF-8 encoded JSON body.
    """
    return json.dumps({"detail": "Service Unavailable"}).encode("utf-8")
