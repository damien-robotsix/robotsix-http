"""Tests for the pure-ASGI correlation-ID middleware.

The middleware is exercised directly at the ASGI level (rather than via
``fastapi.testclient.TestClient``) so assertions about ``structlog``
contextvars binding/unbinding run in the same context as the middleware,
matching the httpx-free style of ``test_fastapi.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.types import Message, Receive, Scope, Send
from structlog.contextvars import get_contextvars

from robotsix_http.fastapi import CorrelationIdMiddleware, create_correlation_id_middleware


def _http_scope(headers: list[tuple[bytes, bytes]] | None = None) -> Scope:
    return {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers or [],
    }


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_app(recorder: dict[str, Any]) -> Any:
    """Return an ASGI app that records the contextvars visible to the endpoint."""

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        recorder["seen"] = dict(get_contextvars())
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    return app


async def _drive(middleware: CorrelationIdMiddleware, scope: Scope) -> list[Message]:
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await middleware(scope, _receive, send)
    return sent


def _response_headers(sent: list[Message]) -> dict[str, str]:
    start = next(m for m in sent if m["type"] == "http.response.start")
    return {k.decode().lower(): v.decode() for k, v in start["headers"]}


async def test_generates_id_when_absent_and_echoes_it() -> None:
    recorder: dict[str, Any] = {}
    middleware = CorrelationIdMiddleware(_make_app(recorder))

    sent = await _drive(middleware, _http_scope())

    correlation_id = recorder["seen"]["correlation_id"]
    assert correlation_id
    assert _response_headers(sent)["x-request-id"] == correlation_id
    # Contextvars are unbound once the request completes.
    assert "correlation_id" not in get_contextvars()


async def test_reuses_inbound_header() -> None:
    recorder: dict[str, Any] = {}
    middleware = CorrelationIdMiddleware(_make_app(recorder))

    sent = await _drive(middleware, _http_scope([(b"x-request-id", b"abc-123")]))

    assert recorder["seen"]["correlation_id"] == "abc-123"
    assert _response_headers(sent)["x-request-id"] == "abc-123"


async def test_custom_header_and_multiple_context_fields() -> None:
    recorder: dict[str, Any] = {}
    middleware = CorrelationIdMiddleware(
        _make_app(recorder),
        header_name=["X-Correlation-ID", "X-Request-ID"],
        context_field=["correlation_id", "request_id"],
    )

    sent = await _drive(middleware, _http_scope([(b"x-correlation-id", b"corr-9")]))

    assert recorder["seen"]["correlation_id"] == "corr-9"
    assert recorder["seen"]["request_id"] == "corr-9"
    # Echoed on the primary (first) header name.
    assert _response_headers(sent)["x-correlation-id"] == "corr-9"
    assert "correlation_id" not in get_contextvars()
    assert "request_id" not in get_contextvars()


async def test_falls_back_to_second_candidate_header() -> None:
    recorder: dict[str, Any] = {}
    middleware = CorrelationIdMiddleware(
        _make_app(recorder),
        header_name=["X-Correlation-ID", "X-Request-ID"],
    )

    await _drive(middleware, _http_scope([(b"x-request-id", b"rid-7")]))

    assert recorder["seen"]["correlation_id"] == "rid-7"


async def test_custom_generator() -> None:
    recorder: dict[str, Any] = {}
    middleware = CorrelationIdMiddleware(_make_app(recorder), generator=lambda: "fixed-id")

    sent = await _drive(middleware, _http_scope())

    assert recorder["seen"]["correlation_id"] == "fixed-id"
    assert _response_headers(sent)["x-request-id"] == "fixed-id"


async def test_log_requests_emits_start_and_end() -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    class _FakeLogger:
        def info(self, event: str, **kwargs: Any) -> None:
            events.append((event, kwargs))

    middleware = CorrelationIdMiddleware(_make_app({}), log_requests=True, logger=_FakeLogger())

    await _drive(middleware, _http_scope())

    names = [event for event, _ in events]
    assert names == ["request.start", "request.end"]
    assert "duration_ms" in events[1][1]
    assert isinstance(events[1][1]["duration_ms"], float)


async def test_unbinds_even_when_app_raises() -> None:
    async def failing_app(scope: Scope, receive: Receive, send: Send) -> None:
        raise RuntimeError("boom")

    middleware = CorrelationIdMiddleware(failing_app)

    async def send(message: Message) -> None:
        return None

    with pytest.raises(RuntimeError, match="boom"):
        await middleware(_http_scope(), _receive, send)

    assert "correlation_id" not in get_contextvars()


async def test_non_http_scope_passes_through() -> None:
    calls: list[Scope] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        calls.append(scope)

    middleware = CorrelationIdMiddleware(app)

    async def send(message: Message) -> None:
        return None

    await middleware({"type": "lifespan"}, _receive, send)

    assert calls == [{"type": "lifespan"}]
    assert "correlation_id" not in get_contextvars()


def test_empty_context_field_rejected() -> None:
    with pytest.raises(ValueError, match="at least one"):
        CorrelationIdMiddleware(_make_app({}), context_field=[])


async def test_create_correlation_id_middleware_registers_on_app() -> None:
    from fastapi import FastAPI

    app = FastAPI()
    create_correlation_id_middleware(app, header_name="X-Trace-ID")

    assert any(m.cls is CorrelationIdMiddleware for m in app.user_middleware)
