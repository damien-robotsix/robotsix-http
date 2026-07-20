"""robotsix-http — shared HTTP retry/backoff library for the robotsix ecosystem."""

from importlib.metadata import PackageNotFoundError, version

from robotsix_http.retry import (
    RetryConfig,
    acall_with_retry,
    call_with_retry,
    is_transient,
)

__all__ = [
    "RetryConfig",
    "acall_with_retry",
    "call_with_retry",
    "is_transient",
]

try:
    __version__ = version(__package__ or __name__.split(".")[0])
except PackageNotFoundError:
    __version__ = "0.0.0.dev0"
