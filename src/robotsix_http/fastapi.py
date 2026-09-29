"""Shared FastAPI service-bootstrap: error envelope, exception handlers, health route.

This submodule provides the reusable pieces every robotsix FastAPI service
repeats by hand:

* a single canonical JSON error envelope — ``{"error": {"code": ..., "detail": ...}}``;
* :func:`register_exception_handlers`, wiring the standard handler suite
  (request validation, ``HTTPException``, :class:`DomainError`,
  :func:`external_http_error_handler`, and a catch-all for unhandled errors);
* :func:`create_health_router`, a ``/health`` route factory returning
  ``{"status": "ok"}``;
* :func:`create_chat_skill_router`, a ``/chat-skill`` route factory serving a
  validated ``text/markdown`` chat-access descriptor, plus
  :func:`assert_chat_skill_route_parity` to keep that descriptor in sync with
  the app's real routes.

Importing :mod:`robotsix_http` does **not** import this submodule, so
``fastapi`` stays an *optional* dependency — install it with the
``robotsix-http[fastapi]`` extra.

The envelope shape defined here is the library's single canonical shape.
Reconciling it with the RFC 9457 ``application/problem+json`` variant used by
some existing services is deliberately out of scope and deferred to the
per-service migration tickets.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import structlog
from fastapi import APIRouter, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from structlog.contextvars import bind_contextvars, unbind_contextvars

from robotsix_http.client import (
    ExternalAuthError,
    ExternalHTTPError,
    ExternalRateLimitError,
    ExternalServiceError,
)

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = [
    "ChatSkillFrontmatter",
    "CorrelationIdMiddleware",
    "DomainError",
    "app_route_paths",
    "assert_chat_skill_route_parity",
    "create_chat_skill_router",
    "create_correlation_id_middleware",
    "create_health_router",
    "documented_routes",
    "domain_error_handler",
    "error_envelope",
    "external_http_error_handler",
    "http_exception_handler",
    "parse_chat_skill_frontmatter",
    "register_exception_handlers",
    "unhandled_exception_handler",
    "validation_exception_handler",
]


class DomainError(Exception):
    """Application-level (domain) error mapped to a structured JSON response.

    Services raise this (or a subclass) for expected, client-facing failures.
    :func:`domain_error_handler` renders it into the canonical envelope using
    :attr:`status_code` and :attr:`code`.

    Attributes:
        status_code: HTTP status to respond with (default ``400``).
        code: Stable machine-readable error code (default ``"domain_error"``).
        detail: Human-readable description of the error.
    """

    status_code: int = 400
    code: str = "domain_error"

    def __init__(
        self,
        detail: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


def error_envelope(status_code: int, code: str, detail: Any) -> JSONResponse:
    """Build the canonical error :class:`JSONResponse`.

    The response body is ``{"error": {"code": code, "detail": detail}}``.
    ``detail`` is passed through :func:`fastapi.encoders.jsonable_encoder`
    so arbitrary (e.g. validation-error) structures serialise safely.
    """
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "detail": jsonable_encoder(detail)}},
    )


# Stable HTTP status + error code for each external-HTTP error class, most
# specific first.  Subclasses of ExternalHTTPError not listed fall through to
# the generic ``upstream_error`` mapping.
_EXTERNAL_ERROR_MAP: tuple[tuple[type[ExternalHTTPError], int, str], ...] = (
    (ExternalAuthError, 502, "upstream_auth_error"),
    (ExternalRateLimitError, 429, "upstream_rate_limited"),
    (ExternalServiceError, 502, "upstream_service_error"),
)


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render a request-validation failure (HTTP 422)."""
    del request
    validation_exc = cast(RequestValidationError, exc)
    return error_envelope(422, "validation_error", validation_exc.errors())


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render a Starlette/FastAPI ``HTTPException`` into the envelope."""
    del request
    http_exc = cast(StarletteHTTPException, exc)
    return error_envelope(http_exc.status_code, "http_error", http_exc.detail)


async def domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render a :class:`DomainError` using its status code and error code."""
    del request
    domain_exc = cast(DomainError, exc)
    return error_envelope(domain_exc.status_code, domain_exc.code, domain_exc.detail)


