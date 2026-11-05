# Configuration reference

This page lists every configuration knob in `robotsix_http` and its default
value in one place. Each value comes from the source module named in its
section. For how the pieces fit together, see the
[architecture overview](overview.md).

## Retry — `RetryConfig` (`robotsix_http.retry`)

`RetryConfig` is a frozen dataclass. Every value is checked in
`__post_init__`, and an invalid value raises `ValueError` when the object is
constructed.

| Field | Type | Default | Constraint | Meaning |
|---|---|---|---|---|
| `max_retries` | `int` | `4` | `>= 0` | Number of retries after the first attempt (so at most `max_retries + 1` attempts in total). |
| `backoff_base` | `float` | `2.0` | `> 0` | Base of the exponential backoff. The raw delay for attempt *n* (counting from 0) is `backoff_base ** n`. |
| `backoff_cap` | `float` | `30.0` | `> 0` | Upper bound on the raw backoff delay, in seconds. It also caps a `Retry-After` value in `RetryClient`. |
| `jitter_factor` | `float` | `0.5` | `0 <= x <= 1.0` | Random jitter subtracted from each delay. The actual delay falls in `[delay * (1 - jitter_factor), delay]`. |
| `stop_after_delay` | `float \| None` | `None` | `> 0` when set | Total wall-clock deadline in seconds, covering all attempts and sleeps. `None` means no deadline. Sleeps are clamped to the time left before the deadline, and once it has passed the last exception is raised. |
| `on_retry` | `Callable[[int, Exception, float], None] \| None` | `None` | — | Called before each retry sleep with `(attempt, exception, delay)`, where `attempt` counts from 1. |
| `on_retry_exhausted` | `Callable[[int, Exception], None] \| None` | `None` | — | Called just before the final raise (retries used up, a non-retryable error, or the deadline passed) with `(attempts, exception)`. |

Where a `RetryConfig` comes from:

- `RetryClient(client, config=DEFAULT_CONFIG, is_transient_fn=None)`:
  `config` is the default for the whole client. Every request method also
  accepts `config=` to override it for a single call. `DEFAULT_CONFIG` is
  `RetryConfig()`, i.e. all the defaults above.
- `call_with_retry(fn, *, config=None, what="operation", is_transient_fn=None)` /
  `acall_with_retry(...)`: `config=None` means `RetryConfig()`.
  `what` is a label reserved for future use.
- `is_transient_fn`: a predicate `(Exception) -> bool` that replaces
  `is_transient` (accepted by `RetryClient`, `call_with_retry` and
  `acall_with_retry`). `None` keeps the default classification.

Fixed behaviour that is **not** configurable (`robotsix_http.client`):

- Methods retried on any transient error: `GET`, `DELETE`, `PUT`, `HEAD`,
  `OPTIONS`.
- `POST` / `PATCH` are retried only on pre-delivery errors
  (`httpx.ConnectError`, `httpx.ConnectTimeout`, `httpx.PoolTimeout`).
- `Retry-After` is honoured only on 429 responses, in either delay-seconds
  or HTTP-date form, and is capped at `backoff_cap`.

## URL/IP safety (`robotsix_http.safety`)

| Option | Where | Default | Meaning |
|---|---|---|---|
| `ALLOWED_SCHEMES` | module constant | `frozenset({"http", "https"})` | Default set of permitted URL schemes. |
| `allowlist` | `guarded_async_client`, `SSRFGuardTransport`, `validate_url`, `safe_http_request` | `None` | Optional sequence of permitted hostnames/domains. `None` or empty allows every host. Matching ignores case and accepts the exact host or any subdomain (an `example.com` entry allows `api.example.com`). |
| `allowed_schemes` | `validate_url`, `safe_http_request` | `ALLOWED_SCHEMES` | Schemes accepted by the up-front check. |
| `validate` | `safe_http_request` | `True` | When `True`, the scheme and allowlist are checked before the request is sent. |
| `**kwargs` | `guarded_async_client` → `httpx.AsyncClient`; `SSRFGuardTransport` → `httpx.AsyncHTTPTransport`; `safe_http_request` → `client.request` | — | Passed through unchanged (e.g. `timeout`, `headers`, `limits`). |

