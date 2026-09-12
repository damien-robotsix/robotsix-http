"""Tests for the SSRF-guard transport and never-raises request wrapper."""

from __future__ import annotations

import asyncio
import socket

import httpcore
import httpx
import pytest

from robotsix_http import (
    HttpResult,
    SSRFError,
    SSRFGuardTransport,
    guarded_async_client,
    safe_http_request,
    validate_url,
)
from robotsix_http.safety import (
    _check_hostname_allowlist,
    _host_is_private,
    _ip_is_blocked,
    _SSRFGuardBackend,
    _validate_url_scheme,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeStream(httpcore.AsyncNetworkStream):
    async def read(self, *args: object, **kwargs: object) -> bytes:
        return b""

    async def write(self, *args: object, **kwargs: object) -> None:
        return None

    async def aclose(self) -> None:
        return None

    async def start_tls(self, *args: object, **kwargs: object) -> httpcore.AsyncNetworkStream:
        return self

    def get_extra_info(self, *args: object, **kwargs: object) -> object:
        return None


class _FakeBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self.connected: list[tuple[str, int]] = []
        self.slept: list[float] = []

    async def connect_tcp(
        self, host: str, port: int, **kwargs: object
    ) -> httpcore.AsyncNetworkStream:
        self.connected.append((host, port))
        return _FakeStream()

    async def connect_unix_socket(
        self, *args: object, **kwargs: object
    ) -> httpcore.AsyncNetworkStream:
        raise AssertionError("connect_unix_socket should never be delegated")

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


def _gai(ip: str) -> object:
    async def fake(host: str, port: object, **kwargs: object) -> list[object]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    return fake


# ---------------------------------------------------------------------------
# IP / host / scheme / allowlist checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.0.1",
        "0.0.0.0",  # noqa: S104 — unspecified address is a block target, not a bind
        "::1",
        "fc00::1",
        "224.0.0.1",
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
    ],
)
def test_ip_is_blocked_true(ip: str) -> None:
    assert _ip_is_blocked(ip) is True


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34"])
def test_ip_is_blocked_false(ip: str) -> None:
    assert _ip_is_blocked(ip) is False


def test_host_is_private_literal() -> None:
    assert _host_is_private("127.0.0.1") is True
    assert _host_is_private("8.8.8.8") is False


def test_host_is_private_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> object:
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert _host_is_private("nonexistent.invalid") is True


def test_host_is_private_resolves_private(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0))],
    )
    assert _host_is_private("internal.test") is True


def test_check_hostname_allowlist() -> None:
    assert _check_hostname_allowlist("anything.com", None) is True
    assert _check_hostname_allowlist("anything.com", []) is True
    assert _check_hostname_allowlist("example.com", ["example.com"]) is True
    assert _check_hostname_allowlist("api.example.com", ["example.com"]) is True
    assert _check_hostname_allowlist("example.com.", ["example.com"]) is True
    assert _check_hostname_allowlist("EXAMPLE.com", [".example.com"]) is True
    assert _check_hostname_allowlist("notexample.com", ["example.com"]) is False
    assert _check_hostname_allowlist("evil.com", ["example.com"]) is False


def test_validate_url_scheme() -> None:
    _validate_url_scheme("http://example.com")
    _validate_url_scheme("https://example.com")
    with pytest.raises(SSRFError):
        _validate_url_scheme("ftp://example.com")
    with pytest.raises(SSRFError):
        _validate_url_scheme("file:///etc/passwd")


def test_validate_url() -> None:
    validate_url("http://8.8.8.8")
    validate_url("https://8.8.8.8", allowlist=None)
    with pytest.raises(SSRFError):
        validate_url("ftp://8.8.8.8")
    with pytest.raises(SSRFError):
        validate_url("http://127.0.0.1")
    with pytest.raises(SSRFError):
        validate_url("http://8.8.8.8", allowlist=["example.com"])
    with pytest.raises(SSRFError):
        validate_url("http://")


# ---------------------------------------------------------------------------
# SSRF-guard backend
# ---------------------------------------------------------------------------


async def test_guard_backend_blocks_literal_private_ip() -> None:
    backend = _FakeBackend()
    guard = _SSRFGuardBackend(backend)
    with pytest.raises(SSRFError):
        await guard.connect_tcp("127.0.0.1", 80)
    assert backend.connected == []


async def test_guard_backend_allows_literal_public_ip() -> None:
    backend = _FakeBackend()
    guard = _SSRFGuardBackend(backend)
    stream = await guard.connect_tcp("8.8.8.8", 443)
    assert isinstance(stream, _FakeStream)
    assert backend.connected == [("8.8.8.8", 443)]


