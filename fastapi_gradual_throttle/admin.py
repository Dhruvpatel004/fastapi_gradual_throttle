"""
Optional admin/inspection router for throttle state.

.. warning::

    This router exposes internal throttle counters.  **Always** protect it
    with an authentication dependency (e.g. ``Depends(require_admin)``).
    Never mount it without access control in production.

Usage::

    from fastapi_gradual_throttle.admin import throttle_admin_router

    app.include_router(
        throttle_admin_router,
        prefix="/_throttle",
        dependencies=[Depends(require_admin)],
    )
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .utils import _sanitize, build_cache_key

throttle_admin_router = APIRouter(tags=["throttle-admin"])

_MAX_KEY_LENGTH = 256


@throttle_admin_router.get("/key/{key:path}")
async def inspect_throttle_key(key: str, request: Request) -> JSONResponse:
    """Return the current throttle state for *key*.

    Requires ``init_throttle`` to have been called so the backend and
    prefix are available on ``request.app.state``.

    The *key* is sanitised and length-capped to prevent cache-key
    injection or denial-of-service via oversized keys.
    """
    from .config import ThrottleConfig
    from .utils import import_backend

    # Sanitise user-supplied key to prevent injection
    key = _sanitize(key, max_len=_MAX_KEY_LENGTH)

    cfg: ThrottleConfig | None = getattr(request.app.state, "throttle_config", None)
    if cfg is None:
        return JSONResponse(
            {"error": "No global throttle config found. Call init_throttle() first."},
            status_code=500,
        )

    # Reuse the shared backend from init_throttle()
    backend = getattr(request.app.state, "throttle_backend", None)
    if backend is None:
        backend_cls = import_backend(cfg.backend)
        backend = backend_cls(**cfg.backend_options)

    full_key = build_cache_key(cfg.key_prefix, key)

    data = await backend.get(full_key)

    if data is None:
        return JSONResponse({"key": key, "found": False})

    count = data.get("count", 0)
    remaining = max(0, cfg.rate - count)

    return JSONResponse(
        {
            "key": key,
            "found": True,
            "count": count,
            "remaining": remaining,
            "window_start": data.get("window_start"),
            "previous_count": data.get("previous_count", 0),
        }
    )