The transport guard always blocks the following, with no option to turn it
off: loopback, private (RFC 1918 / ULA), link-local, multicast, reserved and
unspecified addresses (IPv4-mapped IPv6 addresses are unwrapped first),
unresolvable hosts, and Unix-domain-socket connections. The library sets no
default request timeout. Pass `timeout=` to the `httpx.AsyncClient` (or to
`guarded_async_client`).

## OAuth2 persistence (`robotsix_http.oauth`)

| Option | Where | Default | Meaning |
|---|---|---|---|
| `path` | `write_secret_file`, `read_secret_file`, `SecureTokenStore` | — | Secret file location. `None` or empty turns persistence into a no-op. |
| `encoding` | `write_secret_file`, `read_secret_file` | `"utf-8"` | Text encoding of the secret file. |

Secret files are written with mode `0600` inside a parent directory with mode
`0700`, and these modes cannot be changed. See
[oauth2-persistence.md](oauth2-persistence.md).

## FastAPI health probes (`robotsix_http.fastapi`)

Available only with the `robotsix-http[fastapi]` extra.

`create_health_router(...)` parameters:

| Parameter | Default | Meaning |
|---|---|---|
| `path` | `"/health"` | Basic health route, which always returns `200 {"status": "ok"}`. |
| `live_path` | `"/health/live"` | Liveness route. `None` skips it. Mounted only when `checks` or `state` is given. |
| `ready_path` | `"/health/ready"` | Readiness route. `None` skips it. Mounted only when `checks` or `state` is given. |
| `startup_path` | `None` | Startup route, off by default. |
| `checks` | `None` | Mapping of name → async zero-argument check. The readiness route runs all checks concurrently. |
| `check_timeout_s` | `2.0` | Timeout in seconds for **each** readiness check. A check that times out is reported as `"name: timed out after Ns"`. |
| `state` | `None` | A `ProbeState`. If `checks` is given without `state`, an already-started `ProbeState` is used. |

`ProbeState(*, started=False)` holds the probe lifecycle:
`mark_started()` flips `started` (startup/readiness stop returning
`503 starting`), and `mark_shutting_down()` flips `shutting_down`
(liveness/readiness return `503 shutting_down`).

Other FastAPI-side defaults:

| Symbol | Option | Default |
|---|---|---|
| `DomainError` | `status_code` / `code` | `400` / `"domain_error"` (override per subclass or per instance) |
| `CorrelationIdMiddleware` / `create_correlation_id_middleware` | `header_name` | `"X-Request-ID"` |
| | `context_field` | `"correlation_id"` (structlog contextvar) |
| | `generator` | random hex UUID (`uuid.uuid4().hex`) |
| | `log_requests` | `False` |
| | `logger` | `structlog.get_logger("robotsix_http.correlation")` |
| `create_chat_skill_router` | `path` | `"/chat-skill"` |
| | `name` | `None` (when set, must match the descriptor's frontmatter `name`) |
| `assert_chat_skill_route_parity` | ignored routes | `/health`, `/chat-skill` |

## Logger names

| Logger | Library | Emits |
|---|---|---|
| `robotsix_http` | stdlib `logging` | Package root. A `NullHandler` is attached, so nothing is printed until you configure logging. |
| `robotsix_http.client` | stdlib `logging` | DEBUG: `retry attempt N/M failed (...); next in X.XXs` and `retries exhausted after N attempt(s): ...` from `RetryClient`. |
| `robotsix_http.retry` | stdlib `logging` | DEBUG: the same retry/exhaustion messages from `call_with_retry` / `acall_with_retry`. |
| `robotsix_http.safety` | stdlib `logging` | DEBUG: failures captured by `safe_http_request`. |
| `robotsix_http.oauth` | stdlib `logging` | DEBUG: secret-file permission/read problems and corrupt token payloads that were ignored. |
| `robotsix_http.correlation` | `structlog` | INFO request logs from `CorrelationIdMiddleware` when `log_requests=True`. |

To see the retry behaviour, raise the package logger to DEBUG:

```python
import logging

logging.getLogger("robotsix_http").setLevel(logging.DEBUG)
```
