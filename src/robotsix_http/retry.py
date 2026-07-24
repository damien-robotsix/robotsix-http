"""Generic retry primitives with exponential backoff and jitter.

Domain-neutral — depends only on stdlib and httpx.  No LLM-specific
logic (UsageLimitExceeded, fallback models, hardcoded OpenTelemetry).
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import random
from collections.abc import Callable, Coroutine
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Exception introspection helpers
# ---------------------------------------------------------------------------


def _walk_cause_chain(exc: BaseException, max_depth: int = 10) -> Any:
    """Iterate an exception's cause chain up to *max_depth* steps.

    Yields each exception in the chain starting from *exc* itself,
    then following ``__cause__`` links.
    """
    current: BaseException | None = exc
    for _ in range(max_depth):
        if current is None:
            break
        yield current
        current = current.__cause__


def _status(exc: BaseException) -> int | None:
    """Extract ``status_code`` from any httpx-shaped HTTP error.

    Checks the exception itself for a ``status_code`` attribute, then
    falls back to ``exc.response.status_code`` (``httpx.HTTPStatusError``
    shape) and ``exc.response.status``.
    """
    # Direct attribute on the exception
    status: Any = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status

    # httpx.HTTPStatusError has exc.response with status_code
    response: Any = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status
        # httpx.Response also exposes .status (an alias in some versions)
        status = getattr(response, "status", None)
        if isinstance(status, int):
            return status

    return None


def is_transient(exc: BaseException) -> bool:
    """Return ``True`` for exceptions that warrant a retry.

    Considered transient:
    * ``httpx.TimeoutException``
    * ``httpx.TransportError``
    * ``json.JSONDecodeError``
    * Any exception carrying HTTP status 429 or 5xx (checked on the
      exception itself and throughout its cause chain).
    """
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError, json.JSONDecodeError)):
        return True

    status = _status(exc)
    if status is not None and (status == 429 or 500 <= status < 600):
        return True

    # Walk the cause chain (skip *exc* itself — already checked above).
    for cause in _walk_cause_chain(exc):
        if cause is exc:
            continue
        if isinstance(cause, (httpx.TimeoutException, httpx.TransportError)):
            return True
        s = _status(cause)
        if s is not None and (s == 429 or 500 <= s < 600):
            return True

    return False


# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class RetryConfig:
    """Immutable configuration for the retry loop.

    Attributes:
        max_retries: Maximum number of retry attempts (default 4).
        backoff_base: Base for exponential backoff (default 2.0).
        backoff_cap: Maximum backoff delay in seconds (default 30.0).
        jitter_factor: Fraction of the computed delay to subtract as
            random jitter.  A factor of 0.5 means the actual delay
            ranges from 50 % to 100 % of the computed value.
        on_retry: Optional callback invoked on each retry with
            ``(attempt: int, exception: Exception, delay: float)``.
            *attempt* is 1-indexed.
    """

    max_retries: int = 4
    backoff_base: float = 2.0
    backoff_cap: float = 30.0
    jitter_factor: float = 0.5
    on_retry: Callable[[int, Exception, float], None] | None = None


# ---------------------------------------------------------------------------
# Backoff computation
# ---------------------------------------------------------------------------


def _compute_backoff(attempt: int, config: RetryConfig) -> float:
    """Compute the exponential-backoff delay for *attempt* (0-indexed).

    The raw delay is ``min(backoff_base ** attempt, backoff_cap)``.
    Jitter is subtracted multiplicatively: the actual delay returned
    is in the range ``[delay * (1 - jitter_factor), delay]``.

    Returns:
        Backoff delay in seconds (always ≥ 0).
    """
    delay: float = min(config.backoff_base**attempt, config.backoff_cap)
    jitter: float = delay * config.jitter_factor * random.random()
    return delay - jitter


# ---------------------------------------------------------------------------
# Generic retry loop (async core)
# ---------------------------------------------------------------------------


async def _retry_loop[T](
    fn: Callable[..., T],
    *,
    invoke: Callable[[Callable[..., T]], Coroutine[Any, Any, T]],
    sleep_fn: Callable[[float], Coroutine[Any, Any, None]],
    config: RetryConfig,
    what: str,  # noqa: ARG001 — reserved for future error-context use
    is_transient_fn: Callable[[Exception], bool],
) -> T:
    """Generic async retry loop.

    Parameters:
        fn: The callable to retry (sync or async).
        invoke: Async wrapper that calls *fn* and returns its result.
        sleep_fn: Async sleep callable (e.g. ``asyncio.sleep``).
        config: Retry configuration.
        what: Human-readable operation description (reserved).
        is_transient_fn: Predicate returning ``True`` for retryable errors.

    Returns:
        The return value of *fn* on success.

    Raises:
        The last exception caught when retries are exhausted or the
        error is non-transient.
    """
    last_exc: Exception | None = None
    for attempt in range(config.max_retries + 1):
        try:
            return await invoke(fn)
        except Exception as exc:
            last_exc = exc
            if attempt == config.max_retries:
                raise
            if not is_transient_fn(exc):
                raise
            delay = _compute_backoff(attempt, config)
            if config.on_retry is not None:
                config.on_retry(attempt + 1, exc, delay)
            await sleep_fn(delay)

    # Should be unreachable — satisfy the type checker.
    assert last_exc is not None  # pragma: no cover
    raise last_exc  # pragma: no cover


# ---------------------------------------------------------------------------
# Sync / async public API
# ---------------------------------------------------------------------------


def _drive_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    """Drive an async coroutine to completion in a synchronous context."""
    return asyncio.run(coro)


def _resolve_config(
    config: RetryConfig | None,
    is_transient_fn: Callable[[Exception], bool] | None,
) -> tuple[RetryConfig, Callable[[Exception], bool]]:
    """Return ``(config, is_transient_fn)`` with defaults applied when ``None``."""
    cfg = config if config is not None else RetryConfig()
    transient = is_transient_fn if is_transient_fn is not None else is_transient
    return cfg, transient


async def _invoke[T](f: Callable[..., T]) -> T:
    """Call *f* and ``await`` the result when it is awaitable."""
    result = f()
    if inspect.isawaitable(result):
        return await result  # type: ignore
    return result


def call_with_retry[T](
    fn: Callable[..., T],
    *,
    config: RetryConfig | None = None,
    what: str = "operation",
    is_transient_fn: Callable[[Exception], bool] | None = None,
) -> T:
    """Call *fn* synchronously with automatic retry on transient errors.

    Parameters:
        fn: The callable to invoke (sync or async).
        config: Retry configuration (uses defaults when ``None``).
        what: Human-readable label for the operation (reserved).
        is_transient_fn: Optional custom transient-error predicate.
            Defaults to :func:`is_transient`.

    Returns:
        The return value of *fn*.
    """
    cfg, transient = _resolve_config(config, is_transient_fn)

    return _drive_sync(
        _retry_loop(
            fn,
            invoke=_invoke,
            sleep_fn=asyncio.sleep,
            config=cfg,
            what=what,
            is_transient_fn=transient,
        )
    )


async def acall_with_retry[T](
    fn: Callable[..., T],
    *,
    config: RetryConfig | None = None,
    what: str = "operation",
    is_transient_fn: Callable[[Exception], bool] | None = None,
) -> T:
    """Call *fn* asynchronously with automatic retry on transient errors.

    Parameters:
        fn: The callable to invoke (sync or async).
        config: Retry configuration (uses defaults when ``None``).
        what: Human-readable label for the operation (reserved).
        is_transient_fn: Optional custom transient-error predicate.
            Defaults to :func:`is_transient`.

    Returns:
        The return value of *fn*.
    """
    cfg, transient = _resolve_config(config, is_transient_fn)

    return await _retry_loop(
        fn,
        invoke=_invoke,
        sleep_fn=asyncio.sleep,
        config=cfg,
        what=what,
        is_transient_fn=transient,
    )
