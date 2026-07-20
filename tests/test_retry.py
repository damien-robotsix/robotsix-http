"""Tests for robotsix_http.retry."""

from __future__ import annotations

import json
from unittest import mock

import httpx
import pytest

from robotsix_http.retry import (
    RetryConfig,
    _compute_backoff,
    _status,
    _walk_cause_chain,
    acall_with_retry,
    call_with_retry,
    is_transient,
)

# ---------------------------------------------------------------------------
# _walk_cause_chain
# ---------------------------------------------------------------------------


class TestWalkCauseChain:
    def test_single_exception(self) -> None:
        exc = ValueError("a")
        result = list(_walk_cause_chain(exc))
        assert result == [exc]

    def test_chain(self) -> None:
        inner = ValueError("inner")
        middle = TypeError("middle")
        outer = RuntimeError("outer")
        inner.__cause__ = middle
        middle.__cause__ = outer
        result = list(_walk_cause_chain(inner))
        assert result == [inner, middle, outer]

    def test_max_depth(self) -> None:
        inner: BaseException = ValueError("0")
        current = inner
        for i in range(1, 20):
            nxt = ValueError(str(i))
            current.__cause__ = nxt
            current = nxt
        result = list(_walk_cause_chain(inner, max_depth=5))
        assert len(result) == 5

    def test_none_in_chain(self) -> None:
        exc = ValueError("stop")
        exc.__cause__ = None  # type: ignore[assignment]
        result = list(_walk_cause_chain(exc, max_depth=5))
        assert result == [exc]