async def external_http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map an :class:`~robotsix_http.ExternalHTTPError` to a stable error code.

    This is the natural companion to ``robotsix-http``: it turns the retry
    client's :class:`~robotsix_http.ExternalAuthError`,
    :class:`~robotsix_http.ExternalRateLimitError`, and
    :class:`~robotsix_http.ExternalServiceError` into deterministic
    ``upstream_*`` error codes so downstream clients can branch on them.
    """
    del request
    external_exc = cast(ExternalHTTPError, exc)
    for exc_type, status_code, code in _EXTERNAL_ERROR_MAP:
        if isinstance(external_exc, exc_type):
            return error_envelope(status_code, code, str(external_exc))
    return error_envelope(502, "upstream_error", str(external_exc))


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unexpected errors (HTTP 500).

    The exception detail is intentionally not leaked to the client.
    """
    del request, exc
    return error_envelope(500, "internal_error", "Internal Server Error")


def register_exception_handlers(app: FastAPI) -> None:
    """Wire the standard exception-handler suite onto *app*.

    Registers, in order of specificity:

    * :class:`fastapi.exceptions.RequestValidationError` → 422
      (:func:`validation_exception_handler`);
    * :class:`starlette.exceptions.HTTPException`
      (:func:`http_exception_handler`);
    * :class:`DomainError` (:func:`domain_error_handler`);
    * :class:`~robotsix_http.ExternalHTTPError`
      (:func:`external_http_error_handler`);
    * :class:`Exception` catch-all → 500
      (:func:`unhandled_exception_handler`).
    """
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(ExternalHTTPError, external_http_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)


def create_health_router(path: str = "/health") -> APIRouter:
    """Return an :class:`~fastapi.APIRouter` exposing a health-check route.

    The route responds to ``GET {path}`` with ``{"status": "ok"}``.
    """
    router = APIRouter()

    @router.get(path)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return router


def _default_correlation_id() -> str:
    """Generate a random hex correlation id."""
    return uuid.uuid4().hex


def _as_name_tuple(value: str | Sequence[str]) -> tuple[str, ...]:
    """Normalise a single name or sequence of names into a non-empty tuple."""
    names = (value,) if isinstance(value, str) else tuple(value)
    if not names:
        raise ValueError("expected at least one header/context name")
    return names


