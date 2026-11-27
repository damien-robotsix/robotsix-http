"""Shared pytest fixtures for the ``core`` module test suite."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest


@pytest.fixture
def httpx_status_error() -> Callable[..., httpx.HTTPStatusError]:
    """Factory fixture that builds an :class:`httpx.HTTPStatusError`.

    Encapsulates the ``request``/``response``/``exception`` construction
    trio used throughout the core test suite so a single call yields a
    fully-wired ``HTTPStatusError`` ready for assertions.
    """

    def _factory(
        status: int,
        method: str = "GET",
        msg: str = "boom",
        url: str = "http://example.com",
    ) -> httpx.HTTPStatusError:
        request = httpx.Request(method, url)
        response = httpx.Response(status, request=request)
        return httpx.HTTPStatusError(msg, request=request, response=response)

    return _factory
