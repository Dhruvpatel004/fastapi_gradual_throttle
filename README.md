# FastAPI Gradual Throttle

![Python](https://img.shields.io/badge/python-3.10%20|%203.11%20|%203.12%20|%203.13-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.135.3-009688?logo=fastapi&logoColor=white)
![Version](https://img.shields.io/badge/version-1.0.0-informational)
![License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-275%20passed-brightgreen?logo=pytest&logoColor=white)
![Coverage](https://img.shields.io/badge/coverage-95%25-brightgreen?logo=codecov&logoColor=white)
![Status](https://img.shields.io/badge/status-production--stable-brightgreen)
![Rate Limiting](https://img.shields.io/badge/rate%20limiting-gradual%20%7C%20strict%20%7C%20combined-orange)
![Throttling](https://img.shields.io/badge/throttling-linear%20%7C%20exponential%20%7C%20token%20bucket-blueviolet)
![Backends](https://img.shields.io/badge/backends-in--memory%20%7C%20redis-yellow)
![Typed](https://img.shields.io/badge/typing-PEP%20561%20py.typed-informational?logo=python&logoColor=white)
![Code Style](https://img.shields.io/badge/code%20style-black-black)
![Linter](https://img.shields.io/badge/linter-ruff-red)

A FastAPI/Starlette middleware library that provides gradual request throttling with configurable delay strategies. Unlike traditional rate limiting that immediately blocks requests, this library applies progressive delays to slow down excessive requests gracefully — or can operate as a strict rate limiter when you need hard 429 enforcement.

## Features

- **Three Throttle Modes**: Gradual delay, strict rate-limit (429), or combined (delay + hard cap)
- **Configurable Delay Strategies**: Linear, exponential, or custom delay algorithms
- **Flexible Key Functions**: Throttle by IP address, user ID, or custom keys
- **Pluggable Storage Backends**: In-memory (dev) and Redis (production) with atomic operations
- **Global + Router + Per-Route Config**: Set defaults globally, override at the router level, and fine-tune per endpoint
- **Security-First IP Extraction**: Trusted-proxy validation prevents X-Forwarded-For spoofing
- **Fail-Open by Default**: Backend failures never crash your application traffic
- **Comprehensive Configuration**: Pydantic Settings with env-var and programmatic support
- **Monitoring & Debugging**: Built-in hooks (sync + async) and dry-run mode
- **Headers Support**: Optional response headers for client awareness
- **Path & Custom Exemptions**: Skip throttling for health checks, admin paths, or any custom logic
- **Sliding Window Option**: Weighted sliding-window algorithm to smooth burst spikes
- **Token Bucket Mode**: Allow controlled bursts above sustained rate
- **Pure ASGI Middleware**: No BaseHTTPMiddleware limitations — supports streaming responses
- **JSON 429 Responses**: Customizable response body for rate-limited requests
- **Admin Inspection Endpoint**: Optional router to inspect live throttle counters via HTTP
- **PEP 561 Type Checking**: Ships with `py.typed` marker for full mypy / pyright support

## Installation

```bash
pip install fastapi-gradual-throttle
```

For Redis backend support:

```bash
pip install fastapi-gradual-throttle[redis]
```

## Quick Start

Instantiate a `ThrottleConfig` object once and pass it to both `init_throttle`
and `add_middleware`. This keeps all settings in one place, shares the backend
connection across the middleware and per-route throttles, and lets env vars
(`FASTAPI_GRADUAL_THROTTLE_*`) override any field without touching Python code.

### Option 1: Direct Initialization (Simple)

```python
from fastapi import FastAPI
from fastapi_gradual_throttle import GradualThrottleMiddleware, ThrottleConfig, init_throttle

app = FastAPI()

config = ThrottleConfig(
    rate=120,           # default is 60  — override it
    mode="combined",    # default is "gradual"
    hard_limit=200,     # default is 0 (disabled)
    key_prefix="myapp", # must be unique per app when sharing Redis
    backend="fastapi_gradual_throttle.backends.redis.RedisBackend",
    backend_options={"url": "redis://localhost:6379/1"},
    # all other fields use their built-in defaults
)

init_throttle(app, config=config)                       # shared backend + per-app path cache
app.add_middleware(GradualThrottleMiddleware, config=config)
```

### Option 2: FastAPI Lifespan Context Manager (Recommended)

This modern approach is cleaner and supports async cleanup for Redis connections:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi_gradual_throttle import GradualThrottleMiddleware, ThrottleConfig, init_throttle

config = ThrottleConfig(
    rate=120,
    mode="combined",
    hard_limit=200,
    key_prefix="myapp",
    backend="fastapi_gradual_throttle.backends.redis.RedisBackend",
    backend_options={"url": "redis://localhost:6379/1"},
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize throttle
    init_throttle(app, config=config)
    yield
    # Shutdown: cleanup if needed (backends handle cleanup naturally)

app = FastAPI(lifespan=lifespan)
app.add_middleware(GradualThrottleMiddleware, config=config)
```

> **Warning**: If you are using Redis as the backend, make sure to set a unique `key_prefix` if multiple apps share the same Redis instance. The default prefix `"throttle"` will cause counter collisions between co-hosted apps.

## How It Works

### Execution Model

The library provides three layers of throttle control. Each layer can be used independently or together:

- **Layer 1: `GradualThrottleMiddleware`** — Runs on every HTTP request *before* routing. This is the fastest path and is ideal for global defaults. Configured via `app.add_middleware(...)`.

- **Layer 2: `ThrottleRouter`** — Applies to all routes registered on a specific router. Implemented as a FastAPI dependency injected into every route on that router. Runs *after* routing, as part of the dependency injection phase.

- **Layer 3: `@throttle()` / `Depends(GradualThrottle())`** — Per-route throttle override. Runs *after* routing, inside the handler's dependency chain or decorator wrapper.

**Auto-Exempt**: When you use Layer 1 (global middleware) together with Layer 2 or Layer 3 on the same route, the middleware **automatically detects** the per-route throttle and skips its own counting for that route. This means:

- The global middleware **does not** increment its counter for that route.
- The global middleware **does not** apply its delay or 429 logic.
- **Only** the per-route throttle (Layer 2 or 3) applies — with its own rate, window, and mode.
- No manual `@throttle_exempt()` is needed — the bypass is automatic.

`@throttle_exempt()` is a **separate** concept — it tells the middleware to skip a route that has **no throttle at all** (e.g. `/health`, `/metrics`). See the [`@throttle_exempt()` section](#throttle_exempt--opt-out-of-global-middleware-entirely) for details.

### Request Lifecycle

Here is the full lifecycle of a throttled HTTP request when using the global middleware:

1. **Request arrives** — the ASGI server hands the request to the middleware.
2. **WebSocket check** — if the request is a WebSocket upgrade and `websocket_exempt=True` (default), pass through immediately.
3. **Enabled check** — if `enabled=False`, pass through immediately.
4. **Path exemption** — if the request path matches any prefix in `exempt_paths`, pass through.
5. **Route exemption** — if the path is cached as exempt on `app.state` (via `@throttle_exempt()`), pass through.
6. **Per-route throttle auto-exempt** — if the route has its own per-route throttle (via `@throttle()`, `Depends(GradualThrottle())`, or `ThrottleRouter`), pass through to avoid double-counting.
7. **Custom exemption** — if `exempt_func` is configured and returns `True`, pass through.
8. **Key resolution** — the `key_func` extracts a throttle key from the request (e.g. `"ip:1.2.3.4"` or `"user:42"`).
9. **Atomic counter increment** — the backend atomically increments the counter for this key and returns the current count and window state.
10. **Effective count** — for `window_type="sliding"`, a weighted count is calculated using the previous window's count. For `window_type="fixed"`, the raw count is used. For `window_type="token_bucket"`, a separate token-consumption path is used.
11. **Rate resolution** — if `limit_func` is configured, it is called to get the effective rate for this specific request (e.g. pro users get 1000, free users get 60). Otherwise the global `rate` is used.
12. **Excess calculation** — `excess = max(0, effective_count - effective_rate)`.
13. **Mode-specific action**:
    - **Gradual**: if excess > 0, calculate delay via the delay strategy, then `asyncio.sleep(delay)`. If `dry_run=True`, skip the sleep but still log and add headers.
    - **Strict**: if excess > 0, return HTTP 429 immediately with `Retry-After` header.
    - **Combined**: if excess > 0 but count <= `hard_limit`, apply gradual delay. If count > `hard_limit`, return HTTP 429.
14. **Forward** — the request is forwarded to the route handler.
15. **Headers** — if `headers_enabled=True`, throttle headers (`X-Throttle-Remaining`, `X-Throttle-Limit`, etc.) are injected into the response.

### Flow Diagram

```
                        ┌─────────────────┐
                        │  Incoming ASGI   │
                        │    Request       │
                        └────────┬────────┘
                                 │
                        ┌────────▼────────┐   Yes
                        │  WebSocket +    ├────────► Pass through
                        │  ws_exempt?     │
                        └────────┬────────┘
                                 │ No
                        ┌────────▼────────┐   No
                        │    enabled?     ├────────► Pass through
                        └────────┬────────┘
                                 │ Yes
                        ┌────────▼────────┐   Yes
                        │  exempt_paths   ├────────► Pass through
                        │  match?         │
                        └────────┬────────┘
                                 │ No
                        ┌────────▼────────┐   Yes
                        │  @throttle_     ├────────► Pass through
                        │  exempt()?      │
                        │ (app.state cache)│
                        └────────┬────────┘
                                 │ No
                        ┌────────▼────────┐   Yes
                        │  exempt_func    ├────────► Pass through
                        │  returns True?  │
                        └────────┬────────┘
                                 │ No
                        ┌────────▼────────┐
                        │  key_func(req)  │
                        │  → throttle key │
                        └────────┬────────┘
                                 │
                        ┌────────▼────────┐
                        │  Backend:       │
                        │  increment key  │
                        │  get count      │
                        └────────┬────────┘
                                 │
                        ┌────────▼────────┐
                        │  limit_func?    │
                        │  → eff. rate    │
                        └────────┬────────┘
                                 │
                 excess = count - rate
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
     ┌────────▼───────┐  ┌──────▼──────┐  ┌───────▼───────┐
     │    GRADUAL     │  │   STRICT    │  │   COMBINED    │
     └────────┬───────┘  └──────┬──────┘  └───────┬───────┘
              │                  │                  │
     excess > 0?          excess > 0?       excess > 0?
     Yes: sleep(delay)    Yes: → 429        Yes + ≤ hard_limit:
     (skip if dry_run)    No:  forward        sleep(delay)
     Then: forward                          Yes + > hard_limit:
                                              → 429
                                            No: forward
              │                  │                  │
              └──────────────────┼──────────────────┘
                                 │
                        ┌────────▼────────┐
                        │  Forward to     │
                        │  route handler  │
                        └────────┬────────┘
                                 │
                        ┌────────▼────────┐
                        │  Inject headers │
                        │  (if enabled)   │
                        └────────┬────────┘
                                 │
                        ┌────────▼────────┐
                        │    Response     │
                        └─────────────────┘
```

### Counter Storage

Each unique throttle key (one per client IP or user ID by default) gets its own counter in the storage backend. The counter increments on every request and resets after the window expires. With `window_type="sliding"`, the previous window's count is used to compute a weighted average that smooths rate enforcement at window boundaries. With `window_type="token_bucket"`, tokens refill at a steady rate and are consumed per-request.

### Backend Atomicity

**InMemoryBackend** uses `asyncio.Lock` to ensure the increment-and-read operation is atomic within a single process. **RedisBackend** uses Lua scripts executed atomically on the Redis server, ensuring no two requests can read the same counter value simultaneously — even across multiple workers or instances.

## Throttle Modes

### Gradual Mode (Default)

**What it does**: Requests above the rate limit are slowed down progressively. No request is ever blocked — they just take longer to complete.

**Threshold behavior**:
- `count <= rate` — instant response, no delay
- `count > rate` — delay is applied:
  - Linear strategy (default): `delay = base_delay * excess_requests`
  - Exponential strategy: `delay = base_delay * 2^(excess_requests - 1)`
- Delay is capped at `max_delay` regardless of how many excess requests have been made

**When to use**: APIs where you want to discourage abuse without breaking legitimate clients. Good for search endpoints, public APIs, and any scenario where occasional bursts are acceptable.

**Client experience**: Responses get progressively slower as the client exceeds the rate. Eventually every response takes `max_delay` seconds. No errors are returned.

```python
from fastapi import FastAPI
from fastapi_gradual_throttle import GradualThrottleMiddleware, ThrottleConfig, init_throttle

app = FastAPI()

config = ThrottleConfig(
    mode="gradual",        # default
    rate=100,              # 100 requests per window
    window=60,             # 60-second window
    base_delay=0.2,        # 0.2s delay per excess request
    max_delay=5.0,         # never delay more than 5 seconds
)
init_throttle(app, config=config)
app.add_middleware(GradualThrottleMiddleware, config=config)
```

### Strict Mode

**What it does**: Classic rate limiter. Once the rate is exceeded, all further requests in that window get an immediate HTTP 429 response.

**Threshold behavior**:
- `count <= rate` — instant response
- `count > rate` — HTTP 429 with `Retry-After` header

**When to use**: Login endpoints, payment flows, anything where you want a hard guarantee that no more than N requests are processed per window.

**Note**: `hard_limit` has no effect in strict mode and will raise a validation error at startup if set. Use combined mode instead.

**Client experience**: Clean 429 response with `Retry-After` header. No delays applied.

```python
config = ThrottleConfig(
    mode="strict",
    rate=5,                # 5 login attempts per window
    window=300,            # 5-minute window
)
init_throttle(app, config=config)
app.add_middleware(GradualThrottleMiddleware, config=config)
```

### Combined Mode

**What it does**: Two thresholds. Between `rate` and `hard_limit`, requests are delayed (like gradual). Above `hard_limit`, requests are blocked with 429.

**Threshold behavior**:
- `count <= rate` — instant response
- `rate < count <= hard_limit` — progressive delay (gradual behavior)
- `count > hard_limit` — HTTP 429 with `Retry-After` header

**When to use**: APIs where you want to slow down heavy users first, then cut them off entirely if they keep going. `hard_limit` **must** be set and **must** be >= `rate` when using combined mode.

**Client experience**: Gradually slowing responses, then 429 after `hard_limit` is exceeded.

```python
config = ThrottleConfig(
    mode="combined",
    rate=100,              # delays start after 100 req/min
    window=60,
    hard_limit=200,        # hard block at 200 req/min
    base_delay=0.2,
    max_delay=5.0,
)
init_throttle(app, config=config)
app.add_middleware(GradualThrottleMiddleware, config=config)
```

## Configuration Reference

### `ThrottleConfig` — Pydantic `BaseSettings` class

`ThrottleConfig` is the single source of truth for all throttle settings. It
inherits from `pydantic_settings.BaseSettings`, which means it resolves values
in this priority order (highest → lowest):

```
ThrottleConfig() constructor kwargs         ← explicit Python value, always wins
  └── Environment variables                 ← FASTAPI_GRADUAL_THROTTLE_<FIELD>
        └── Pydantic field defaults         ← built-in fallback, every field has one
```

**Key behaviours:**
- Every field has a sensible built-in default. You only need to pass the fields you want to override.
- Environment variables use the prefix `FASTAPI_GRADUAL_THROTTLE_` followed by the uppercase field name (e.g. `FASTAPI_GRADUAL_THROTTLE_RATE=200`).
- Nested dict fields (like `backend_options`) use `__` as the delimiter: `FASTAPI_GRADUAL_THROTTLE_BACKEND_OPTIONS__URL=redis://...`.
- Constructor kwargs always take precedence over env vars — passing `rate=60` in Python ignores any `FASTAPI_GRADUAL_THROTTLE_RATE` env var for that field.

**Passing to the middleware:**

```python
config = ThrottleConfig(rate=100, mode="strict")

# pass via init_throttle (recommended — creates shared backend)
init_throttle(app, config=config)
app.add_middleware(GradualThrottleMiddleware, config=config)

# or pass directly without init_throttle
app.add_middleware(GradualThrottleMiddleware, config=config)
```

All settings are optional and have sensible defaults. Configure via a `ThrottleConfig` object or environment variables with the `FASTAPI_GRADUAL_THROTTLE_` prefix.

---

### `enabled` (bool, default: `True`)

**env**: `FASTAPI_GRADUAL_THROTTLE_ENABLED`

Master switch for the throttle. When `False`, the middleware, decorators, and dependencies all pass requests through without any processing. Useful for turning off throttling in specific environments via env var.

---

### `mode` (str, default: `"gradual"`)

**env**: `FASTAPI_GRADUAL_THROTTLE_MODE`

Controls throttle behavior. One of: `"gradual"`, `"strict"`, `"combined"`. See the [Throttle Modes](#throttle-modes) section for full explanation of each.

---

### `rate` (int, default: `60`)

**env**: `FASTAPI_GRADUAL_THROTTLE_RATE`

The maximum number of requests allowed per time window before throttling begins. In gradual mode this is the point at which delays start. In strict mode this is the hard cutoff. Must be > 0.

**Gotcha**: `rate` is per unique key (per IP by default), not global across all clients. 100 different IPs can each make 60 requests — that's 6,000 total requests per window.

---

### `window` (int, default: `60`)

**env**: `FASTAPI_GRADUAL_THROTTLE_WINDOW`

The duration of the time window in seconds. Counters reset after this many seconds from the first request in the window. Use `window_type="sliding"` to smooth resets at window boundaries. Must be > 0.

---

### `base_delay` (float, default: `0.2`)

**env**: `FASTAPI_GRADUAL_THROTTLE_BASE_DELAY`

The delay in seconds applied per excess request in gradual/combined mode. With linear strategy: `total_delay = base_delay * excess_requests`. With exponential: `delay = base_delay * 2^(excess_requests - 1)`. Must be >= 0.

---

### `max_delay` (float, default: `5.0`)

**env**: `FASTAPI_GRADUAL_THROTTLE_MAX_DELAY`

The maximum delay in seconds regardless of how many excess requests have been made. Acts as a ceiling — no request will ever be delayed longer than this. Must be >= `base_delay`.

---

### `hard_limit` (int, default: `0` = disabled)

**env**: `FASTAPI_GRADUAL_THROTTLE_HARD_LIMIT`

The absolute request ceiling above which 429 is returned immediately. Only meaningful in combined mode (and optionally gradual mode as a safety ceiling). Must be 0 (disabled) or >= `rate`. Setting `hard_limit` with `mode="strict"` raises a validation error at startup — use `mode="combined"` instead.

---

### `window_type` (str, default: `"fixed"`)

**env**: `FASTAPI_GRADUAL_THROTTLE_WINDOW_TYPE`

Algorithm for counting requests. Options:

- `"fixed"` — Counter resets at the end of each window period. Simple and cheap. Can allow a burst of up to 2× rate at window boundaries (e.g. 60 requests at second 59, then 60 more at second 61).
- `"sliding"` — Weighted count using previous window to smooth bursts. Slightly more memory but prevents the double-burst at window boundaries. Formula: `effective = current_count + previous_count * (1 - elapsed/window)`.
- `"token_bucket"` — Allows controlled bursts above the sustained rate. Requires `burst_size > 0`. Tokens refill at `rate/window` per second up to `burst_size`. Best for bursty-but-fair traffic patterns.

---

### `burst_size` (int, default: `0`)

**env**: `FASTAPI_GRADUAL_THROTTLE_BURST_SIZE`

Number of requests available instantly in token bucket mode. After the burst is consumed, requests are allowed at `rate/window` per second. Only used when `window_type="token_bucket"`. Must be > 0 when `window_type="token_bucket"`.

**Gotcha**: Setting `burst_size > 0` when `window_type` is not `"token_bucket"` has no effect. A startup warning is emitted to alert you of this misconfiguration.

---

### `key_func` (str, default: built-in IP/user function)

**env**: `FASTAPI_GRADUAL_THROTTLE_KEY_FUNC`

Dotted path to a `callable(request: Request) -> str` that returns the throttle key for a request. The returned string determines which counter bucket this request counts against. Default uses `request.state.user.id` if present, otherwise the client IP address.

**Gotcha**: Make sure keys are reasonably unique. Returning a constant string means ALL clients share one counter.

```python
# myapp/utils.py
from starlette.requests import Request
from fastapi_gradual_throttle.utils import get_client_ip

def custom_key_func(request: Request) -> str:
    """Key by API key header or IP."""
    api_key = request.headers.get("x-api-key")
    if api_key:
        return f"apikey:{api_key}"
    ip = get_client_ip(
        request,
        trusted_proxies=getattr(request.state, "_throttle_trusted_proxies", None),
    )
    return f"ip:{ip}"
```

---

### `limit_func` (str, default: `None`)

**env**: `FASTAPI_GRADUAL_THROTTLE_LIMIT_FUNC`

Dotted path to a `callable(request: Request) -> int` that returns the effective rate limit for this specific request. Overrides the global `rate` for that request only. Use for per-tier limits (e.g. free vs pro users). Falls back to config `rate` if `limit_func` raises or returns an invalid value. Supports sync and async callables.

```python
# myapp/limits.py
def my_limit_func(request):
    user = getattr(request.state, "user", None)
    if getattr(user, "is_pro", False):
        return 1000
    return 60
```

---

### `delay_strategy` (str, default: `"fastapi_gradual_throttle.strategies.linear.LinearDelayStrategy"`)

**env**: `FASTAPI_GRADUAL_THROTTLE_DELAY_STRATEGY`

Dotted path to the delay strategy class. Built-in options:

- `"fastapi_gradual_throttle.strategies.linear.LinearDelayStrategy"` — `delay = base_delay * excess_requests`
- `"fastapi_gradual_throttle.strategies.exponential.ExponentialDelayStrategy"` — `delay = base_delay * 2^(excess_requests - 1)` (default multiplier 2.0)

Custom strategies must extend `BaseDelayStrategy` and implement `calculate_delay(self, excess_requests: int) -> float`.

---

### `exempt_paths` (list[str], default: `[]`)

**env**: `FASTAPI_GRADUAL_THROTTLE_EXEMPT_PATHS`

List of URL path prefixes that skip throttling entirely. Checked as a prefix match — `"/health/"` exempts `"/health/"`, `"/health/live"`, etc. Use for health checks, metrics endpoints, and documentation routes.

```python
exempt_paths = ["/health/", "/metrics/", "/docs", "/openapi.json"]
```

---

### `exempt_func` (str, default: `None`)

**env**: `FASTAPI_GRADUAL_THROTTLE_EXEMPT_FUNC`

Dotted path to a `callable(request: Request) -> bool`. If it returns `True`, the request skips throttling. Supports sync and async callables. If it raises an exception, the request is **NOT** exempted and a warning is logged. This fail-safe prevents accidental bypass due to DB timeouts etc.

```python
# myapp/auth.py
def is_premium_user(request):
    """Exempt premium users from throttling."""
    user = getattr(request.state, "user", None)
    return user and getattr(user, "is_premium", False)
```

---

### `trusted_proxies` (list[str], default: `[]`)

**env**: `FASTAPI_GRADUAL_THROTTLE_TRUSTED_PROXIES`

List of trusted proxy IP addresses or CIDR blocks. When a request arrives from a trusted proxy IP, the `X-Forwarded-For` / `X-Real-IP` headers are used to extract the real client IP. Without this, those headers are **ignored** (secure by default — prevents IP spoofing attacks). All entries are validated as valid IPs or CIDRs at startup.

```python
trusted_proxies = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
```

---

### `fail_open` (bool, default: `True`)

**env**: `FASTAPI_GRADUAL_THROTTLE_FAIL_OPEN`

When `True`: if the storage backend (Redis etc.) fails, requests pass through without throttling. Application stays up. When `False`: backend failure returns HTTP 503 to the client.

**Recommendation**: `True` for public APIs (availability over strictness), `False` for security-critical flows (strictness over availability).

---

### `websocket_exempt` (bool, default: `True`)

**env**: `FASTAPI_GRADUAL_THROTTLE_WEBSOCKET_EXEMPT`

When `True` (default), WebSocket upgrade requests bypass throttle logic entirely. When `False`, WebSocket upgrades are subject to the same throttle rules as HTTP requests — in strict/combined mode, an upgrade can be rejected with HTTP 429 before the connection is established; in gradual mode the upgrade handshake is delayed.

---

### `dry_run` (bool, default: `False`)

**env**: `FASTAPI_GRADUAL_THROTTLE_DRY_RUN`

When `True`: delays are calculated and logged but `asyncio.sleep()` is **NOT** called. Hard limit 429 responses **ARE** still enforced. Hook functions are still called (with `dry_run=True` in kwargs). Headers are still added. Counters are still incremented in the backend. Use during development or staging to observe throttle behavior without actually slowing requests.

---

### `key_prefix` (str, default: `"throttle"`)

**env**: `FASTAPI_GRADUAL_THROTTLE_KEY_PREFIX`

Prefix added to all cache keys in the storage backend. Use a unique value per app when multiple apps share the same Redis instance. The default `"throttle"` emits a startup warning when using the Redis backend to remind you to set a unique prefix.

---

### `headers_enabled` (bool, default: `True`)

**env**: `FASTAPI_GRADUAL_THROTTLE_HEADERS_ENABLED`

When `True`, throttle information is added to response headers:

| Header | Description | When Added |
|---|---|---|
| `X-Throttle-Remaining` | Requests left before throttling starts | Always |
| `X-Throttle-Limit` | The effective rate limit for this request | Always |
| `X-Throttle-Window` | Window size in seconds | Always |
| `X-Throttle-Delay` | Delay applied in seconds | When delay > 0 |
| `X-Throttle-Excess` | Excess requests above rate | When excess > 0 |
| `Retry-After` | Seconds until window resets | On 429 and when excess > 0 |

---

### `backend` (str, default: `"fastapi_gradual_throttle.backends.memory.InMemoryBackend"`)

**env**: `FASTAPI_GRADUAL_THROTTLE_BACKEND`

Dotted path to the storage backend class. Built-in options:

- `"fastapi_gradual_throttle.backends.memory.InMemoryBackend"` — single process only, data lost on restart, no external dependencies. Suitable for development and single-worker deployments.
- `"fastapi_gradual_throttle.backends.redis.RedisBackend"` — shared across all workers, persistent, requires Redis. Required for production multi-worker deployments.

Custom backends must extend `BaseBackend`.

---

### `backend_options` (dict, default: `{}`)

**env**: `FASTAPI_GRADUAL_THROTTLE_BACKEND_OPTIONS`

Keyword arguments passed to the backend constructor. For RedisBackend: `{"url": "redis://localhost:6379/1"}`. For InMemoryBackend: `{"max_entries": 10000}`.

---

### `hook` (str, default: `None`)

**env**: `FASTAPI_GRADUAL_THROTTLE_HOOK`

Dotted path to a monitoring hook function called on throttle events. Signature: `hook(request, action, **kwargs)`. Actions: `"throttled"`, `"rate_limited"`, `"hard_limit_exceeded"`. Supports sync and async callables. See the [Monitoring & Hooks](#monitoring--hooks) section.

---

### `response_factory` (str, default: `None`)

**env**: `FASTAPI_GRADUAL_THROTTLE_RESPONSE_FACTORY`

Dotted path to a `callable(retry_after: int) -> bytes | str` that returns the body of 429 responses. Use to customise the JSON error format. Falls back to default JSON body if factory raises.

---

### Configuration Validation

The library validates configuration at startup and rejects invalid values:

- `rate` must be > 0
- `window` must be > 0
- `base_delay` must be >= 0
- `max_delay` must be >= `base_delay`
- `hard_limit` must be 0 (disabled) or >= `rate`
- `key_prefix` must not be empty
- `hard_limit` with `mode="strict"` raises a validation error (use `mode="combined"` instead)
- `burst_size` must be > 0 when `window_type="token_bucket"`
- `burst_size > 0` with `window_type` other than `"token_bucket"` has no effect — a startup warning is emitted
- `trusted_proxies` entries must be valid IP addresses or CIDR blocks

## Configuration Hierarchy

The library supports a three-level configuration hierarchy where each level inherits from and can override its parent:

```
Global Config (init_throttle)
  └── Router Config (ThrottleRouter overrides)
       └── Per-Route Config (@throttle / Depends overrides)
```

### 1. Global Configuration

Set once at startup using `init_throttle`. The recommended pattern is to create
a `ThrottleConfig` object and pass it to both `init_throttle` and
`add_middleware`:

```python
from fastapi import FastAPI
from fastapi_gradual_throttle import ThrottleConfig, init_throttle, GradualThrottleMiddleware

app = FastAPI()

# Build one config object — used by middleware, @throttle(), and Depends()
config = ThrottleConfig(
    rate=100,
    window=60,
    backend="fastapi_gradual_throttle.backends.redis.RedisBackend",
    backend_options={"url": "redis://localhost:6379/1"},
    trusted_proxies=["10.0.0.0/8"],
    key_prefix="myapp",
)

init_throttle(app, config=config)          # stores config + shared backend on app.state
app.add_middleware(GradualThrottleMiddleware, config=config)
```

`init_throttle()` stores the config on `app.state.throttle_config` and a shared backend instance on `app.state.throttle_backend`. It also eagerly scans `app.routes` and caches exempt paths and per-route-throttled paths on `app.state`. When `@throttle()` decorators and `Depends(GradualThrottle())` initialise lazily, they reuse this shared backend instead of creating their own connections.

### 2. Router-Level Configuration

Apply throttle settings to all routes in a router. If the global middleware is also active, it automatically bypasses routes on this router — the router's own throttle is the only one that applies.

```python
from fastapi import Request
from fastapi_gradual_throttle import ThrottleRouter

# All routes in this router get rate=50/30s.
# The global middleware auto-skips these routes — no double-counting.
api_v1 = ThrottleRouter(prefix="/api/v1", throttle_rate=50, throttle_window=30)

@api_v1.get("/users")
async def list_users(request: Request):
    return {"users": []}

@api_v1.get("/products")
async def list_products(request: Request):
    return {"products": []}

app.include_router(api_v1)
```

### 3. Per-Route Configuration

Override the global throttle for individual endpoints using either `Depends()` or the `@throttle()` decorator.
When a route has its own per-route throttle, the global middleware **automatically bypasses** that route — the route is controlled exclusively by its own throttle rules.

> **How auto-exempt works (v1.2+):**
>
> When the global `GradualThrottleMiddleware` is active:
>
> | Route setup | What the global middleware does | What throttles the route |
> |---|---|---|
> | No decorator | Applies its rate/window/mode normally | Global middleware |
> | `@throttle(rate=10, window=60)` | **Skips** — auto-detects per-route throttle | `@throttle()` only |
> | `Depends(GradualThrottle(rate=10))` | **Skips** — auto-detects dependency | `GradualThrottle` only |
> | `ThrottleRouter(throttle_rate=10)` | **Skips** — auto-detects router dependency | `ThrottleRouter` only |
> | `@throttle_exempt()` | **Skips** — explicitly exempt | Nothing (zero throttle) |

#### `Depends()` Style

```python
from fastapi import Depends, Request
from fastapi_gradual_throttle import GradualThrottle

# Per-route throttle — works with or without global middleware.
# If the global middleware is active, it auto-skips this route.
@app.get("/expensive", dependencies=[Depends(GradualThrottle(rate=5, window=60))])
async def expensive_endpoint(request: Request):
    return {"result": "ok"}
```

#### `@throttle()` Decorator Style

> **Note:** The `@throttle()` decorator requires `async def` route handlers. For sync handlers, use `Depends(GradualThrottle(...))` instead, or convert the handler to `async def`.

```python
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi_gradual_throttle import throttle

@app.get("/search")
@throttle(rate=10, window=60)
async def search(request: Request):
    return JSONResponse({"results": []})

# With all options
@app.post("/upload")
@throttle(
    rate=5,
    window=120,
    base_delay=0.5,
    max_delay=10.0,
    delay_strategy="fastapi_gradual_throttle.strategies.exponential.ExponentialDelayStrategy",
    hard_limit=20,
)
async def upload(request: Request):
    return JSONResponse({"status": "uploaded"})

# Strict rate limit on login
@app.post("/login")
@throttle(rate=5, window=300, mode="strict")
async def login(request: Request):
    return JSONResponse({"token": "..."})

# Dry-run mode for testing
@app.get("/preview")
@throttle(rate=3, window=30, dry_run=True)
async def preview(request: Request):
    return JSONResponse({"data": "preview"})
```

> **Note:** The `@throttle()` decorated handler **must** accept `request: Request` as a parameter so the decorator can extract it for key generation and IP lookup.

#### `@throttle_exempt()` — Opt Out of Global Middleware Entirely

`@throttle_exempt()` tells the global middleware: **"skip this route completely — do not count it, do not delay it, do not 429 it."** The route will have zero throttle protection from the global layer.

**This is different from `@throttle()`:**

| Decorator | What it does | Global middleware behavior |
|---|---|---|
| `@throttle(rate=10, window=60)` | Adds its **own** per-route throttle with custom limits | Middleware **auto-skips** (route is still throttled, just by its own rules) |
| `@throttle_exempt()` | Marks the route as **completely unthrottled** | Middleware **skips** (route has zero throttle protection) |
| Neither | Route uses global middleware limits | Middleware **applies** its rate/window/mode normally |

**When to use `@throttle_exempt()`:**

- Health check endpoints (`/health`, `/ready`, `/live`)
- Metrics / monitoring endpoints (`/metrics`, `/prometheus`)
- Internal probe endpoints that must never be delayed or blocked
- Any route where **zero** throttle is the correct behavior

**When NOT to use `@throttle_exempt()`:**

- Routes with `@throttle()` or `Depends(GradualThrottle())` — these are auto-exempted from the global middleware since v1.2.
- Routes that should follow the global middleware limits — just leave them alone.

```python
from fastapi import Request
from fastapi_gradual_throttle import throttle_exempt

@app.get("/health")
@throttle_exempt()       # no throttle at all — always passes instantly
async def health(request: Request):
    return {"status": "ok"}
```

Routes decorated with `@throttle_exempt()` are detected by the middleware through a `_throttle_exempt` marker attribute set on the endpoint function. On the first request (or eagerly via `init_throttle`), the middleware scans `app.routes` and caches exempt paths on `app.state._throttle_exempt_paths`. The middleware checks the incoming request path against this per-app cache before applying any throttle logic. If the path is cached as exempt, the request passes through immediately without any throttle processing.

#### What Happens if You Use Both `@throttle_exempt()` and `@throttle()` on the Same Route?

**Short answer: the route still works, but it is a misuse — do not do this.**

If you stack both decorators on the same route:

```python
# ❌ DO NOT DO THIS — redundant and confusing
@app.post("/login")
@throttle_exempt()
@throttle(rate=5, window=300, mode="strict")
async def login(request: Request):
    ...
```

Here is what actually happens at runtime:

1. The global middleware sees that `/login` is cached as exempt on `app.state` (`@throttle_exempt()`) and also cached as per-route-throttled (`@throttle()`). It skips its own counting — the route is bypassed by the middleware either way.
2. The `@throttle()` decorator still runs inside the handler and applies its own rate=5/window=300 throttle normally.
3. **Net result:** The per-route throttle applies, the global middleware does not. This is the same behavior as using `@throttle()` alone.

**Why you should not do this:**

- It is **redundant** — `@throttle()` already causes auto-exemption from the global middleware. Adding `@throttle_exempt()` does nothing extra.
- It is **confusing** for readers — it looks like the route should have zero throttle, but it actually has a per-route throttle.
- If you later remove `@throttle()`, the route will still have `@throttle_exempt()` and will be **completely unthrottled** — which may not be what you intended.

**Rule of thumb: choose one.**

| Goal | Use |
|---|---|
| Route has its own custom throttle limits | `@throttle()` alone _(or `Depends(GradualThrottle())`)_ |
| Route must have zero throttle (health, metrics) | `@throttle_exempt()` alone |
| Route should use the global middleware limits | Neither — leave it alone |

#### Per-Route Patterns

**Pattern A — Stricter limit on one route, global for the rest:**

```python
config = ThrottleConfig(rate=100, window=60)
init_throttle(app, config=config)
app.add_middleware(GradualThrottleMiddleware, config=config)

@app.post("/auth/login")
@throttle(rate=5, window=300, mode="strict")             # own stricter limit — auto-exempted from middleware
async def login(request: Request):
    return JSONResponse({"token": "..."})
```

**Pattern B — One route fully exempt, throttle everything else:**

```python
config = ThrottleConfig(rate=100, window=60)
init_throttle(app, config=config)
app.add_middleware(GradualThrottleMiddleware, config=config)

@app.get("/health")
@throttle_exempt()                                      # middleware skips this entirely
async def health(request: Request):
    return JSONResponse({"status": "ok"})
```

**Pattern C — No global middleware, selective routes only:**

```python
# No app.add_middleware() call — no @throttle_exempt() needed

@app.get("/search")
@throttle(rate=10, window=60)
async def search(request: Request):
    return JSONResponse({"results": []})

@app.get("/upload")
@throttle(rate=2, window=60, mode="strict")
async def upload(request: Request):
    return JSONResponse({"status": "ok"})

@app.get("/health")                                     # no decorator = no throttling
async def health(request: Request):
    return JSONResponse({"status": "ok"})
```

**Pattern D — limit_func with per-route:**

```python
@app.get("/api/data")
@throttle(rate=60, window=60, limit_func="myapp.limits.my_limit_func")
async def data(request: Request):
    return JSONResponse({"data": "..."})

# Or with Depends():
@app.get("/api/data")
async def data(
    request: Request,
    _: None = Depends(GradualThrottle(rate=60, limit_func="myapp.limits.my_limit_func")),
):
    return JSONResponse({"data": "..."})
```

#### Combined Auth + Throttle `Depends()` Example

```python
from fastapi import Depends, Request
from fastapi_gradual_throttle import GradualThrottle
from myapp.auth import get_current_user

@app.get("/api/data")
async def get_data(
    current_user: User = Depends(get_current_user),
    _: None = Depends(GradualThrottle(rate=100, window=60)),
):
    return {"data": "..."}

# For user-keyed throttling, use a custom key_func that reads request.state.user
# Make sure AuthMiddleware is registered AFTER GradualThrottleMiddleware
# (see Middleware Order section).
```

### Choosing the Right Approach

| Scenario | Recommended Approach |
|---|---|
| All routes, same limit | Middleware only |
| All routes, one route needs stricter limit | Middleware + `@throttle()` on that route _(auto-exempted)_ |
| All routes, one route fully exempt (zero throttle) | Middleware + `@throttle_exempt()` on that route |
| No global throttle, selective routes only | No middleware + `@throttle()` or `Depends()` per route |
| Different limits per user tier | Middleware + `limit_func` hook |
| Group of routes with shared limit | `ThrottleRouter` |

### When to Use Which

| Approach | Best For | Characteristics |
|---|---|---|
| **Middleware** (`add_middleware`) | Global throttling on all routes | Zero per-route code; app-wide |
| **`ThrottleRouter`** | Group of related endpoints | Router-level defaults; clean separation |
| **`Depends()`** (`GradualThrottle`) | Selective routes via FastAPI DI | Idiomatic FastAPI; composable with other deps |
| **`@throttle()` decorator** | Selective routes with clean syntax | Most concise; handler-level; supports response headers |

## Delay Strategies

### Built-in Strategies

#### Linear Delay (Default)

Delay increases linearly with excess requests: `delay = base_delay * excess_requests`

For example, with `base_delay=0.2`: 1 excess → 0.2s, 5 excess → 1.0s, 10 excess → 2.0s.

```python
app.add_middleware(
    GradualThrottleMiddleware,
    delay_strategy="fastapi_gradual_throttle.strategies.linear.LinearDelayStrategy",
)
```

#### Exponential Delay

Delay increases exponentially: `delay = base_delay * (multiplier ^ (excess_requests - 1))`

The default multiplier is 2.0. For example, with `base_delay=0.2`: 1 excess → 0.2s, 2 excess → 0.4s, 3 excess → 0.8s, 4 excess → 1.6s.

```python
app.add_middleware(
    GradualThrottleMiddleware,
    delay_strategy="fastapi_gradual_throttle.strategies.exponential.ExponentialDelayStrategy",
)
```

#### No Delay (Internal)

Used internally by `mode="strict"` — always returns 0.0 delay. You don't need to configure this directly.

### Custom Delay Strategy

Create your own delay strategy by extending `BaseDelayStrategy`:

```python
from fastapi_gradual_throttle import BaseDelayStrategy

class SteppedDelayStrategy(BaseDelayStrategy):
    def calculate_delay(self, excess_requests: int) -> float:
        if excess_requests <= 0:
            return 0.0
        delay = self.base_delay * (excess_requests ** 1.5)
        return self._clamp_delay(delay)

# Use via dotted path
app.add_middleware(
    GradualThrottleMiddleware,
    delay_strategy="myapp.strategies.SteppedDelayStrategy",
)
```

## Custom Key Functions

By default, requests are keyed by `request.state.user.id` (if present) or client IP address. You can customize this:

```python
# myapp/utils.py
from starlette.requests import Request
from fastapi_gradual_throttle.utils import get_client_ip

def custom_key_func(request: Request) -> str:
    """Key by API key header or IP."""
    api_key = request.headers.get("x-api-key")
    if api_key:
        return f"apikey:{api_key}"
    ip = get_client_ip(
        request,
        trusted_proxies=getattr(request.state, "_throttle_trusted_proxies", None),
    )
    return f"ip:{ip}"

# Configure
app.add_middleware(
    GradualThrottleMiddleware,
    key_func="myapp.utils.custom_key_func",
)
```

## Exemptions

### Path Exemptions

```python
app.add_middleware(
    GradualThrottleMiddleware,
    exempt_paths=["/health/", "/metrics/", "/docs", "/openapi.json"],
)
```

### Custom Exempt Function

```python
# myapp/auth.py
def is_premium_user(request):
    """Exempt premium users from throttling."""
    user = getattr(request.state, "user", None)
    return user and getattr(user, "is_premium", False)

# Async exempt functions are also supported
async def is_internal_service(request):
    """Exempt internal service requests."""
    return request.headers.get("x-internal-token") == "secret"

# Configure
app.add_middleware(
    GradualThrottleMiddleware,
    exempt_func="myapp.auth.is_premium_user",
)
```

> **Note:** If `exempt_func` raises an exception (e.g. database timeout), the request is treated as **not exempt** and a warning is logged. This ensures that failures in the exempt function never accidentally bypass throttling.

## Storage Backends

### Choosing a Backend

| Deployment | Recommended Backend |
|---|---|
| Single process (dev/test) | `InMemoryBackend` (default) |
| Single process (production) | `InMemoryBackend` (acceptable) |
| Multiple workers (`uvicorn -w N`) | `RedisBackend` (required) |
| Multiple instances / containers | `RedisBackend` (required) |
| Custom requirements | Extend `BaseBackend` |

### In-Memory Backend (Default)

Suitable for development and single-process deployments.

- Uses `asyncio.Lock` for safe concurrent access within one process
- LRU eviction when `max_entries` is reached (default 10,000)
- Periodic cleanup of expired entries
- Data is lost on process restart
- **NOT shared between workers** — each worker has independent counters, meaning the effective rate = `config.rate * num_workers`

```python
app.add_middleware(
    GradualThrottleMiddleware,
    backend="fastapi_gradual_throttle.backends.memory.InMemoryBackend",
    backend_options={"max_entries": 10000},
)
```

> **Warning**: With `uvicorn --workers N` (N > 1), each worker maintains its own independent counters. Use Redis for production multi-worker deployments.

### Redis Backend (Production)

Uses atomic Lua scripts for thread-safe increment operations. Required for multi-worker and multi-instance deployments.

- Uses atomic Lua scripts for increment — no race conditions even across multiple workers
- Keys expire automatically via Redis TTL
- Connection pooling via the `redis-py` library
- Handles reconnection automatically
- Requires: `pip install fastapi-gradual-throttle[redis]`
- A startup warning is emitted when using the default `"throttle"` key prefix with Redis

```python
app.add_middleware(
    GradualThrottleMiddleware,
    backend="fastapi_gradual_throttle.backends.redis.RedisBackend",
    backend_options={"url": "redis://localhost:6379/1"},
    key_prefix="myapp_throttle",  # unique prefix to avoid collisions
)
```

> **Warning**: If multiple apps share the same Redis instance, set a unique `key_prefix` to avoid counter collisions.

#### Redis with Backend Lifecycle

Clean up Redis connections on shutdown using FastAPI's lifespan:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi_gradual_throttle import GradualThrottleMiddleware, ThrottleConfig
from fastapi_gradual_throttle.backends.redis import RedisBackend

backend = RedisBackend(url="redis://localhost:6379/1")

@asynccontextmanager
async def lifespan(app):
    yield
    await backend.close()

app = FastAPI(lifespan=lifespan)
```

### Custom Backend

Create your own backend by extending `BaseBackend`:

```python
from fastapi_gradual_throttle.backends.base import BaseBackend

class MyCustomBackend(BaseBackend):

    async def get(self, key: str):
        """Return throttle data for key, or None if not found / expired."""
        ...

    async def set(self, key: str, data: dict, ttl: int):
        """Store data under key with a TTL of ttl seconds."""
        ...

    async def increment(self, key: str, window: int, ttl: int, now: float) -> dict:
        """
        Atomically increment the counter for key and return current state.

        Returns:
            count (int): requests in current window
            window_start (float): unix timestamp when current window started
            previous_count (int): request count from the previous window.
                Used by sliding window algorithm:
                    weighted = previous_count * (1 - elapsed/window) + count
                Set to 0 if no previous window exists.
        """
        ...

    async def token_bucket_consume(
        self, key: str, rate: int, burst_size: int, window: int, ttl: int, now: float
    ) -> dict:
        """
        Attempt to consume one token from the bucket.

        Returns:
            allowed (bool): True if request is allowed
            tokens_remaining (float): tokens left after this request
            retry_after_seconds (float): time until next token available
                = (1 - tokens_remaining) / (rate / window)
        """
        ...

    async def reset(self, key: str) -> None:
        """Delete the throttle counter for this key."""
        ...

    async def ping(self) -> bool:
        """Health check. Return True if backend is reachable."""
        ...

    async def close(self) -> None:
        """Clean up connections on app shutdown."""
        ...
```

## Monitoring & Hooks

Set up monitoring by providing a hook function. Hooks are called on throttle and hard-limit events, and support both sync and async callables.

### Hook Actions

| Action | Mode | When | Extra Kwargs |
|---|---|---|---|
| `"throttled"` | gradual, combined | Request delayed (excess > 0) | `delay`, `dry_run` |
| `"hard_limit_exceeded"` | gradual, combined | Count exceeds `hard_limit` | — |
| `"rate_limited"` | strict | Count exceeds `rate` | — |

### Hook Kwargs

All hooks receive these kwargs:

| Kwarg | Type | Always Present |
|---|---|---|
| `request` | `Request` | Yes |
| `action` | `str` | Yes |
| `current_count` | `int` | Yes |
| `excess_requests` | `int` | Yes |
| `delay` | `float` | Only for `"throttled"` |
| `dry_run` | `bool` | Only for `"throttled"` |

### Complete Production Example (sync)

```python
# myapp/monitoring.py
import logging

logger = logging.getLogger(__name__)

def throttle_hook(request, action, **kwargs):
    """Hook function for monitoring throttling events."""
    client_ip = request.client.host if request.client else "unknown"
    path = request.url.path

    if action == "throttled":
        logger.warning(
            "Throttled %s %s: %d requests, %.2fs delay (dry_run=%s)",
            client_ip,
            path,
            kwargs["current_count"],
            kwargs["delay"],
            kwargs.get("dry_run", False),
        )
    elif action == "hard_limit_exceeded":
        logger.error(
            "Hard limit exceeded %s %s: %d requests — returning 429",
            client_ip,
            path,
            kwargs["current_count"],
        )
    elif action == "rate_limited":
        logger.warning(
            "Rate limited %s %s: %d requests, %d excess — returning 429",
            client_ip,
            path,
            kwargs["current_count"],
            kwargs["excess_requests"],
        )

# Configure
app.add_middleware(
    GradualThrottleMiddleware,
    hook="myapp.monitoring.throttle_hook",
)
```

### Async Hook Example

```python
# myapp/monitoring.py
import httpx

async def send_to_datadog(request, action, **kwargs):
    """Report throttle events to Datadog."""
    async with httpx.AsyncClient() as client:
        await client.post(
            "https://api.datadoghq.com/api/v1/events",
            json={
                "title": f"Throttle {action}",
                "text": f"Path: {request.url.path}, Count: {kwargs['current_count']}",
                "tags": [f"action:{action}", f"path:{request.url.path}"],
            },
            headers={"DD-API-KEY": "your-api-key"},
        )

app.add_middleware(
    GradualThrottleMiddleware,
    hook="myapp.monitoring.send_to_datadog",
)
```

## Response Headers

When `headers_enabled` is `True` (default), the following headers are added to responses:

| Header | Description | When Added |
|---|---|---|
| `X-Throttle-Remaining` | Requests remaining in current window | Always |
| `X-Throttle-Limit` | Request limit per window | Always |
| `X-Throttle-Window` | Time window in seconds | Always |
| `X-Throttle-Delay` | Applied delay in seconds | When delay > 0 |
| `X-Throttle-Excess` | Number of excess requests | When excess > 0 |
| `Retry-After` | Seconds until window resets | On 429 responses and when excess > 0 |

## Security

### IP Spoofing Protection

By default, the library uses the direct socket IP (`request.client.host`) and **ignores** `X-Forwarded-For` and `X-Real-IP` headers. This prevents attackers from rotating fake IPs to bypass rate limits.

To trust proxy headers (when behind a load balancer or reverse proxy), explicitly configure `trusted_proxies`:

```python
app.add_middleware(
    GradualThrottleMiddleware,
    trusted_proxies=["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
)
```

Only when a request arrives from a trusted proxy IP will the `X-Forwarded-For` / `X-Real-IP` headers be used.

### Cache Key Sanitisation

All cache keys are sanitised to prevent injection attacks — special characters are stripped, and overly long keys are truncated.

### Import Validation

Dynamically imported classes are type-checked after import to ensure they implement the correct interface. This is a type-safety guard, not a sandbox — only use dotted paths pointing to trusted, reviewed code.

### Fail-Open Behavior

With `fail_open=True` (default), if the storage backend (Redis, etc.) goes down, requests pass through without throttling rather than crashing your application. Set `fail_open=False` for stricter security posture where backend failure results in 503 errors.

## Environment Variables

All settings can be configured via environment variables with the `FASTAPI_GRADUAL_THROTTLE_` prefix:

```bash
export FASTAPI_GRADUAL_THROTTLE_RATE=100
export FASTAPI_GRADUAL_THROTTLE_WINDOW=60
export FASTAPI_GRADUAL_THROTTLE_MODE=strict
export FASTAPI_GRADUAL_THROTTLE_BACKEND=fastapi_gradual_throttle.backends.redis.RedisBackend
export FASTAPI_GRADUAL_THROTTLE_TRUSTED_PROXIES='["10.0.0.0/8"]'
```

Environment variables are automatically loaded by Pydantic Settings. Explicit constructor kwargs take precedence over env vars.

## Testing

### Test Coverage

The library maintains **211 tests** across **13 test modules** covering all public APIs, modes, backends, strategies, and edge cases.

| Module | Coverage |
|---|---|
| `__init__.py` | 100% |
| `config.py` | 99% |
| `middleware.py` | 89% |
| `decorators.py` | 81% |
| `dependencies.py` | 84% |
| `initializer.py` | 100% |
| `router.py` | 100% |
| `admin.py` | 79% |
| `utils.py` | 95% |
| `exceptions.py` | 100% |
| `exempt.py` | 88% |
| `defaults.py` | 100% |
| `backends/memory.py` | 95% |
| `backends/redis.py` | 32%* |
| `strategies/*` | 100% |
| **Overall** | **87%** |

\* Redis backend coverage is low because it requires a live Redis server. The Lua scripts and protocol are tested via integration tests when Redis is available.

### Running the Library's Tests

```bash
# Install development dependencies
pip install -e .[dev]

# Run tests
pytest

# Run with coverage
pytest --cov=fastapi_gradual_throttle --cov-report=html
```

### Dry Run Mode

For testing and development, use dry run mode to observe throttle decisions without slowing requests:

```python
app.add_middleware(
    GradualThrottleMiddleware,
    dry_run=True,
)
```

In dry-run mode:
- Delays are calculated and logged but **not applied** (no `asyncio.sleep`)
- Hard-limit 429 responses are **still enforced** (strict mode and hard limits still reject)
- Hook functions are still called (with `dry_run=True` in kwargs)
- Headers are still added to responses
- Counters are still incremented in the backend

Use in CI/staging to observe throttle behavior without actually slowing tests.

### Resetting Counters in Tests

Use `reset_throttle_key()` to clean up counters between tests:

```python
from fastapi_gradual_throttle import reset_throttle_key

async def test_login_throttle(client, app):
    # Reset before test to ensure clean state
    await reset_throttle_key("ip:127.0.0.1", app=app)

    for i in range(5):
        r = await client.post("/auth/login")
        assert r.status_code == 200

    r = await client.post("/auth/login")
    assert r.status_code == 429

    # Reset after test (cleanup)
    await reset_throttle_key("ip:127.0.0.1", app=app)
```

### Testing with InMemoryBackend

Always use `InMemoryBackend` in tests (never Redis) so tests are isolated and fast. Override via environment variable or explicit config:

```python
import os
os.environ["FASTAPI_GRADUAL_THROTTLE_BACKEND"] = (
    "fastapi_gradual_throttle.backends.memory.InMemoryBackend"
)
```

### Checking Response Headers in Tests

```python
r = await client.get("/search")
assert r.headers["x-throttle-remaining"] == "9"
assert r.headers["x-throttle-limit"] == "10"
assert r.headers["x-throttle-window"] == "60"
```

## Examples

### Basic Gradual Throttling

```python
# 100 requests per 5 minutes, 0.1s delay per excess request, max 10s
config = ThrottleConfig(rate=100, window=300, base_delay=0.1, max_delay=10.0)
init_throttle(app, config=config)
app.add_middleware(GradualThrottleMiddleware, config=config)
```

### API Rate Limiting (Strict)

```python
# 1000 requests per hour, immediate 429 when exceeded
config = ThrottleConfig(mode="strict", rate=1000, window=3600)
init_throttle(app, config=config)
app.add_middleware(GradualThrottleMiddleware, config=config)
```

### Combined with Hard Limit

```python
# Gradual delay after 100 req/min, hard block at 500
config = ThrottleConfig(
    mode="combined",
    rate=100,
    window=60,
    hard_limit=500,
    delay_strategy="fastapi_gradual_throttle.strategies.exponential.ExponentialDelayStrategy",
)
init_throttle(app, config=config)
app.add_middleware(GradualThrottleMiddleware, config=config)
```

### Login Endpoint Protection

```python
from fastapi_gradual_throttle import throttle

@app.post("/auth/login")
@throttle(rate=5, window=300, mode="strict")  # 5 attempts per 5 minutes
async def login(request: Request):
    return JSONResponse({"token": "..."})
```

### Router-Level API Throttling with Redis

```python
from fastapi import FastAPI
from fastapi_gradual_throttle import init_throttle, ThrottleRouter, ThrottleConfig, GradualThrottleMiddleware

app = FastAPI()

# Global config with Redis
config = ThrottleConfig(
    backend="fastapi_gradual_throttle.backends.redis.RedisBackend",
    backend_options={"url": "redis://localhost:6379/1"},
    key_prefix="myapp_throttle",
    trusted_proxies=["10.0.0.0/8"],
)
init_throttle(app, config=config)
app.add_middleware(GradualThrottleMiddleware, config=config)

# Public API: strict rate limit
public_api = ThrottleRouter(
    prefix="/api/v1", throttle_rate=100, throttle_window=60, throttle_mode="strict"
)

# Internal API: relaxed gradual throttle
internal_api = ThrottleRouter(prefix="/internal", throttle_rate=1000, throttle_window=60)

app.include_router(public_api)
app.include_router(internal_api)
```

### Development Setup

```python
config = ThrottleConfig(
    exempt_paths=["/docs", "/openapi.json", "/health/", "/metrics/"],
    dry_run=True,  # Log only, don't delay
)
init_throttle(app, config=config)
app.add_middleware(GradualThrottleMiddleware, config=config)
```

### Sliding Window for Smooth Rate Limiting

```python
# Sliding window prevents burst spikes at window boundaries
config = ThrottleConfig(mode="strict", rate=100, window=60, window_type="sliding")
init_throttle(app, config=config)
app.add_middleware(GradualThrottleMiddleware, config=config)
```

### Token Bucket for Controlled Bursts

```python
config = ThrottleConfig(
    window_type="token_bucket",
    rate=100,       # sustained req/min (refill rate)
    burst_size=30,  # allow burst of 30 instantly
    window=60,
)
init_throttle(app, config=config)
app.add_middleware(GradualThrottleMiddleware, config=config)
```

### Dynamic Per-User Rate Limits

```python
# myapp/limits.py
def my_limit_func(request):
    user = getattr(request.state, "user", None)
    return 1000 if getattr(user, "is_pro", False) else 60

config = ThrottleConfig(limit_func="myapp.limits.my_limit_func")
init_throttle(app, config=config)
app.add_middleware(GradualThrottleMiddleware, config=config)
```

## Middleware Order

Starlette middleware runs in **reverse** registration order (last registered runs first). `GradualThrottleMiddleware` must be registered **before** auth middleware so that auth runs first and `request.state.user` is populated before `key_func` runs.

```python
app.add_middleware(GradualThrottleMiddleware)  # registered first  → runs second
app.add_middleware(AuthMiddleware)             # registered second → runs first
```

## Utilities

### Resetting a Throttle Counter

Reset the throttle counter for a specific key without flushing all data:

```python
from fastapi_gradual_throttle import reset_throttle_key

# Using the app's global config (backend, key_prefix)
await reset_throttle_key("ip:1.2.3.4", app=app)

# Or with an explicit backend
from fastapi_gradual_throttle.backends.memory import InMemoryBackend
backend = InMemoryBackend()
await reset_throttle_key("ip:1.2.3.4", backend=backend, key_prefix="myapp")
```

### Admin / Inspection Router

The library ships with an optional admin router that exposes live throttle counters via HTTP. Useful for debugging in staging and ops dashboards.

> **Warning**: This router exposes internal throttle counters. **Always** protect it with an authentication dependency. Never mount it without access control in production.

```python
from fastapi import Depends
from fastapi_gradual_throttle.admin import throttle_admin_router

app.include_router(
    throttle_admin_router,
    prefix="/_throttle",
    dependencies=[Depends(require_admin)],  # your auth dependency
)
```

#### `GET /_throttle/key/{key}`

Inspect the current throttle state for a specific cache key.

```bash
curl http://localhost:8000/_throttle/key/ip:1.2.3.4
```

**Response (key found):**

```json
{
  "key": "ip:1.2.3.4",
  "found": true,
  "count": 42,
  "remaining": 58,
  "window_start": 1711843200.0,
  "previous_count": 10
}
```

**Response (key not found):**

```json
{
  "key": "ip:1.2.3.4",
  "found": false
}
```

Requires `init_throttle()` to have been called so the backend and prefix are available. The key parameter is sanitised and length-capped to prevent injection.

### ThrottleException

A dedicated exception class for throttle-related errors. Can be used with FastAPI's exception handlers for custom error responses:

```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi_gradual_throttle import ThrottleException

app = FastAPI()

@app.exception_handler(ThrottleException)
async def throttle_exception_handler(request: Request, exc: ThrottleException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "retry_after": exc.retry_after,
        },
        headers={"Retry-After": str(exc.retry_after)} if exc.retry_after else {},
    )
```

**Attributes:**

| Attribute | Type | Default | Description |
|---|---|---|---|
| `detail` | `str` | `"Too Many Requests"` | Human-readable error message |
| `retry_after` | `int \| None` | `None` | Seconds the client should wait |
| `status_code` | `int` | `429` | HTTP status code |

## WebSocket Support

WebSocket connections are exempt from throttling by default. Set `websocket_exempt=False` to apply throttling to WebSocket upgrades:

```python
app.add_middleware(
    GradualThrottleMiddleware,
    websocket_exempt=False,  # default: True
)
```

## Performance Considerations

- **Backend Choice**: Use Redis for production multi-worker deployments. InMemoryBackend is single-process only.
- **Atomic Operations**: The Redis backend uses Lua scripts for atomic increment — no race conditions under high concurrency.
- **Non-Blocking Delays**: Uses `asyncio.sleep()` (not `time.sleep()`), so delays don't block the event loop.
- **Pure ASGI**: The middleware is a pure ASGI app — no BaseHTTPMiddleware overhead, supports streaming responses.
- **Key Distribution**: Ensure your key function distributes load evenly across cache keys.
- **Window Size**: Larger windows use more memory but provide smoother throttling.
- **Sliding Window**: Adds minimal overhead but prevents the "double burst" problem at fixed-window boundaries.
- **LRU Eviction**: InMemoryBackend caps at 10,000 entries by default to prevent memory leaks.
- **Backend Reuse**: `init_throttle()` creates a shared backend instance. `@throttle()` and `Depends(GradualThrottle())` automatically reuse it instead of creating their own connections.
- **Per-App Path Cache**: Exempt and per-route-throttled paths are stored on `app.state` (not in a global module-level set), so multiple app instances in the same process are fully isolated.

## FAQ

### What happens when Redis goes down?

With `fail_open=True` (default), requests pass through **without throttling**. Your app stays up, but rate limits are temporarily unenforced. Set `fail_open=False` if you prefer returning HTTP 503 when the backend is unreachable — this is recommended for security-critical flows like login or payment endpoints.

### Can I use different rate limits for different users?

Yes. Use `limit_func` — a callable that receives the `Request` and returns an `int`. For example, return `1000` for pro users and `60` for free-tier users. The global `rate` acts as the fallback if `limit_func` raises or returns an invalid value.

```python
def my_limit_func(request):
    user = getattr(request.state, "user", None)
    return 1000 if getattr(user, "is_pro", False) else 60

config = ThrottleConfig(limit_func="myapp.limits.my_limit_func")
```

### How do I prevent double-counting with middleware + per-route throttle?

**You don't need to — it is handled automatically since v1.2.**

When a route uses `@throttle()`, `Depends(GradualThrottle())`, or `ThrottleRouter`, the global middleware detects it and skips its own counting for that route. Only the per-route throttle applies:

```python
# Global middleware (rate=100) is active, but it auto-skips /login.
# Only the per-route @throttle(rate=5) applies to this endpoint.
@app.post("/login")
@throttle(rate=5, window=300, mode="strict")
async def login(request: Request):
    ...
```

`@throttle_exempt()` is **not** needed here. Reserve it for routes that should have **zero** throttle (health checks, metrics).

### Does this work with WebSockets?

WebSocket upgrade requests are **exempt by default** (`websocket_exempt=True`). Set `websocket_exempt=False` to throttle WebSocket handshakes. In strict/combined mode an upgrade can be rejected with HTTP 429 before the connection is established; in gradual mode the handshake is delayed.

### What is the difference between fixed, sliding, and token bucket windows?

- **Fixed** (`window_type="fixed"`) — Counter resets every `window` seconds. Simple and cheap, but allows a burst of up to 2× `rate` at window boundaries.
- **Sliding** (`window_type="sliding"`) — Uses a weighted average of the current and previous window's counts to smooth enforcement. Prevents the double-burst problem.
- **Token bucket** (`window_type="token_bucket"`) — Allows a controlled burst of `burst_size` requests instantly, then refills at `rate/window` per second. Best for bursty-but-fair traffic.

### Can I use this without the global middleware?

Yes. Skip `app.add_middleware(GradualThrottleMiddleware, ...)` entirely and use `@throttle()` or `Depends(GradualThrottle())` on specific routes. No `@throttle_exempt()` is needed in this pattern.

### What is the difference between `@throttle()` and `Depends(GradualThrottle())`?

Both achieve per-route throttling. Key differences:

| | `@throttle()` | `Depends(GradualThrottle())` |
|---|---|---|
| Syntax | Decorator on handler | FastAPI dependency |
| Handler type | Requires `async def` | Works with sync and async |
| Composability | Standalone | Composable with other `Depends()` |
| Response headers | Injected by the decorator | Injected by the dependency |

### How do I test my throttled endpoints?

1. Always use `InMemoryBackend` in tests (no Redis dependency).
2. Use `reset_throttle_key()` to clear counters between tests.
3. Check `X-Throttle-*` response headers to verify throttle behaviour.
4. Use `dry_run=True` in staging to observe decisions without slowing requests.

### Does `dry_run` still reject requests?

Yes. `dry_run=True` only skips the `asyncio.sleep()` delay. Hard-limit 429s (combined mode) and strict-mode 429s **are still returned**. Counters, hooks, and headers all work normally. This lets you validate rejection logic without adding latency.

### How does the admin inspection router work?

Mount `throttle_admin_router` with an auth guard. It exposes `GET /_throttle/key/{key}` to look up live counter state for any cache key. Requires `init_throttle()` to have been called first. **Never expose it without authentication in production.**

### Can I customise the 429 JSON body?

Yes. Set `response_factory` to a dotted path pointing to a `callable(retry_after: int) -> bytes | str`. If your factory raises, the default JSON body is used as a fallback.

## When to Use Which Setting

A quick-reference guide for choosing the right configuration for common scenarios.

### Choosing a Mode

| Scenario | Mode | Why |
|---|---|---|
| Public API — discourage abuse, never break clients | `gradual` | Progressive delays let legitimate clients continue |
| Login / payment — hard cap, no wiggle room | `strict` | Immediate 429 after `rate` is exceeded |
| General API — slow down first, then cut off | `combined` | Gradual delays + safety ceiling via `hard_limit` |

### Choosing a Window Type

| Scenario | Window Type | Why |
|---|---|---|
| Simple setup, low overhead | `fixed` | Cheapest; counters reset every `window` seconds |
| Prevent burst spikes at window edges | `sliding` | Weighted average smooths enforcement |
| Bursty traffic (e.g. page-load fans out 20 requests) | `token_bucket` | Allows controlled burst, then steady refill |

### Choosing a Delay Strategy

| Scenario | Strategy | Why |
|---|---|---|
| Proportional slowdown | `LinearDelayStrategy` | Delay grows steadily: `base_delay × excess` |
| Aggressive back-off for repeat offenders | `ExponentialDelayStrategy` | Delay doubles per excess request |
| Strict mode (no delays) | `NoDelayStrategy` | Set automatically; returns 0 |

### Choosing a Backend

| Scenario | Backend | Why |
|---|---|---|
| Development / single worker | `InMemoryBackend` | Zero dependencies; fast; process-local |
| Production with `uvicorn -w N` or multiple containers | `RedisBackend` | Shared counters across all workers |

### Choosing a Throttle Layer

| Scenario | Layer | Config |
|---|---|---|
| Same limit for every route | Middleware only | `app.add_middleware(GradualThrottleMiddleware, config=config)` |
| Group of related endpoints | `ThrottleRouter` | `ThrottleRouter(prefix="/api", throttle_rate=50)` |
| One route needs a stricter limit | Middleware + `@throttle()` | Auto-exempted — no extra config needed |
| Selective routes, no global throttle | `@throttle()` or `Depends()` only | No middleware needed |
| Per-user tier limits | Any layer + `limit_func` | `limit_func="myapp.limits.my_limit_func"` |

### Common Configuration Recipes

| Use Case | Config |
|---|---|
| Public REST API | `mode="combined"`, `rate=100`, `window=60`, `hard_limit=500`, `window_type="sliding"`, Redis backend |
| Login endpoint | `mode="strict"`, `rate=5`, `window=300` via `@throttle()` |
| Free vs pro tiers | `limit_func` returning different rates per user tier |
| Dev / staging | `dry_run=True`, `InMemoryBackend` |
| Health check exempt | `exempt_paths=["/health/", "/readiness/"]` |
| Behind a load balancer | `trusted_proxies=["10.0.0.0/8"]` |
| Burst-tolerant endpoint | `window_type="token_bucket"`, `burst_size=30`, `rate=100`, `window=60` |

## Known Limitations

The following are acknowledged limitations of the current release. Each is either inherent to the design, not yet implemented, or planned for a future version.

### Storage & State

| # | Limitation | Workaround |
|---|---|---|
| 1 | **`InMemoryBackend` is not shared across OS processes.** With `uvicorn --workers N` (N > 1) each worker tracks its own independent counters, so the effective rate limit per server is `N × rate`. | Use `RedisBackend` for any multi-worker or multi-instance deployment. |
| 2 | **`InMemoryBackend` does not survive restarts.** All counters are lost when the process exits. | Acceptable for development; use `RedisBackend` in production. |
| 3 | **`RedisBackend` Lua SHA is per-instance only.** After a Redis `SCRIPT FLUSH` or failover the SHA cache is stale. The backend retries once, but a second consecutive flush within the same request would still fail. | This scenario is extremely rare in practice; normal Redis failovers do not flush scripts. |

### Configuration

| # | Limitation | Workaround |
|---|---|---|
| 4 | **`mode="combined"` requires `hard_limit > 0`.** Without it the mode silently behaves like `"gradual"`. A `UserWarning` is emitted at config construction time. | Always set `hard_limit` when using `mode="combined"`. |
| 5 | **`burst_size` is ignored when `window_type != "token_bucket"`.** A `UserWarning` is emitted at config construction time. | Remove `burst_size` or switch to `window_type="token_bucket"`. |
| 6 | **`window`, `rate`, and key-prefix fields are not hot-reloadable.** `GradualThrottle` (`Depends`) and `@throttle()` lazy-init on the first request and cache config for the lifetime of the process. | Restart the application after changing throttle config. |
| 7 | **Default `key_prefix="throttle"` with Redis warns about shared-instance collisions.** If multiple apps share the same Redis instance the counters will collide. | Set a unique `key_prefix` per application (e.g. `key_prefix="my_app"`) |

### Per-Route & Decorator

| # | Limitation | Workaround |
|---|---|---|
| 8 | **`@throttle()` requires the handler to declare a `request: Request` parameter.** Handlers without it raise `TypeError` at decoration time. | Add `request: Request` to the handler signature. |
| 9 | **Path-cache for `@throttle_exempt()` and per-route throttles is built at first request** (unless `init_throttle()` is called at startup). Routes added after the first request are not detected. | Always call `init_throttle(app, ...)` in a FastAPI lifespan startup handler. |
| 10 | **`ThrottleRouter` cannot be combined with `@throttle()` on individual routes inside it** without careful ordering — both will run, potentially double-counting. | Use either `ThrottleRouter` *or* `@throttle()` per route, not both. |

### Headers & Responses

| # | Limitation | Workaround |
|---|---|---|
| 11 | **Response headers (`X-Throttle-*`) are only injected when the route returns a Starlette `Response` object** in the `@throttle()` decorator. Plain `dict` returns (auto-serialised by FastAPI) do not receive headers via the decorator path. | The middleware path always injects headers via `_wrap_send`, unaffected. For the decorator, return `JSONResponse(...)` explicitly. |
| 12 | **`Retry-After` header value is an integer (rounded seconds).** Sub-second precision is not exposed in headers per RFC 7231. | No workaround needed — this matches the RFC. |

### WebSocket

| # | Limitation | Workaround |
|---|---|---|
| 13 | **WebSocket connections are exempt by default** (`websocket_exempt=True`). There is no per-message throttling. | Set `websocket_exempt=False` to throttle the initial upgrade handshake. Message-rate limiting inside a WebSocket session is out of scope for this library. |

### Admin Router

| # | Limitation | Workaround |
|---|---|---|
| 14 | **The admin router (`throttle_admin_router`) has no built-in authentication.** Mounting it without a guard exposes internal counter data. | Always pass a `dependencies=[Depends(require_admin)]` when including the router. |
| 15 | **Admin endpoint only reads the fixed-window counter key.** Token-bucket state (`tokens`, `last_refill`) is not surfaced. | Inspect Redis/memory directly for token-bucket state if needed. |

### Planned for Future Releases

- Distributed rate limiting without Redis (e.g. Memcached backend)
- Per-user configurable rate tiers via a richer `limit_func` API
- Metrics export integration (Prometheus / OpenTelemetry)
- Async-safe hot-reload of config without restart
- Per-route admin endpoint scoped to a single path key

---

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request. For major changes, please open an issue first to discuss what you would like to change.

## Versioning

This library follows [Semantic Versioning](https://semver.org/).

**Stable (covered by semver):**
- Middleware constructor kwargs
- `@throttle()` and `@throttle_exempt()` decorators
- `GradualThrottle` dependency, `ThrottleRouter`
- `BaseBackend` and `BaseDelayStrategy` interfaces
- `ThrottleConfig` fields
- `init_throttle`, `reset_throttle_key` utilities

**Unstable (may change in minor releases):**
- Internal/private classes (names starting with `_`)
- Private methods and attributes
- Test helpers and fixtures

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Type Checking

This package ships with a `py.typed` marker file ([PEP 561](https://peps.python.org/pep-0561/)), so type checkers like **mypy** and **pyright** will pick up the inline type annotations automatically — no need for stub packages.

## Changelog

### v1.0.0 — Initial Release
- Three throttle modes: gradual (progressive delays), strict (immediate 429), combined (delay + hard cap)
- Linear and exponential delay strategies with custom strategy support
- Pluggable storage backends — in-memory (dev) and Redis with atomic Lua scripts (production)
- Global + router + per-route configuration hierarchy
- `@throttle()` decorator and `Depends(GradualThrottle())` for per-route overrides
- `@throttle_exempt()` decorator — opt specific routes out of global middleware; detected via endpoint attribute and cached per-app on `app.state`
- `ThrottleRouter` — router-level throttle defaults for groups of endpoints
- `limit_func` hook — dynamic per-request rate limits (e.g. free vs pro tiers)
- `reset_throttle_key()` utility — reset a specific key's counter without flushing all data
- Three window types: fixed, sliding (weighted average), and token bucket (controlled bursts)
- WebSocket support — exempt by default, configurable via `websocket_exempt`
- Security-first IP extraction with `trusted_proxies` (CIDR validated at startup)
- Fail-open backend resilience (`fail_open=True` by default)
- Pure ASGI middleware — no BaseHTTPMiddleware overhead, supports streaming responses
- JSON 429 response bodies with customizable `response_factory`
- Comprehensive configuration validation via Pydantic Settings with env-var support
- Monitoring hooks (sync + async) and dry-run mode
- Optional admin inspection router for live throttle counter lookup
- Response headers (`X-Throttle-Remaining`, `X-Throttle-Limit`, `Retry-After`, etc.)
- PEP 561 `py.typed` marker for mypy / pyright support
- Startup validation: `hard_limit` + `mode="strict"` error, `burst_size` mismatch warning, Redis key prefix collision warning
- `exempt_func` exceptions fail-safe (logs warning, treats as not exempt)
- Concurrency-safe lazy init in `GradualThrottle` and `@throttle()` via `asyncio.Lock`
- Backend instance reuse across all throttle layers via `init_throttle()` + `app.state.throttle_backend`
