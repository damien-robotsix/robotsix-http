"""Shared FastAPI service-bootstrap: error envelope, exception handlers, health route.

This submodule provides the reusable pieces every robotsix FastAPI service
repeats by hand:

* a single canonical JSON error envelope — ``{"error": {"code": ..., "detail": ...}}``;
* :func:`register_exception_handlers`, wiring the standard handler suite
  (request validation, ``HTTPException``, :class:`DomainError`,
  :func:`external_http_error_handler`, and a catch-all for unhandled errors);
* :func:`create_health_router`, a ``/health`` route factory returning
  ``{"status": "ok"}``.

Importing :mod:`robotsix_http` does **not** import this submodule, so
``fastapi`` stays an *optional* dependency — install it with the
``robotsix-http[fastapi]`` extra.

The envelope shape defined here is the library's single canonical shape.
Reconciling it with the RFC 9457 ``application/problem+json`` variant used by
some existing services is deliberately out of scope and deferred to the
per-service migration tickets.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from robotsix_http.client import (
    ExternalAuthError,
    ExternalHTTPError,
    ExternalRateLimitError,
    ExternalServiceError,
)

__all__ = [
    "DomainError",
    "create_health_router",
    "domain_error_handler",
    "error_envelope",
    "external_http_error_handler",
    "http_exception_handler",
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
