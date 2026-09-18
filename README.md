# robotsix-http

Shared HTTP retry/backoff library for the robotsix ecosystem.

Consumed from git via `uv.sources` — not published to a package index.

## Installation

Add a git source to your `pyproject.toml`:

```toml
[tool.uv.sources]
robotsix-http = { git = "https://github.com/damien-robotsix/robotsix-http.git" }
```

Then install with `uv sync` or `uv add` as usual.

### Optional FastAPI integration

To use the FastAPI service-bootstrap helpers (error envelope, exception handlers, health route), install with the `fastapi` extra:

```toml
[project.dependencies]
robotsix-http = { version = "*", extras = ["fastapi"] }
```

## Quick start

```python
import httpx
from robotsix_http import RetryClient, RetryConfig, ExternalHTTPError


async def main():
    # RetryClient wraps an existing httpx.AsyncClient — you own its lifecycle
    async with httpx.AsyncClient() as client:
        rc = RetryClient(client)

        try:
            resp = await rc.get("https://api.example.com/data")
            print(resp.json())
        except ExternalHTTPError as exc:
            print(f"HTTP {exc.status_code}: {exc}")


# asyncio.run(main())
```

All requests through `RetryClient` are automatically retried on transient
errors (timeouts, transport errors, 429, 5xx) with exponential backoff and
jitter — no extra code needed.

### Custom retry configuration

```python
from robotsix_http import RetryConfig

# Tighter retries: at most 2 retries, 10-second cap
config = RetryConfig(max_retries=2, backoff_cap=10.0)

# Per-client default
rc = RetryClient(client, config=config)

# Or per-call override
resp = await rc.get("https://api.example.com/data", config=config)
```

`RetryConfig` validates all parameters at construction time, raising `ValueError` if any value is invalid (e.g., negative `max_retries`, zero `backoff_cap`, or `jitter_factor` outside [0, 1.0]). See the class docstring for constraint details.

### Custom transient classification

Like the low-level `call_with_retry` / `acall_with_retry` functions, `RetryClient`
accepts an optional `is_transient_fn` predicate that overrides which exceptions are
considered transient and warrant a retry. Omit it (or pass `None`) to keep the
default `is_transient()` classification:

```python
import httpx
from robotsix_http import RetryClient


def is_retryable(exc):
    """Retry on the default transient set, plus any HTTP 408 response."""
    response = getattr(exc, "response", None)
    if isinstance(response, httpx.Response) and response.status_code == 408:
        return True
    return isinstance(exc, httpx.TimeoutException) or isinstance(exc, httpx.TransportError)


rc = RetryClient(client, is_transient_fn=is_retryable)
```

The predicate is honoured on every method (both the freely-retried GET/DELETE/PUT/
HEAD/OPTIONS set and the idempotency-gated POST/PATCH pre-delivery path). Note that
the idempotency gate still applies: even with a custom predicate, a POST/PATCH that
already received a response is never retried.

## API overview

| Symbol | Description |
|---|---|
| `RetryClient` | Async HTTP client wrapping `httpx.AsyncClient` with automatic retry, backoff, and `Retry-After` support. Provides `.get()`, `.post()`, `.patch()`, `.delete()`, `.put()`, `.head()`, `.options()`, and a general `.request()` method. |
| `RetryConfig` | Frozen dataclass controlling retry behaviour: `max_retries` (default 4), `backoff_base` (2.0), `backoff_cap` (30.0 s), `jitter_factor` (0.5), an optional `stop_after_delay` wall-clock deadline in seconds (default `None` = unbounded), and optional `on_retry` / `on_retry_exhausted` callbacks. |
| `DEFAULT_CONFIG` | Module-level `RetryConfig` singleton with sensible defaults. |
| `call_with_retry` | Synchronous retry loop for an arbitrary callable. Uses `asyncio.run()` internally so it works with both sync and async functions. |
| `acall_with_retry` | Async retry loop for an arbitrary callable. Call from within an existing event loop. |
| `is_transient` | Predicate: returns `True` for `httpx.TimeoutException`, `httpx.TransportError`, `json.JSONDecodeError`, and any exception carrying HTTP 429 or 5xx (walking the cause chain). |
| `guarded_async_client` | Async client factory that wraps `httpx.AsyncClient` with SSRF protection: blocks private IP ranges at connection time (defeating DNS-rebinding) and optionally enforces a hostname allowlist. |
| `safe_http_request` | Never-raises request wrapper: performs a request and returns an `HttpResult` instead of raising, for callers that prefer explicit result handling. |
| `HttpResult` | Result dataclass from `safe_http_request`: `ok`, `url`, `status_code`, `text`, `headers`, `error`, and the underlying `response`. |
| `validate_url` | Eager URL validation: checks the scheme, an optional hostname allowlist, and that the hostname does not resolve to a blocked IP range, raising `SSRFError` on any violation. |
| `SSRFError` | Exception raised by the safety layer when a URL, hostname, or resolved IP is blocked (invalid scheme, not in allowlist, or private/reserved range). |
| `SSRFGuardTransport` | `httpx` transport that pins the resolved IP and re-validates it against blocked ranges before the TCP connection is established. |
| `ALLOWED_SCHEMES` | Tuple of URL schemes permitted by default by the safety layer (`"http"` and `"https"`). |