class CorrelationIdMiddleware:
    """Pure-ASGI correlation-ID middleware.

    Deliberately implemented as raw ASGI rather than
    :class:`starlette.middleware.base.BaseHTTPMiddleware` so the correlation id
    bound into ``structlog`` contextvars propagates across ``await`` boundaries
    all the way to the endpoint (and any downstream calls it makes) — a
    ``BaseHTTPMiddleware`` runs the request in a separate task and would break
    that propagation.

    For every incoming HTTP request it:

    * reads the correlation id from the first present request header named in
      *header_name* (default ``X-Request-ID``), or generates one with
      *generator* when none is present;
    * binds that id into ``structlog`` contextvars under every key in
      *context_field* (default ``correlation_id``), unbinding them in a
      ``finally`` block so ids never leak between requests;
    * echoes the id back on the response under the primary (first) header name;
    * optionally logs ``request.start`` / ``request.end`` events — the latter
      carrying a ``duration_ms`` timing — when *log_requests* is true.

    Non-HTTP scopes (lifespan, websockets, …) are passed through untouched.

    Args:
        app: The wrapped ASGI application.
        header_name: Request header (or ordered sequence of candidate headers)
            to read the inbound id from; the first entry is also the header the
            id is echoed back on.
        context_field: One or more ``structlog`` contextvar keys to bind the id
            to.
        generator: Zero-argument callable returning a fresh id when no inbound
            header is present.
        log_requests: When true, emit ``request.start`` / ``request.end`` logs.
        logger: Optional ``structlog`` logger to use; defaults to a logger named
            ``robotsix_http.correlation``.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        header_name: str | Sequence[str] = "X-Request-ID",
        context_field: str | Sequence[str] = "correlation_id",
        generator: Callable[[], str] = _default_correlation_id,
        log_requests: bool = False,
        logger: Any | None = None,
    ) -> None:
        self.app = app
        self._request_headers = _as_name_tuple(header_name)
        self._response_header = self._request_headers[0]
        self._context_fields = _as_name_tuple(context_field)
        self._generator = generator
        self._log_requests = log_requests
        self._logger = (
            logger if logger is not None else structlog.get_logger("robotsix_http.correlation")
        )

    def _extract(self, headers: Headers) -> str | None:
        """Return the first non-empty inbound correlation header, if any."""
        for name in self._request_headers:
            value = headers.get(name)
            if value:
                return value
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        correlation_id = self._extract(Headers(scope=scope)) or self._generator()
        response_header = self._response_header

        async def send_with_correlation_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(raw=message["headers"])[response_header] = correlation_id
            await send(message)

        bind_contextvars(**{field: correlation_id for field in self._context_fields})
        start = time.perf_counter()
        if self._log_requests:
            self._logger.info(
                "request.start",
                method=scope.get("method"),
                path=scope.get("path"),
            )
        try:
            await self.app(scope, receive, send_with_correlation_id)
        finally:
            if self._log_requests:
                duration_ms = (time.perf_counter() - start) * 1000.0
                self._logger.info(
                    "request.end",
                    method=scope.get("method"),
                    path=scope.get("path"),
                    duration_ms=round(duration_ms, 3),
                )
            unbind_contextvars(*self._context_fields)


def create_correlation_id_middleware(
    app: FastAPI,
    *,
    header_name: str | Sequence[str] = "X-Request-ID",
    context_field: str | Sequence[str] = "correlation_id",
    generator: Callable[[], str] = _default_correlation_id,
    log_requests: bool = False,
    logger: Any | None = None,
) -> None:
    """Register :class:`CorrelationIdMiddleware` on *app*.

    Convenience wrapper over ``app.add_middleware(CorrelationIdMiddleware, ...)``
    that mirrors :func:`register_exception_handlers`.  All keyword arguments are
    forwarded verbatim to :class:`CorrelationIdMiddleware`.
    """
    app.add_middleware(
        CorrelationIdMiddleware,
        header_name=header_name,
        context_field=context_field,
        generator=generator,
        log_requests=log_requests,
        logger=logger,
    )


# ---------------------------------------------------------------------------
# Chat-skill descriptor helpers
#
# The robotsix chat-access standard mandates an opt-in ``GET /chat-skill``
# endpoint that serves a ``text/markdown`` descriptor whose YAML frontmatter
# declares a kebab-case ``name`` (the component id) and a one-sentence
# ``description``.  The helpers below let every FastAPI service serve and
# validate that descriptor — and keep it in sync with real routes — instead of
# hand-rolling the serving, the frontmatter and the route-parity test.
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)
_KEBAB_CASE_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_DOCUMENTED_ROUTE_RE = re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[^\s`)>\"']*)")
_DEFAULT_PARITY_IGNORE = frozenset({"/health", "/chat-skill"})


@dataclass(frozen=True)
class ChatSkillFrontmatter:
    """Validated YAML frontmatter of a chat-skill descriptor.

    :param name: the kebab-case component id.
    :param description: the one-sentence component description.
    """

    name: str
    description: str


def _unquote(value: str) -> str:
    """Strip a single pair of matching surrounding quotes from ``value``."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_chat_skill_frontmatter(markdown_text: str) -> ChatSkillFrontmatter:
    """Parse and validate the YAML frontmatter of a chat-skill descriptor.

    Per the robotsix chat-access standard the descriptor must begin with a
    ``---``-delimited YAML block declaring a kebab-case ``name`` and a
    non-empty one-sentence ``description``.  Only those two scalar fields are
    required; any others are ignored.

    :raises ValueError: if the frontmatter block is missing, the ``name`` is
        absent or not kebab-case, or the ``description`` is absent or empty.
    """
    match = _FRONTMATTER_RE.match(markdown_text)
    if match is None:
        raise ValueError(
            "chat-skill descriptor must begin with a '---'-delimited YAML frontmatter block"
        )

    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition(":")
        if not sep:
            continue
        fields[key.strip()] = _unquote(value.strip())

    name = fields.get("name", "")
    description = fields.get("description", "")
    if not _KEBAB_CASE_RE.fullmatch(name):
        raise ValueError(f"chat-skill frontmatter 'name' must be kebab-case; got {name!r}")
    if not description:
        raise ValueError(
            "chat-skill frontmatter 'description' must be a non-empty one-sentence string"
        )
    return ChatSkillFrontmatter(name=name, description=description)


