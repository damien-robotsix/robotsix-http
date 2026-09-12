"""robotsix-http — shared HTTP retry/backoff library for the robotsix ecosystem."""

import logging
from importlib.metadata import PackageNotFoundError, version

from robotsix_http.client import (
    DEFAULT_CONFIG,
    ExternalAuthError,
    ExternalHTTPError,
    ExternalRateLimitError,
    ExternalServiceError,
    RetryClient,
)
from robotsix_http.retry import (
    RetryConfig,
    acall_with_retry,
    call_with_retry,
    compute_backoff,
    is_transient,
)

logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = [
    "DEFAULT_CONFIG",
    "ExternalAuthError",
    "ExternalHTTPError",
    "ExternalRateLimitError",
    "ExternalServiceError",
    "RetryClient",
    "RetryConfig",
    "acall_with_retry",
    "call_with_retry",
    "compute_backoff",
    "is_transient",
]

try:
    __version__ = version(__package__ or __name__.split(".")[0])
except PackageNotFoundError:
    __version__ = "0.0.0.dev0"


def __getattr__(name: str) -> object:
    """Lazily expose the optional ``fastapi`` submodule.

    ``robotsix_http.fastapi`` deliberately imports ``fastapi`` (an optional
    dependency installed via the ``robotsix-http[fastapi]`` extra), so it is
    not imported eagerly at package top level.  Accessing it here — e.g.
    ``from robotsix_http import fastapi`` or ``robotsix_http.fastapi`` —
    imports it on demand without breaking fastapi-free installs.
    """
    if name == "fastapi":
        import importlib

        return importlib.import_module(f"{__name__}.fastapi")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Include the lazy ``fastapi`` submodule in :func:`dir` results."""
    return sorted(set(globals()) | {"fastapi"})
