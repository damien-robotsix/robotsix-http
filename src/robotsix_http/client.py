"""Async RetryClient wrapping httpx.AsyncClient with retry, backoff, and Retry-After.

Depends on the domain-neutral retry primitives in ``robotsix_http.retry``.
"""

from __future__ import annotations

import asyncio
import datetime
import email.utils
import re
from typing import Any

import httpx

from robotsix_http.retry import (
    RetryConfig,
    _compute_backoff,
    is_transient,
)

# ---------------------------------------------------------------------------
# Default configuration (module-level singleton)
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = RetryConfig()

# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class ExternalHTTPError(Exception):
    """Base exception for HTTP errors returned by an external service.

    Attributes:
        status_code: The HTTP status code.
        response: The :class:`httpx.Response` object.
    """

    def __init__(self, message: str, *, status_code: int, response: httpx.Response) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class ExternalAuthError(ExternalHTTPError):
    """Authentication / authorisation error (HTTP 401 or 403)."""


class ExternalRateLimitError(ExternalHTTPError):
    """Rate limit exhausted (HTTP 429)."""


class ExternalServiceError(ExternalHTTPError):
    """Upstream service error (HTTP 5xx)."""


# ---------------------------------------------------------------------------
# Retry-After parsing
# ---------------------------------------------------------------------------

_RETRY_AFTER_DIGITS = re.compile(r"^\s*\d+\s*$")


def _parse_retry_after(header: str) -> float | None:
    """Parse a ``Retry-After`` header value into delay-seconds from now.

    Handles both delta-seconds (integer) and HTTP-date formats.
    Returns ``None`` when the header is absent or unparseable.
    """
    if not header:
        return None
    if _RETRY_AFTER_DIGITS.match(header):
        return float(header.strip())
    # Attempt HTTP-date parse.
    try:
        parsed = email.utils.parsedate_to_datetime(header)
        delta = (parsed - datetime.datetime.now(datetime.UTC)).total_seconds()
        return max(0.0, delta)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Idempotency helpers
# ---------------------------------------------------------------------------

# Methods whose semantics guarantee at-most-once or idempotent behaviour,
# so they can be safely retried on *any* transient error (including 5xx).
_SAFE_METHODS: frozenset[str] = frozenset({"GET", "DELETE", "PUT", "HEAD", "OPTIONS"})


def _is_retryable_for_method(method: str, exc: Exception) -> bool:
    """Determine whether *exc* warrants a retry given the HTTP *method*.

    Per the idempotency gate:
    * **POST / PATCH** — never retried on HTTP response errors (the
      server may have already acted on the request); only network /
      transport-level errors are retried.
    * **GET / DELETE / PUT / HEAD / OPTIONS** — retried freely (all
      transient errors including 429 and 5xx).
    """
    if method.upper() in _SAFE_METHODS:
        return is_transient(exc)
    # For POST and PATCH: a response was received, so the server processed
    # the request — do not retry regardless of status code.
    if isinstance(exc, httpx.HTTPStatusError):
        return False
    return is_transient(exc)


# ---------------------------------------------------------------------------
# Exception mapping
# ---------------------------------------------------------------------------


def _map_exception(exc: Exception) -> Exception:
    """Map an *exc* to the appropriate :class:`ExternalHTTPError` subclass.

    Only :class:`httpx.HTTPStatusError` instances are mapped; all other
    exceptions are returned unchanged.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return ExternalAuthError(str(exc), status_code=status, response=exc.response)
        if status == 429:
            return ExternalRateLimitError(str(exc), status_code=status, response=exc.response)
        if 500 <= status < 600:
            return ExternalServiceError(str(exc), status_code=status, response=exc.response)
    return exc


def _extract_retry_after(exc: Exception) -> float | None:
    """Extract and parse ``Retry-After`` from a 429 response, if present."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    if exc.response.status_code != 429:
        return None
    return _parse_retry_after(exc.response.headers.get("Retry-After", ""))


# ---------------------------------------------------------------------------
# RetryClient
# ---------------------------------------------------------------------------