def create_chat_skill_router(
    markdown_text: str,
    *,
    path: str = "/chat-skill",
    name: str | None = None,
) -> APIRouter:
    """Return an :class:`~fastapi.APIRouter` serving a chat-skill descriptor.

    The route responds to ``GET {path}`` with HTTP 200 and the descriptor as
    ``text/markdown`` — mirroring :func:`create_health_router`.

    The descriptor's frontmatter is validated eagerly (see
    :func:`parse_chat_skill_frontmatter`) so a malformed descriptor fails at
    router-construction time rather than on first request.  Pass ``name`` to
    additionally assert the frontmatter ``name`` matches the expected
    component id.

    :raises ValueError: if the frontmatter is invalid or ``name`` is given and
        does not match the frontmatter ``name``.
    """
    frontmatter = parse_chat_skill_frontmatter(markdown_text)
    if name is not None and frontmatter.name != name:
        raise ValueError(
            f"chat-skill frontmatter 'name' {frontmatter.name!r} does not "
            f"match expected component id {name!r}"
        )

    router = APIRouter()

    @router.get(path, response_class=PlainTextResponse)
    async def chat_skill() -> PlainTextResponse:
        return PlainTextResponse(markdown_text, media_type="text/markdown")

    return router


def app_route_paths(app: FastAPI) -> set[str]:
    """Return the set of HTTP route paths exposed by ``app``.

    Uses ``app.openapi()`` so included sub-routers are resolved and
    framework-mounted routes (``/openapi.json``, ``/docs``, ``/redoc``) are
    excluded automatically.  Routes hidden from the schema
    (``include_in_schema=False``) are not returned.
    """
    paths: dict[str, Any] = app.openapi().get("paths", {})
    return set(paths)


def documented_routes(markdown_text: str) -> set[str]:
    """Return the set of route paths documented in a chat-skill descriptor.

    A route is considered documented when its path follows an HTTP method verb
    (e.g. ``GET /events`` or ``POST /events/{event_id}``) anywhere in the
    descriptor body — the shape the chat-access standard uses to classify
    read-only vs confirmation-gated operations.
    """
    paths: set[str] = set()
    for raw in _DOCUMENTED_ROUTE_RE.findall(markdown_text):
        path = raw.rstrip("`.,;:)")
        if path:
            paths.add(path)
    return paths


def assert_chat_skill_route_parity(
    app: FastAPI,
    markdown_text: str,
    *,
    ignore: Iterable[str] = (),
) -> None:
    """Assert the chat-skill descriptor and ``app`` describe the same routes.

    Gives every service calendar's guarantee for free: every route registered
    on ``app`` is documented in the descriptor, and every route documented in
    the descriptor resolves to a registered route.  ``/health`` and
    ``/chat-skill`` are ignored by default; pass ``ignore`` to skip additional
    infrastructure paths.

    Intended to be called from a pytest test — combine with
    :func:`app_route_paths` and :func:`documented_routes` when you want to
    parametrize over individual routes instead.

    :raises AssertionError: if any registered route is undocumented or any
        documented route does not resolve.
    """
    ignored = _DEFAULT_PARITY_IGNORE | frozenset(ignore)
    registered = app_route_paths(app) - ignored
    documented = documented_routes(markdown_text) - ignored

    undocumented = sorted(registered - documented)
    if undocumented:
        raise AssertionError(
            "routes registered on the app but absent from the chat-skill "
            f"descriptor: {undocumented}"
        )

    dangling = sorted(documented - registered)
    if dangling:
        raise AssertionError(
            "routes documented in the chat-skill descriptor that do not "
            f"resolve to a registered app route: {dangling}"
        )
