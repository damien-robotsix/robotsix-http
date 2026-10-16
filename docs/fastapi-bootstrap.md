# FastAPI service bootstrap

The optional `robotsix_http.fastapi` submodule provides a canonical error envelope, wired exception handlers, and a health-check route factory — eliminating boilerplate duplication across services.

Because `fastapi` is an optional dependency, the submodule is not imported eagerly at package top level. It is re-exported lazily, so both import styles work once the `fastapi` extra is installed:

```python
from robotsix_http import fastapi  # lazy re-export, no eager fastapi import
from robotsix_http.fastapi import register_exception_handlers  # explicit
```

## Setup

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

## Error envelope

All errors render into a canonical JSON envelope:

```json
{
  "error": {
    "code": "upstream_auth_error",
    "detail": "..."
  }
}
```

## Exception handlers

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

## Domain errors

Raise `DomainError` for expected, client-facing failures:

```python
from robotsix_http.fastapi import DomainError


@app.post("/items")
async def create_item(data: ItemSchema):
    if not data.name:
        raise DomainError("Item name is required", code="missing_name")
    # ...
```

## Health check

The default health route responds to `GET /health` with `{"status": "ok"}`. Customize the path:

```python
app.include_router(create_health_router(path="/healthz"))
```

## Correlation ID middleware

The `CorrelationIdMiddleware` automatically attaches a correlation/request ID to every request, binding it into `structlog` contextvars so it propagates across `await` boundaries to all downstream code and logging.

It is implemented as raw ASGI (not `BaseHTTPMiddleware`) to ensure contextvar propagation reaches your endpoints and any downstream calls they make.

```python
from robotsix_http.fastapi import create_correlation_id_middleware

app = FastAPI()

# Register the middleware (default: read/generate X-Request-ID, bind as correlation_id)
create_correlation_id_middleware(app)

# All requests now have correlation_id bound in structlog contextvars
```

For each request, the middleware:

- **Reads or generates** an ID: extracts an existing correlation ID from the first matching request header (e.g. `X-Request-ID`), or generates one with `uuid.uuid4().hex` if absent
- **Binds it** into `structlog` contextvars so every log call in that request carries the ID
- **Echoes it back** on the response under the same header name
- **Unbinds after** the request completes, preventing leaks between requests

### Customization

Override the header name(s), context field(s), ID generator, and optional request timing logs:

```python
create_correlation_id_middleware(
    app,
    header_name=["X-Correlation-ID", "X-Request-ID"],  # Primary header is the first one
    context_field=["correlation_id", "request_id"],
    generator=lambda: "custom-prefix-" + uuid.uuid4().hex,
    log_requests=True,  # Log request.start / request.end with duration_ms
)
```

Parameters:

- `header_name` — one header name (string) or ordered list of candidate headers to read from. The first entry is the header the ID is echoed back on.
- `context_field` — one field name (string) or list of `structlog` contextvar keys to bind the ID under.
- `generator` — zero-argument callable returning a fresh ID (default: `uuid.uuid4().hex`).
- `log_requests` — when true, emit `request.start` and `request.end` log events with timing (default: `False`).
- `logger` — optional `structlog` logger instance (default: `structlog.get_logger("robotsix_http.correlation")`).

## Chat-access descriptor

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

### Route-parity testing

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