> The optional `robotsix_http.fastapi` submodule (lazy re-export) is not part of the core `__all__`; see its dedicated section below.

### Exception hierarchy

```
Exception
  └── ExternalHTTPError(message, *, status_code, response)
        ├── ExternalAuthError            ← HTTP 401 / 403
        ├── ExternalRateLimitError       ← HTTP 429
        └── ExternalServiceError         ← HTTP 5xx
```

All exceptions carry the original `status_code` and `httpx.Response` object for
inspection.

## URL/IP safety (SSRF protection)

`robotsix_http` provides a security-hardened HTTP layer to defend against Server-Side Request Forgery (SSRF) attacks and other unsafe outbound requests. The safety layer validates URL schemes, hostnames against an optional allowlist, and most critically, blocks requests to private IP ranges (RFC 1918, loopback, link-local, multicast, reserved).

Private IPs are blocked at connection time (not before), so DNS-rebinding attacks cannot occur — the resolved IP is pinned and re-validated before the TCP connection is established.

### Protected IP ranges

The SSRF guard blocks connections to:
- **Loopback** — `127.0.0.1`, `::1`
- **Private (RFC 1918)** — `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`
- **ULA** — `fc00::/7` (IPv6 private)
- **Link-local** — `169.254.0.0/16` (IPv4), `fe80::/10` (IPv6)
- **Multicast** — `224.0.0.0/4` (IPv4), `ff00::/8` (IPv6)
- **Reserved & unspecified** — `0.0.0.0`, `255.255.255.255`, `::`

### Guarded async client

Use `guarded_async_client()` to create a client with automatic SSRF protection:

```python
from robotsix_http import guarded_async_client

# Create a client that blocks private IPs
client = guarded_async_client()

try:
    # Requests to public IPs are allowed
    response = await client.get("https://example.com")
    
    # Requests to private IPs are rejected at connection time
    response = await client.get("http://10.0.0.1")  # SSRFError
finally:
    await client.aclose()
```

### Hostname allowlist

Restrict requests to a specific set of domains:

```python
from robotsix_http import guarded_async_client

# Only api.example.com and its subdomains are allowed
client = guarded_async_client(allowlist=["example.com"])

# Allowed
await client.get("https://api.example.com")
await client.get("https://api.v2.example.com")

# Blocked
await client.get("https://evil.com")  # SSRFError
```

### Never-raises request wrapper

For callers that prefer explicit result handling over exceptions, use `safe_http_request()`:

```python
from robotsix_http import safe_http_request, HttpResult

result = await safe_http_request(client, "GET", "https://api.example.com")

if result.ok:
    print(f"Success: {result.status_code}")
    print(f"Body: {result.text}")
else:
    print(f"Failed: {result.error}")
    print(f"Status: {result.status_code}")  # None if no response received
```

`HttpResult` fields:
- `ok` — `True` when a response was received and its status is not an error (< 400)
- `url` — The final request URL (post-redirect on success, else the requested URL)
- `status_code` — HTTP status code, or `None` if no response was received
- `text` — Decoded response body, or `""` if unavailable
- `headers` — Response headers, or an empty mapping
- `error` — A `"<ExcType>: <message>"` string on failure, else `None`
- `response` — The underlying `httpx.Response`, or `None` on failure

### Eager validation

To validate a URL before issuing a request, call `validate_url()`:

```python
from robotsix_http import validate_url, SSRFError

url = "https://api.example.com/data"

try:
    validate_url(url, allowlist=["example.com"])
    # URL is valid; safe to use
except SSRFError as exc:
    print(f"Blocked: {exc}")
```

`validate_url()` performs a blocking DNS lookup for hostname targets and raises `SSRFError` on any of:
- Disallowed URL scheme (only `http` and `https` are permitted by default)
- Hostname not in allowlist
- Hostname resolves to a blocked IP range

### Combining with RetryClient

`RetryClient` and the guarded client are compatible — wrap a guarded client with `RetryClient` to get both retry logic and SSRF protection:

```python
from robotsix_http import guarded_async_client, RetryClient

client = guarded_async_client(allowlist=["api.example.com"])
rc = RetryClient(client)

# All requests through rc have automatic retry + SSRF protection
response = await rc.get("https://api.example.com/data")
```

## Idempotency gating

`RetryClient` uses the HTTP method to decide whether retrying on a response
error is safe:

| Method | Retry on 5xx / 429? | Retry on transport error? |
|---|---|---|
| **GET, DELETE, PUT, HEAD, OPTIONS** | Yes | Yes |
| **POST, PATCH** | **No** — the server may have already acted | Yes |

For POST and PATCH, only network-level errors (timeouts, transport failures)
trigger a retry. If a response was received — even a 5xx — the request is not
retried, because the server may have already processed it.

## Low-level retry primitives

When you need retry logic outside of HTTP request/response cycles (e.g. for a
database call or SDK wrapper), use the generic retry functions:

