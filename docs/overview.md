# Architecture overview

This page shows how the modules of `robotsix_http` fit together and what
happens to a request on its way through the library. For every tunable
default, see the [configuration reference](configuration.md). The
[README](../README.md) still holds the per-feature deep-dives.

## Module map

| Module | Role | Depends on |
|---|---|---|
| `robotsix_http/__init__.py` | Public import surface. Re-exports the core symbols (`__all__`), installs a `logging.NullHandler` on the `robotsix_http` logger, and loads the optional `fastapi` submodule lazily through a module `__getattr__`. | `client`, `retry`, `safety`, `oauth` |
| `robotsix_http/retry.py` | Retry primitives that work with any callable: `RetryConfig`, `compute_backoff`, `is_transient`, `call_with_retry` (sync) and `acall_with_retry` (async). Depends only on the stdlib and `httpx`. | `httpx` |
| `robotsix_http/client.py` | `RetryClient`: an async HTTP client around a caller-owned `httpx.AsyncClient`. Adds retry/backoff, `Retry-After` handling, idempotency gating by HTTP method, and maps errors to the `ExternalHTTPError` hierarchy. Also defines `DEFAULT_CONFIG`. | `retry`, `httpx` |
| `robotsix_http/safety.py` | URL/IP safety (SSRF) layer: `SSRFGuardTransport` / `guarded_async_client` check addresses at connect time, `validate_url` checks a URL up front, and `safe_http_request` is a request wrapper that never raises and returns an `HttpResult`. | `httpx`, `httpcore` |
| `robotsix_http/oauth.py` | OAuth2 token persistence that works with any provider: secret file I/O (`0600` file inside a `0700` directory), `SecureTokenStore`, `refresh_and_persist`, `build_token_provider`. | stdlib |
| `robotsix_http/fastapi.py` | **Optional** (needs the `fastapi` extra). FastAPI service bootstrap: canonical error envelope, `register_exception_handlers`, `create_health_router` + `ProbeState`, `CorrelationIdMiddleware`, and the re-exported chat-skill helpers. | `fastapi`, `structlog`, `client`, `_chat_skill` |
| `robotsix_http/_chat_skill.py` | Private implementation of the `/chat-skill` descriptor: frontmatter parsing, `create_chat_skill_router`, and the route-parity assertion. Its public names are re-exported from `robotsix_http.fastapi`, so import them from there. | `fastapi` |

The core modules (`retry`, `client`, `safety`, `oauth`) do not depend on one
another in a cycle. `client` builds on `retry`. `safety` and `oauth` stand
alone. Only `fastapi` / `_chat_skill` import FastAPI.

## Request lifecycle

A typical hardened outbound call puts the three core layers together:

```python
from robotsix_http import RetryClient, RetryConfig, guarded_async_client

async with guarded_async_client(allowlist=["api.example.com"]) as http:
    rc = RetryClient(http, config=RetryConfig(max_retries=3))
    resp = await rc.get("https://api.example.com/data")
```

```
caller
  │  rc.get(url) / rc.request(method, url, config=...)
  ▼
RetryClient.request            (client.py)
  │  picks the per-call config, else the client default (DEFAULT_CONFIG)
  │  loop: attempt = 0 … max_retries
  ▼
httpx.AsyncClient.request      (caller-owned client)
  ▼
SSRFGuardTransport             (safety.py, only when built via guarded_async_client)
  │  _SSRFGuardBackend.connect_tcp:
  │    hostname allowlist check → resolve DNS → reject blocked IPs
  │    → connect to the pinned, validated IP (stops DNS rebinding)
  ▼
network ──► response ──► response.raise_for_status()
```

On each failure, `RetryClient.request` does the following:

1. **Last attempt reached** (`attempt == max_retries`): calls
   `on_retry_exhausted`, logs at DEBUG, and raises the mapped exception.
2. **Not retryable for this method** (see the idempotency gate below):
   behaves the same as step 1 and raises at once.
3. **Otherwise** it works out the delay. This is `compute_backoff(attempt, config)`,
   except for a 429 that has a parseable `Retry-After` header, in which case
   the header value is used, capped at `backoff_cap`. If `stop_after_delay`
   is set, the delay is clamped to the time left before the deadline, and
   once the deadline has passed the client raises instead of sleeping.
   Then it calls `on_retry`, logs at DEBUG, and runs `await asyncio.sleep(delay)`.

**Idempotency gate.** `GET`, `DELETE`, `PUT`, `HEAD` and `OPTIONS` are
retried on any transient error. `POST` and `PATCH` are retried only when the
failure shows the request never reached the server (`httpx.ConnectError`,
`ConnectTimeout` or `PoolTimeout` somewhere in the cause chain). They are
never retried after a response was received.

**Transient classification.** By default `is_transient` is used, which
treats as transient `httpx.TimeoutException`, `httpx.TransportError`,
`json.JSONDecodeError`, `APITimeoutError` (matched by class name), and any
error carrying HTTP 429 or 5xx, anywhere in the `__cause__` chain. Pass
`is_transient_fn` to override this.

**Error mapping.** An `httpx.HTTPStatusError` raised at the end is converted to
`ExternalAuthError` (401/403), `ExternalRateLimitError` (429) or
`ExternalServiceError` (5xx), all subclasses of `ExternalHTTPError`. Any other
exception is re-raised unchanged.

### Other entry points

- **`call_with_retry` / `acall_with_retry`** (`retry.py`) run the same
  backoff / deadline / callback logic around any callable, sync or async.
  They have no HTTP-method gating and no exception mapping. `call_with_retry`
  drives the loop with `asyncio.run`, or in a worker thread when an event
  loop is already running.
- **`safe_http_request`** (`safety.py`) wraps any object that has an async
  `request(...)` method (an `httpx.AsyncClient` or a `RetryClient`). It can
  check the scheme and allowlist up front and catches every exception into
  `HttpResult.error`.
- **`validate_url`** performs a blocking check of the scheme, allowlist and
  private IPs before any client is involved. The transport guard is still the
  authoritative check.

## Optional FastAPI extra

`robotsix_http.fastapi` is the service-side half of the library. It is only
importable when the `robotsix-http[fastapi]` extra (`fastapi`, `structlog`)
is installed. `import robotsix_http` never imports it eagerly. Accessing
`robotsix_http.fastapi` triggers a lazy import through the package
`__getattr__`.

It connects back to the client side through `external_http_error_handler`.
That handler turns `ExternalHTTPError` subclasses raised by a `RetryClient`
inside a route into the canonical `{"error": {"code": ..., "detail": ...}}`
envelope. See [fastapi-bootstrap.md](fastapi-bootstrap.md) for the full
guide and [oauth2-persistence.md](oauth2-persistence.md) for the OAuth
helpers.