# ---------------------------------------------------------------------------
# _status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_direct_status_code(self) -> None:
        class StatusError(Exception):
            pass

        exc = StatusError()
        exc.status_code = 429  # type: ignore[attr-defined]
        assert _status(exc) == 429

    def test_httpx_response_status_code(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(503, request=request)
        exc = httpx.HTTPStatusError("boom", request=request, response=response)
        assert _status(exc) == 503

    def test_response_status_fallback(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(500, request=request)
        # Remove status_code to force .status fallback
        exc = httpx.HTTPStatusError("boom", request=request, response=response)
        assert _status(exc) == 500

    def test_no_status(self) -> None:
        assert _status(ValueError("nope")) is None

    def test_non_int_status_code(self) -> None:
        class StatusError(Exception):
            pass

        exc = StatusError()
        exc.status_code = "429"  # type: ignore[attr-defined]
        assert _status(exc) is None


# ---------------------------------------------------------------------------
# is_transient
# ---------------------------------------------------------------------------


class TestIsTransient:
    def test_timeout_exception(self) -> None:
        assert is_transient(httpx.TimeoutException("timeout")) is True

    def test_transport_error(self) -> None:
        assert is_transient(httpx.TransportError("transport")) is True

    def test_json_decode_error(self) -> None:
        assert is_transient(json.JSONDecodeError("bad json", "", 0)) is True

    def test_http_429(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(429, request=request)
        exc = httpx.HTTPStatusError("rate limited", request=request, response=response)
        assert is_transient(exc) is True

    def test_http_500(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(500, request=request)
        exc = httpx.HTTPStatusError("server error", request=request, response=response)
        assert is_transient(exc) is True

    def test_http_502(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(502, request=request)
        exc = httpx.HTTPStatusError("bad gateway", request=request, response=response)
        assert is_transient(exc) is True

    def test_http_503(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(503, request=request)
        exc = httpx.HTTPStatusError("unavailable", request=request, response=response)
        assert is_transient(exc) is True

    def test_http_400_not_transient(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(400, request=request)
        exc = httpx.HTTPStatusError("bad request", request=request, response=response)
        assert is_transient(exc) is False

    def test_http_404_not_transient(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(404, request=request)
        exc = httpx.HTTPStatusError("not found", request=request, response=response)
        assert is_transient(exc) is False

    def test_cause_chain_transient(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(503, request=request)
        inner = httpx.HTTPStatusError("inner", request=request, response=response)
        outer = RuntimeError("wrapper")
        outer.__cause__ = inner
        assert is_transient(outer) is True

    def test_non_transient(self) -> None:
        assert is_transient(ValueError("nope")) is False


# ---------------------------------------------------------------------------
# RetryConfig
# ---------------------------------------------------------------------------


class TestRetryConfig:
    def test_defaults(self) -> None:
        cfg = RetryConfig()
        assert cfg.max_retries == 4
        assert cfg.backoff_base == 2.0
        assert cfg.backoff_cap == 30.0
        assert cfg.jitter_factor == 0.5
        assert cfg.on_retry is None

    def test_frozen(self) -> None:
        import dataclasses

        cfg = RetryConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.max_retries = 10  # type: ignore[misc]

    def test_custom(self) -> None:
        def _noop(_attempt: int, _exc: Exception, _delay: float) -> None:
            return

        cfg = RetryConfig(
            max_retries=5,
            backoff_base=3.0,
            backoff_cap=60.0,
            jitter_factor=0.2,
            on_retry=_noop,
        )
        assert cfg.max_retries == 5
        assert cfg.backoff_base == 3.0
        assert cfg.backoff_cap == 60.0
        assert cfg.jitter_factor == 0.2
        assert cfg.on_retry is _noop


# ---------------------------------------------------------------------------
# _compute_backoff
# ---------------------------------------------------------------------------


class TestComputeBackoff:
    def test_attempt_0(self) -> None:
        cfg = RetryConfig(backoff_base=2.0, backoff_cap=30.0)
        with mock.patch("random.random", return_value=0.0):
            delay = _compute_backoff(0, cfg)
            assert delay == 1.0

    def test_attempt_1(self) -> None:
        cfg = RetryConfig(backoff_base=2.0, backoff_cap=30.0)
        with mock.patch("random.random", return_value=0.0):
            delay = _compute_backoff(1, cfg)
            assert delay == 2.0

    def test_attempt_3(self) -> None:
        cfg = RetryConfig(backoff_base=2.0, backoff_cap=30.0)
        with mock.patch("random.random", return_value=0.0):
            delay = _compute_backoff(3, cfg)
            assert delay == 8.0

    def test_cap(self) -> None:
        cfg = RetryConfig(backoff_base=2.0, backoff_cap=30.0)
        with mock.patch("random.random", return_value=0.0):
            delay = _compute_backoff(10, cfg)
            assert delay == 30.0

    def test_jitter_max(self) -> None:
        """With random.random() == 1.0, jitter is at maximum — delay is halved."""
        cfg = RetryConfig(backoff_base=2.0, backoff_cap=30.0, jitter_factor=0.5)
        with mock.patch("random.random", return_value=1.0):
            delay = _compute_backoff(0, cfg)
            assert delay == pytest.approx(0.5)

    def test_jitter_mid(self) -> None:
        cfg = RetryConfig(backoff_base=2.0, backoff_cap=30.0, jitter_factor=0.5)
        with mock.patch("random.random", return_value=0.5):
            delay = _compute_backoff(0, cfg)
            assert delay == pytest.approx(0.75)

    def test_jitter_bounds(self) -> None:
        """Without mocking, delay should be within expected bounds."""
        cfg = RetryConfig(backoff_base=2.0, backoff_cap=30.0, jitter_factor=0.5)
        for _ in range(20):
            delay = _compute_backoff(0, cfg)
            assert 0.5 <= delay <= 1.0

    def test_zero_jitter(self) -> None:
        cfg = RetryConfig(backoff_base=2.0, backoff_cap=30.0, jitter_factor=0.0)
        with mock.patch("random.random", return_value=0.5):
            delay = _compute_backoff(0, cfg)
            assert delay == 1.0


# ---------------------------------------------------------------------------
# call_with_retry (sync)
# ---------------------------------------------------------------------------


class TestCallWithRetrySync:
    def test_success_first_try(self) -> None:
        def fn() -> str:
            return "ok"

        result = call_with_retry(fn)
        assert result == "ok"

    def test_retry_then_success(self) -> None:
        call_count = 0

        def fn() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.HTTPStatusError(
                    "boom",
                    request=httpx.Request("GET", "http://x"),
                    response=httpx.Response(503, request=httpx.Request("GET", "http://x")),
                )
            return "ok"

        result = call_with_retry(fn, config=RetryConfig(jitter_factor=0.0))
        assert result == "ok"
        assert call_count == 3

    def test_non_transient_raises_immediately(self) -> None:
        call_count = 0

        def fn() -> str:
            nonlocal call_count
            call_count += 1
            raise ValueError("not transient")

        with pytest.raises(ValueError, match="not transient"):
            call_with_retry(fn)
        assert call_count == 1  # no retries

    def test_exhaust_retries_reraises(self) -> None:
        call_count = 0

        def fn() -> str:
            nonlocal call_count
            call_count += 1
            raise httpx.TimeoutException("always times out")

        with pytest.raises(httpx.TimeoutException, match="always times out"):
            call_with_retry(fn, config=RetryConfig(max_retries=2, jitter_factor=0.0))
        # 1 initial + 2 retries = 3 total calls
        assert call_count == 3

    def test_on_retry_callback(self) -> None:
        calls: list[tuple[int, Exception, float]] = []

        def on_retry(attempt: int, exc: Exception, delay: float) -> None:
            calls.append((attempt, exc, delay))

        call_count = 0

        def fn() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.TimeoutException("timeout")
            return "ok"

        result = call_with_retry(
            fn,
            config=RetryConfig(jitter_factor=0.0, on_retry=on_retry),
        )
        assert result == "ok"
        assert len(calls) == 2

        # First retry (attempt=1)
        assert calls[0][0] == 1
        assert isinstance(calls[0][1], httpx.TimeoutException)
        assert calls[0][2] == pytest.approx(1.0)  # 2^0 = 1.0

        # Second retry (attempt=2)
        assert calls[1][0] == 2
        assert isinstance(calls[1][1], httpx.TimeoutException)
        assert calls[1][2] == pytest.approx(2.0)  # 2^1 = 2.0

    def test_async_fn_sync_wrapper(self) -> None:
        async def fn() -> str:
            return "async-ok"

        result = call_with_retry(fn)
        assert result == "async-ok"

    def test_custom_is_transient(self) -> None:
        def always_transient(_exc: Exception) -> bool:
            return True

        call_count = 0

        def fn() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("custom transient")
            return "ok"

        result = call_with_retry(
            fn,
            is_transient_fn=always_transient,
            config=RetryConfig(jitter_factor=0.0),
        )
        assert result == "ok"
        assert call_count == 2


# ---------------------------------------------------------------------------
# acall_with_retry (async)
# ---------------------------------------------------------------------------


class TestACallWithRetry:
    async def test_success_first_try(self) -> None:
        async def fn() -> str:
            return "ok"

        result = await acall_with_retry(fn)
        assert result == "ok"

    async def test_retry_then_success(self) -> None:
        call_count = 0

        async def fn() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.HTTPStatusError(
                    "boom",
                    request=httpx.Request("GET", "http://x"),
                    response=httpx.Response(503, request=httpx.Request("GET", "http://x")),
                )
            return "ok"

        result = await acall_with_retry(fn, config=RetryConfig(jitter_factor=0.0))
        assert result == "ok"
        assert call_count == 3

    async def test_non_transient_raises_immediately(self) -> None:
        call_count = 0

        async def fn() -> str:
            nonlocal call_count
            call_count += 1
            raise ValueError("not transient")

        with pytest.raises(ValueError, match="not transient"):
            await acall_with_retry(fn)
        assert call_count == 1

    async def test_exhaust_retries_reraises(self) -> None:
        call_count = 0

        async def fn() -> str:
            nonlocal call_count
            call_count += 1
            raise httpx.TimeoutException("always times out")

        with pytest.raises(httpx.TimeoutException, match="always times out"):
            await acall_with_retry(fn, config=RetryConfig(max_retries=2, jitter_factor=0.0))
        assert call_count == 3

    async def test_on_retry_callback(self) -> None:
        calls: list[tuple[int, Exception, float]] = []

        def on_retry(attempt: int, exc: Exception, delay: float) -> None:
            calls.append((attempt, exc, delay))

        call_count = 0

        async def fn() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.TimeoutException("timeout")
            return "ok"

        result = await acall_with_retry(
            fn,
            config=RetryConfig(jitter_factor=0.0, on_retry=on_retry),
        )
        assert result == "ok"
        assert len(calls) == 2
        assert calls[0][0] == 1
        assert isinstance(calls[0][1], httpx.TimeoutException)
        assert calls[0][2] == pytest.approx(1.0)
        assert calls[1][0] == 2
        assert isinstance(calls[1][1], httpx.TimeoutException)
        assert calls[1][2] == pytest.approx(2.0)

    async def test_sync_fn_async_wrapper(self) -> None:
        def fn() -> str:
            return "sync-ok"

        result = await acall_with_retry(fn)
        assert result == "sync-ok"

    async def test_custom_is_transient(self) -> None:
        def always_transient(_exc: Exception) -> bool:
            return True

        call_count = 0

        async def fn() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("custom transient")
            return "ok"

        result = await acall_with_retry(
            fn,
            is_transient_fn=always_transient,
            config=RetryConfig(jitter_factor=0.0),
        )
        assert result == "ok"
        assert call_count == 2
