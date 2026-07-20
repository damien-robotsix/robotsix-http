"""Tests for robotsix_http.client."""

from __future__ import annotations

import datetime
import email.utils

import httpx
import pytest

from robotsix_http.client import (
    ExternalAuthError,
    ExternalHTTPError,
    ExternalRateLimitError,
    ExternalServiceError,
    RetryClient,
    _is_retryable_for_method,
    _map_exception,
    _parse_retry_after,
)
from robotsix_http.retry import RetryConfig

# ---------------------------------------------------------------------------
# _parse_retry_after
# ---------------------------------------------------------------------------


class TestParseRetryAfter:
    def test_delta_seconds(self) -> None:
        assert _parse_retry_after("120") == 120.0

    def test_delta_seconds_with_whitespace(self) -> None:
        assert _parse_retry_after("  42  ") == 42.0

    def test_http_date(self) -> None:
        future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=30)
        header = email.utils.format_datetime(future)
        result = _parse_retry_after(header)
        assert result is not None
        # Should be roughly 30 seconds (allow a small clock skew window).
        assert 25.0 <= result <= 35.0

    def test_http_date_past(self) -> None:
        past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=10)
        header = email.utils.format_datetime(past)
        assert _parse_retry_after(header) == 0.0

    def test_empty_header(self) -> None:
        assert _parse_retry_after("") is None

    def test_garbage_header(self) -> None:
        assert _parse_retry_after("not-a-date") is None


# ---------------------------------------------------------------------------
# _is_retryable_for_method
# ---------------------------------------------------------------------------


