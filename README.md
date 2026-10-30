# robotsix-http

Shared HTTP retry/backoff library for the robotsix ecosystem.

Consumed from git via `uv.sources` — not published to a package index.

## Documentation

- **[docs/overview.md](docs/overview.md)** — architecture overview: module map, request lifecycle (`RetryClient` → retry loop → SSRF-guard transport), and the optional FastAPI extra.
- **[docs/configuration.md](docs/configuration.md)** — consolidated configuration reference: every `RetryConfig` field, safety/allowlist options, `ProbeState` and health-check timeouts, and logger names.
- **[docs/fastapi-bootstrap.md](docs/fastapi-bootstrap.md)** — FastAPI service-bootstrap guide.
- **[docs/oauth2-persistence.md](docs/oauth2-persistence.md)** — OAuth2 token persistence guide.

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
| `compute_backoff` | `compute_backoff(attempt, config)` — returns the jittered exponential backoff delay (seconds) used internally by the retry loops for a given attempt number and `RetryConfig`. The raw delay is `min(backoff_base ** attempt, backoff_cap)`; multiplicative jitter then yields a value in `[delay * (1 - jitter_factor), delay]` (always ≥ 0). |
| `is_transient` | Predicate: returns `True` for `httpx.TimeoutException`, `httpx.TransportError`, `json.JSONDecodeError`, and any exception carrying HTTP 429 or 5xx (walking the cause chain). |
| `guarded_async_client` | Async client factory that wraps `httpx.AsyncClient` with SSRF protection: blocks private IP ranges at connection time (defeating DNS-rebinding) and optionally enforces a hostname allowlist. |
| `safe_http_request` | Never-raises request wrapper: performs a request and returns an `HttpResult` instead of raising, for callers that prefer explicit result handling. |
| `HttpResult` | Result dataclass from `safe_http_request`: `ok`, `url`, `status_code`, `text`, `headers`, `error`, and the underlying `response`. |
| `validate_url` | Eager URL validation: checks the scheme, an optional hostname allowlist, and that the hostname does not resolve to a blocked IP range, raising `SSRFError` on any violation. |
| `SSRFError` | Exception raised by the safety layer when a URL, hostname, or resolved IP is blocked (invalid scheme, not in allowlist, or private/reserved range). |
| `SSRFGuardTransport` | `httpx` transport that pins the resolved IP and re-validates it against blocked ranges before the TCP connection is established. |
| `ALLOWED_SCHEMES` | Tuple of URL schemes permitted by default by the safety layer (`"http"` and `"https"`). |
| `write_secret_file` | Write an opaque secret string to a file with `0600` mode inside a `0700` parent directory; a no-op if path is `None` or empty. |
| `read_secret_file` | Read and return the text content of a secret file; returns `None` if path is `None`/empty, file is missing, or any read error occurs. |
| `SecureTokenStore` | Secure on-disk cache for a provider-defined token value; combines file I/O with caller-supplied serialization (e.g. JSON, MSAL); tolerates missing/corrupt payloads gracefully. |
| `refresh_and_persist` | Return a valid cached token or refresh and re-persist it; orchestrates the load-check-refresh-persist lifecycle. |
| `build_token_provider` | Build a zero-argument `() -> str` access-token provider callable for downstream HTTP clients; composes an acquire function with an extract function. |

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

## OAuth2 token persistence

Several robotsix services (e.g. `robotsix-linkedin`, `robotsix-auto-mail`) persist OAuth2 credentials to disk with the same security posture: a `0600` secret file inside a `0700` parent directory, wrapped in a refresh-and-repersist lifecycle behind a zero-argument token-provider callable. The `robotsix_http.oauth` module extracts the provider-agnostic primitives (`write_secret_file`, `read_secret_file`, `SecureTokenStore`, `refresh_and_persist`, `build_token_provider`) so each consumer can layer its provider-specific serialization on top.

See **[docs/oauth2-persistence.md](docs/oauth2-persistence.md)** for the full guide: secure file I/O, the token store, the refresh-and-repersist lifecycle, the token-provider factory, and a complete end-to-end example.

## FastAPI service bootstrap

The optional `robotsix_http.fastapi` submodule provides a canonical error envelope, wired exception handlers, a health-check route factory, correlation-ID middleware, and a chat-access descriptor route — eliminating boilerplate duplication across services. Because `fastapi` is an optional dependency, the submodule is re-exported lazily (install the `fastapi` extra to use it).

See **[docs/fastapi-bootstrap.md](docs/fastapi-bootstrap.md)** for the full guide: setup, the error envelope, the exception-handler suite, domain errors, health checks, correlation-ID middleware, and the chat-access descriptor with route-parity testing.

### Health and probe routes

`create_health_router()` mounts only `GET /health` → `200 {"status": "ok"}`. Pass a `ProbeState` (with `mark_started()` / `mark_shutting_down()`) and/or named async `checks` to add the probe routes:

| Route | Default path | Responds |
|---|---|---|
| liveness | `/health/live` | `200 ok`; `503 shutting_down` once `mark_shutting_down()` is called. Runs no dependency checks. |
| startup | off (`startup_path=None`) | `503 starting` until `mark_started()` is called, then `200 ok`. |
| readiness | `/health/ready` | `503 starting` / `503 shutting_down` in those states. Otherwise runs all checks concurrently, each with its own `check_timeout_s` (default 2.0 s) timeout. Returns `503 {"status": "unready", "failures": ["name: error"]}` if any check fails, else `200 ok`. |

See [docs/fastapi-bootstrap.md](docs/fastapi-bootstrap.md#liveness-startup-and-readiness-probes) for a lifespan example.

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