```python
from robotsix_http import call_with_retry, acall_with_retry, RetryConfig

# Sync
result = call_with_retry(my_function, config=RetryConfig(max_retries=3))

# Async
result = await acall_with_retry(my_async_function)

# Custom transient predicate
from robotsix_http import is_transient


def my_transient_check(exc):
    return isinstance(exc, MyRetryableError) or is_transient(exc)


result = call_with_retry(my_function, is_transient_fn=my_transient_check)
```

## FastAPI service bootstrap

The optional `robotsix_http.fastapi` submodule provides a canonical error envelope, wired exception handlers, and a health-check route factory — eliminating boilerplate duplication across services.

Because `fastapi` is an optional dependency, the submodule is not imported eagerly at package top level. It is re-exported lazily, so both import styles work once the `fastapi` extra is installed:

```python
from robotsix_http import fastapi  # lazy re-export, no eager fastapi import
from robotsix_http.fastapi import register_exception_handlers  # explicit
```

### Setup

Wire all exception handlers onto your FastAPI app:

```python
from fastapi import FastAPI
from robotsix_http.fastapi import register_exception_handlers, create_health_router

app = FastAPI()

# Register the standard exception handler suite
register_exception_handlers(app)

# Optional: add a health-check route
app.include_router(create_health_router())
```

### Error envelope

All errors render into a canonical JSON envelope:

```json
{
  "error": {
    "code": "upstream_auth_error",
    "detail": "..."
  }
}
```

### Exception handlers

The registered handlers cover:

| Exception | HTTP Status | Error Code |
|---|---|---|
| `RequestValidationError` (invalid request body) | 422 | `validation_error` |
| `HTTPException` (FastAPI/Starlette) | Via `.status_code` | `http_error` |
| `DomainError` (application-level error) | Via `.status_code` (default 400) | Via `.code` (default `domain_error`) |
| `ExternalAuthError` (upstream 401/403) | 502 | `upstream_auth_error` |
| `ExternalRateLimitError` (upstream 429) | 429 | `upstream_rate_limited` |
| `ExternalServiceError` (upstream 5xx) | 502 | `upstream_service_error` |
| Unhandled `Exception` (catch-all) | 500 | `internal_error` |

### Domain errors

Raise `DomainError` for expected, client-facing failures:

```python
from robotsix_http.fastapi import DomainError


@app.post("/items")
async def create_item(data: ItemSchema):
    if not data.name:
        raise DomainError("Item name is required", code="missing_name")
    # ...
```

### Health check

The default health route responds to `GET /health` with `{"status": "ok"}`. Customize the path:

```python
app.include_router(create_health_router(path="/healthz"))
```

### Chat-access descriptor

Per the `robotsix-standards/docs/chat-access-standard.md`, every service exposes an opt-in `GET /chat-skill` endpoint serving a `text/markdown` descriptor with YAML frontmatter. The descriptor declares the component id (`name`, kebab-case) and a one-sentence description, followed by a safety-rules section classifying operations as read-only or confirmation-gated.

Use `create_chat_skill_router()` to serve the descriptor without hand-rolling the serving or frontmatter:

```python
from robotsix_http.fastapi import create_chat_skill_router

CHAT_SKILL = """\
---
name: robotsix-calendar
description: Calendar service exposing CRUD operations over events.
---

# robotsix-calendar

## Safety rules

- `GET /events` — list events (read-only).
- `POST /events` — create an event (confirmation-gated).
- `DELETE /events/{event_id}` — delete an event (confirmation-gated).
"""

app.include_router(create_chat_skill_router(CHAT_SKILL, name="robotsix-calendar"))
```

The `name` parameter is optional but recommended — it asserts the descriptor's frontmatter name matches your expected component id, catching mismatches at startup time.

#### Route-parity testing

Use `assert_chat_skill_route_parity()` to ensure the descriptor and your app stay in sync — every registered route is documented, and every documented route resolves:

```python
from robotsix_http.fastapi import assert_chat_skill_route_parity


def test_chat_skill_routes_match_api():
    """Descriptor routes match the app's real routes."""
    assert_chat_skill_route_parity(app, CHAT_SKILL)
```

`/health` and `/chat-skill` are ignored by default. Pass `ignore` to skip additional infrastructure paths:

```python
assert_chat_skill_route_parity(app, CHAT_SKILL, ignore={"/metrics", "/admin"})
```

## Logging

`robotsix_http` emits DEBUG records on the package loggers
(`robotsix_http.client` and `robotsix_http.retry`) as it schedules and
exhausts retries — no configuration needed. By default nothing is printed
(the package registers a `NullHandler`), so to observe retry/backoff behaviour
just enable logging at DEBUG for the package:

```python
import logging

logging.getLogger("robotsix_http").setLevel(logging.DEBUG)
```

On failure/backoff the logged message reads like
`retry attempt 2/5 failed (...); next in 1.80s`, and retry exhaustion logs
`retries exhausted after N attempt(s): ...`. This complements (does not
replace) the programmatic `on_retry` / `on_retry_exhausted` callbacks.