class TestIsRetryableForMethod:
    def test_get_retries_on_503(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(503, request=request)
        exc = httpx.HTTPStatusError("boom", request=request, response=response)
        assert _is_retryable_for_method("GET", exc) is True

    def test_get_retries_on_429(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(429, request=request)
        exc = httpx.HTTPStatusError("boom", request=request, response=response)
        assert _is_retryable_for_method("GET", exc) is True

    def test_post_does_not_retry_on_503(self) -> None:
        request = httpx.Request("POST", "http://example.com")
        response = httpx.Response(503, request=request)
        exc = httpx.HTTPStatusError("boom", request=request, response=response)
        assert _is_retryable_for_method("POST", exc) is False

    def test_post_does_not_retry_on_429(self) -> None:
        request = httpx.Request("POST", "http://example.com")
        response = httpx.Response(429, request=request)
        exc = httpx.HTTPStatusError("boom", request=request, response=response)
        assert _is_retryable_for_method("POST", exc) is False

    def test_post_retries_on_transport_error(self) -> None:
        exc = httpx.TransportError("connection reset")
        assert _is_retryable_for_method("POST", exc) is True

    def test_post_retries_on_timeout(self) -> None:
        exc = httpx.TimeoutException("timeout")
        assert _is_retryable_for_method("POST", exc) is True

    def test_patch_does_not_retry_on_503(self) -> None:
        request = httpx.Request("PATCH", "http://example.com")
        response = httpx.Response(503, request=request)
        exc = httpx.HTTPStatusError("boom", request=request, response=response)
        assert _is_retryable_for_method("PATCH", exc) is False

    def test_delete_retries_on_503(self) -> None:
        request = httpx.Request("DELETE", "http://example.com")
        response = httpx.Response(503, request=request)
        exc = httpx.HTTPStatusError("boom", request=request, response=response)
        assert _is_retryable_for_method("DELETE", exc) is True

    def test_put_retries_on_503(self) -> None:
        request = httpx.Request("PUT", "http://example.com")
        response = httpx.Response(503, request=request)
        exc = httpx.HTTPStatusError("boom", request=request, response=response)
        assert _is_retryable_for_method("PUT", exc) is True

    def test_post_non_transient(self) -> None:
        assert _is_retryable_for_method("POST", ValueError("nope")) is False


# ---------------------------------------------------------------------------
# _map_exception
# ---------------------------------------------------------------------------


class TestMapException:
    def test_401_maps_to_auth_error(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(401, request=request)
        exc = httpx.HTTPStatusError("unauthorized", request=request, response=response)
        mapped = _map_exception(exc)
        assert isinstance(mapped, ExternalAuthError)
        assert mapped.status_code == 401
        assert mapped.response is response

    def test_403_maps_to_auth_error(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(403, request=request)
        exc = httpx.HTTPStatusError("forbidden", request=request, response=response)
        mapped = _map_exception(exc)
        assert isinstance(mapped, ExternalAuthError)
        assert mapped.status_code == 403

    def test_429_maps_to_rate_limit_error(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(429, request=request)
        exc = httpx.HTTPStatusError("rate limited", request=request, response=response)
        mapped = _map_exception(exc)
        assert isinstance(mapped, ExternalRateLimitError)
        assert mapped.status_code == 429

    def test_500_maps_to_service_error(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(500, request=request)
        exc = httpx.HTTPStatusError("internal error", request=request, response=response)
        mapped = _map_exception(exc)
        assert isinstance(mapped, ExternalServiceError)
        assert mapped.status_code == 500

    def test_502_maps_to_service_error(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(502, request=request)
        exc = httpx.HTTPStatusError("bad gateway", request=request, response=response)
        mapped = _map_exception(exc)
        assert isinstance(mapped, ExternalServiceError)
        assert mapped.status_code == 502

    def test_non_http_error_passes_through(self) -> None:
        exc = ValueError("not http")
        assert _map_exception(exc) is exc

    def test_400_not_mapped(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(400, request=request)
        exc = httpx.HTTPStatusError("bad request", request=request, response=response)
        mapped = _map_exception(exc)
        # 400 is not mapped — returned as-is.
        assert mapped is exc


# ---------------------------------------------------------------------------
# Helpers for integration tests
# ---------------------------------------------------------------------------


def _make_429_response(request: httpx.Request, *, retry_after: str = "") -> httpx.Response:
    headers = {"Retry-After": retry_after} if retry_after else {}
    return httpx.Response(429, headers=headers, request=request)


def _make_503_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(503, request=request)


# ---------------------------------------------------------------------------
# Retry-After delay override
# ---------------------------------------------------------------------------


class TestRetryAfterDelayOverride:
    async def test_retry_after_delta_seconds_overrides_backoff(self) -> None:
        """When a 429 carries Retry-After: 5, the delay should be 5 (capped if needed)."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_429_response(request, retry_after="5")
            return httpx.Response(200, request=request)

        delays: list[float] = []

        def on_retry(_attempt: int, _exc: Exception, delay: float) -> None:
            delays.append(delay)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            rc = RetryClient(
                client,
                config=RetryConfig(max_retries=2, jitter_factor=0.0, on_retry=on_retry),
            )
            response = await rc.get("http://example.com")
            assert response.status_code == 200
            assert call_count == 2

        assert len(delays) == 1
        assert delays[0] == 5.0

    async def test_retry_after_capped_to_backoff_cap(self) -> None:
        """Retry-After value exceeding backoff_cap is clamped."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_429_response(request, retry_after="999")
            return httpx.Response(200, request=request)

        delays: list[float] = []

        def on_retry(_attempt: int, _exc: Exception, delay: float) -> None:
            delays.append(delay)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            rc = RetryClient(
                client,
                config=RetryConfig(
                    max_retries=2, backoff_cap=10.0, jitter_factor=0.0, on_retry=on_retry
                ),
            )
            response = await rc.get("http://example.com")
            assert response.status_code == 200

        assert len(delays) == 1
        assert delays[0] == 10.0

    async def test_no_retry_after_header_uses_backoff(self) -> None:
        """429 without Retry-After header falls back to computed backoff."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_429_response(request)  # no Retry-After
            return httpx.Response(200, request=request)

        delays: list[float] = []

        def on_retry(_attempt: int, _exc: Exception, delay: float) -> None:
            delays.append(delay)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            rc = RetryClient(
                client,
                config=RetryConfig(max_retries=2, jitter_factor=0.0, on_retry=on_retry),
            )
            response = await rc.get("http://example.com")
            assert response.status_code == 200

        assert len(delays) == 1
        # Default backoff: 2^0 = 1.0 (with jitter_factor=0.0).
        assert delays[0] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Method-idempotency gate
# ---------------------------------------------------------------------------


class TestMethodIdempotencyGate:
    async def test_post_does_not_retry_on_429_response(self) -> None:
        """POST on a 429 response should NOT retry — the request may have succeeded."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return _make_429_response(request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            rc = RetryClient(client, config=RetryConfig(max_retries=3, jitter_factor=0.0))
            with pytest.raises(ExternalRateLimitError, match="429"):
                await rc.post("http://example.com")
            # Only one attempt — no retries because POST + HTTP response error.
            assert call_count == 1

    async def test_post_does_not_retry_on_503_response(self) -> None:
        """POST on a 503 response should NOT retry."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return _make_503_response(request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            rc = RetryClient(client, config=RetryConfig(max_retries=3, jitter_factor=0.0))
            with pytest.raises(ExternalServiceError, match="503"):
                await rc.post("http://example.com")
            assert call_count == 1

    async def test_post_retries_on_transport_error(self) -> None:
        """POST on a transport error SHOULD retry."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.TransportError("connection reset")
            return httpx.Response(200, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            rc = RetryClient(client, config=RetryConfig(max_retries=3, jitter_factor=0.0))
            response = await rc.post("http://example.com")
            assert response.status_code == 200
            assert call_count == 3

    async def test_get_retries_on_429_response(self) -> None:
        """GET on a 429 response SHOULD retry."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return _make_429_response(request)
            return httpx.Response(200, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            rc = RetryClient(client, config=RetryConfig(max_retries=3, jitter_factor=0.0))
            response = await rc.get("http://example.com")
            assert response.status_code == 200
            assert call_count == 3


# ---------------------------------------------------------------------------
# Typed exceptions after retries exhausted
# ---------------------------------------------------------------------------


class TestTypedExceptionsAfterExhaustion:
    async def test_401_raises_external_auth_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            rc = RetryClient(client, config=RetryConfig(max_retries=2, jitter_factor=0.0))
            with pytest.raises(ExternalAuthError) as exc_info:
                await rc.get("http://example.com")
            assert exc_info.value.status_code == 401
            assert exc_info.value.response.status_code == 401

    async def test_403_raises_external_auth_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            rc = RetryClient(client, config=RetryConfig(max_retries=2, jitter_factor=0.0))
            with pytest.raises(ExternalAuthError) as exc_info:
                await rc.get("http://example.com")
            assert exc_info.value.status_code == 403

    async def test_429_raises_external_rate_limit_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            rc = RetryClient(client, config=RetryConfig(max_retries=2, jitter_factor=0.0))
            with pytest.raises(ExternalRateLimitError) as exc_info:
                await rc.get("http://example.com")
            assert exc_info.value.status_code == 429

    async def test_500_raises_external_service_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            rc = RetryClient(client, config=RetryConfig(max_retries=2, jitter_factor=0.0))
            with pytest.raises(ExternalServiceError) as exc_info:
                await rc.get("http://example.com")
            assert exc_info.value.status_code == 500

    async def test_503_raises_external_service_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            rc = RetryClient(client, config=RetryConfig(max_retries=2, jitter_factor=0.0))
            with pytest.raises(ExternalServiceError) as exc_info:
                await rc.get("http://example.com")
            assert exc_info.value.status_code == 503

    async def test_non_http_error_reraises_original(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("always times out")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            rc = RetryClient(client, config=RetryConfig(max_retries=2, jitter_factor=0.0))
            with pytest.raises(httpx.TimeoutException, match="always times out"):
                await rc.get("http://example.com")


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


class TestRetryClientMisc:
    async def test_success_first_try(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={"ok": True}))
        ) as client:
            rc = RetryClient(client)
            response = await rc.get("http://example.com")
            assert response.status_code == 200
            assert response.json() == {"ok": True}

    async def test_per_call_config_override(self) -> None:
        """The per-call config overrides the instance default."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(503, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            # Instance default allows 4 retries, per-call overrides to 0.
            rc = RetryClient(client, config=RetryConfig(max_retries=4))
            with pytest.raises(ExternalServiceError):
                await rc.get(
                    "http://example.com",
                    config=RetryConfig(max_retries=0, jitter_factor=0.0),
                )
            # max_retries=0 → 1 total attempt, no retries.
            assert call_count == 1

    async def test_convenience_methods(self) -> None:
        """Smoke test that get/post/patch/delete all work."""
        for method_name in ("get", "post", "patch", "delete"):
            upper_method = method_name.upper()
            transport = httpx.MockTransport(
                lambda _r, m=upper_method: httpx.Response(200, json={"method": m})
            )
            async with httpx.AsyncClient(transport=transport) as client:
                rc = RetryClient(client)
                fn = getattr(rc, method_name)
                response = await fn("http://example.com")
                assert response.status_code == 200
                assert response.json() == {"method": method_name.upper()}


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    def test_external_http_error_is_base(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(500, request=request)
        exc = ExternalHTTPError("base", status_code=500, response=response)
        assert isinstance(exc, Exception)
        assert exc.status_code == 500
        assert exc.response is response

    def test_auth_error_inherits(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(401, request=request)
        exc = ExternalAuthError("auth", status_code=401, response=response)
        assert isinstance(exc, ExternalHTTPError)
        assert isinstance(exc, Exception)

    def test_rate_limit_error_inherits(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(429, request=request)
        exc = ExternalRateLimitError("rate", status_code=429, response=response)
        assert isinstance(exc, ExternalHTTPError)

    def test_service_error_inherits(self) -> None:
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(503, request=request)
        exc = ExternalServiceError("service", status_code=503, response=response)
        assert isinstance(exc, ExternalHTTPError)
