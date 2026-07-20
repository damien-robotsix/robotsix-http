"""robotsix-http — shared HTTP retry/backoff library for the robotsix ecosystem."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version(__package__ or __name__.split(".")[0])
except PackageNotFoundError:
    __version__ = "0.0.0.dev0"