async def test_guard_backend_blocks_hostname_resolving_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _FakeBackend()
    guard = _SSRFGuardBackend(backend)
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", _gai("10.0.0.5"))
    with pytest.raises(SSRFError):
        await guard.connect_tcp("evil.test", 80)
    assert backend.connected == []


async def test_guard_backend_allows_hostname_resolving_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _FakeBackend()
    guard = _SSRFGuardBackend(backend)
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", _gai("93.184.216.34"))
    await guard.connect_tcp("good.test", 80)
    # The connection is pinned to the validated resolved IP, not the hostname.
    assert backend.connected == [("93.184.216.34", 80)]


async def test_guard_backend_resolution_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _FakeBackend()
    guard = _SSRFGuardBackend(backend)
    loop = asyncio.get_running_loop()

    async def boom(*args: object, **kwargs: object) -> list[object]:
        raise socket.gaierror("no such host")

    monkeypatch.setattr(loop, "getaddrinfo", boom)
    with pytest.raises(SSRFError):
        await guard.connect_tcp("nowhere.invalid", 80)


async def test_guard_backend_allowlist_rejects_before_dns() -> None:
    backend = _FakeBackend()
    guard = _SSRFGuardBackend(backend, allowlist=["example.com"])
    with pytest.raises(SSRFError):
        await guard.connect_tcp("other.com", 80)
    assert backend.connected == []


async def test_guard_backend_blocks_unix_socket() -> None:
    guard = _SSRFGuardBackend(_FakeBackend())
    with pytest.raises(SSRFError):
        await guard.connect_unix_socket("/tmp/socket")  # noqa: S108


async def test_guard_backend_sleep_delegates() -> None:
    backend = _FakeBackend()
    guard = _SSRFGuardBackend(backend)
    await guard.sleep(0.0)
    assert backend.slept == [0.0]


# ---------------------------------------------------------------------------
# Transport / client factory
# ---------------------------------------------------------------------------


async def test_guarded_transport_wraps_backend() -> None:
    async with SSRFGuardTransport(allowlist=["example.com"]) as transport:
        assert isinstance(transport._pool._network_backend, _SSRFGuardBackend)


async def test_guarded_async_client_uses_guard_transport() -> None:
    client = guarded_async_client(allowlist=["example.com"])
    try:
        transport = client._transport
        assert isinstance(transport, SSRFGuardTransport)
        assert isinstance(transport._pool._network_backend, _SSRFGuardBackend)
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# safe_http_request / HttpResult
# ---------------------------------------------------------------------------


async def test_safe_http_request_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, text="hello", headers={"x-test": "1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await safe_http_request(client, "GET", "http://example.com")

    assert isinstance(result, HttpResult)
    assert result.ok is True
    assert result.status_code == 200
    assert result.text == "hello"
    assert result.headers["x-test"] == "1"
    assert result.error is None
    assert result.response is not None


async def test_safe_http_request_error_status_does_not_raise() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await safe_http_request(client, "GET", "http://example.com")

    assert result.ok is False
    assert result.status_code == 503
    assert result.error is None
    assert result.response is not None


async def test_safe_http_request_captures_exception() -> None:
    class _Boom:
        async def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
            raise httpx.ConnectError("boom")

    result = await safe_http_request(_Boom(), "GET", "http://example.com")
    assert result.ok is False
    assert result.status_code is None
    assert result.error is not None
    assert "ConnectError" in result.error


async def test_safe_http_request_validation_blocks_bad_scheme() -> None:
    class _Track:
        def __init__(self) -> None:
            self.called = False

        async def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
            self.called = True
            return httpx.Response(200)

    client = _Track()
    result = await safe_http_request(client, "GET", "file:///etc/passwd")
    assert result.ok is False
    assert result.error is not None
    assert "SSRFError" in result.error
    assert client.called is False


async def test_safe_http_request_validation_blocks_allowlist() -> None:
    class _Track:
        def __init__(self) -> None:
            self.called = False

        async def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
            self.called = True
            return httpx.Response(200)

    client = _Track()
    result = await safe_http_request(client, "GET", "http://evil.com", allowlist=["example.com"])
    assert result.ok is False
    assert result.error is not None
    assert "SSRFError" in result.error
    assert client.called is False


async def test_safe_http_request_validate_false_skips_checks() -> None:
    class _Track:
        def __init__(self) -> None:
            self.called = False

        async def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
            self.called = True
            return httpx.Response(200, text="ok", request=httpx.Request(method, url))

    client = _Track()
    result = await safe_http_request(client, "GET", "file:///x", validate=False)
    assert client.called is True
    assert result.ok is True
    assert result.text == "ok"