class RetryClient:
    """Async HTTP client with automatic retry, backoff, and ``Retry-After`` support.

    Wraps an existing :class:`httpx.AsyncClient` — the caller owns the
    client's lifecycle (creation, configuration, and closing).

    Parameters:
        client: The underlying :class:`httpx.AsyncClient` to use for requests.
        config: Default retry configuration.  Individual method calls may
            override it via their ``config`` keyword argument.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        config: RetryConfig = DEFAULT_CONFIG,
    ) -> None:
        self._client = client
        self._config = config

    # -- Convenience methods -------------------------------------------------

    async def get(
        self, url: str, *, config: RetryConfig | None = None, **kwargs: Any
    ) -> httpx.Response:
        """Issue a GET request with retry."""
        return await self.request("GET", url, config=config, **kwargs)

    async def post(
        self, url: str, *, config: RetryConfig | None = None, **kwargs: Any
    ) -> httpx.Response:
        """Issue a POST request with retry (idempotency-gated)."""
        return await self.request("POST", url, config=config, **kwargs)

    async def patch(
        self, url: str, *, config: RetryConfig | None = None, **kwargs: Any
    ) -> httpx.Response:
        """Issue a PATCH request with retry (idempotency-gated)."""
        return await self.request("PATCH", url, config=config, **kwargs)

    async def delete(
        self, url: str, *, config: RetryConfig | None = None, **kwargs: Any
    ) -> httpx.Response:
        """Issue a DELETE request with retry."""
        return await self.request("DELETE", url, config=config, **kwargs)

    async def put(
        self, url: str, *, config: RetryConfig | None = None, **kwargs: Any
    ) -> httpx.Response:
        """Issue a PUT request with retry (idempotency-gated)."""
        return await self.request("PUT", url, config=config, **kwargs)

    async def head(
        self, url: str, *, config: RetryConfig | None = None, **kwargs: Any
    ) -> httpx.Response:
        """Issue a HEAD request with retry."""
        return await self.request("HEAD", url, config=config, **kwargs)

    async def options(
        self, url: str, *, config: RetryConfig | None = None, **kwargs: Any
    ) -> httpx.Response:
        """Issue an OPTIONS request with retry (idempotency-gated)."""
        return await self.request("OPTIONS", url, config=config, **kwargs)

    # -- Core request method -------------------------------------------------

    async def request(
        self,
        method: str,
        url: str,
        *,
        config: RetryConfig | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Issue an HTTP request with automatic retry and backoff.

        Parameters:
            method: HTTP method (``GET``, ``POST``, …).
            url: Target URL.
            config: Per-call retry configuration override.
            **kwargs: Forwarded to :meth:`httpx.AsyncClient.request`.

        Returns:
            The :class:`httpx.Response`.

        Raises:
            ExternalAuthError: After exhausting retries on a 401 / 403.
            ExternalRateLimitError: After exhausting retries on a 429.
            ExternalServiceError: After exhausting retries on a 5xx.
            Exception: The original exception for non-HTTP errors when
                retries are exhausted or the error is non-transient.
        """
        cfg = config if config is not None else self._config

        last_exc: Exception | None = None
        for attempt in range(cfg.max_retries + 1):
            try:
                response = await self._client.request(method, url, **kwargs)
                response.raise_for_status()
                return response
            except Exception as exc:
                last_exc = exc
                if attempt == cfg.max_retries:
                    raise _map_exception(exc) from exc
                if not _is_retryable_for_method(method, exc):
                    raise _map_exception(exc) from exc

                delay = self._compute_delay(attempt, exc, cfg)
                if cfg.on_retry is not None:
                    cfg.on_retry(attempt + 1, exc, delay)
                await asyncio.sleep(delay)

        # Should be unreachable.
        assert last_exc is not None  # pragma: no cover
        raise _map_exception(last_exc)  # pragma: no cover

    # -- Internal helpers ----------------------------------------------------

    @staticmethod
    def _compute_delay(attempt: int, exc: Exception, config: RetryConfig) -> float:
        """Compute the backoff delay, overriding with ``Retry-After`` on 429.

        When the exception is an :class:`httpx.HTTPStatusError` with status 429
        and a parseable ``Retry-After`` header, the header value (capped to
        *config.backoff_cap*) is used.  Otherwise the standard exponential
        backoff is computed.
        """
        retry_after = _extract_retry_after(exc)
        if retry_after is not None:
            return min(retry_after, config.backoff_cap)
        return _compute_backoff(attempt, config)
