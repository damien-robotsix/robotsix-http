"""URL/IP-safety layer for outbound HTTP.

This module ports the SSRF guard and the never-raises request wrapper that
previously lived only in ``robotsix-chat`` (``common/http_fetch.py`` and
``common/http.py``) into the shared library, so every consumer of
:class:`robotsix_http.client.RetryClient` — and of :mod:`httpx` in general —
can obtain scheme, hostname-allowlist, and private-IP validation by default.

Two layers are provided:

* **Transport guard** — :class:`SSRFGuardTransport` (built by
  :func:`guarded_async_client`) wraps an :class:`httpcore.AsyncNetworkBackend`
  and validates the *resolved* address of every outbound TCP connection
  before it is established.  Because resolution and validation happen inside
  the backend and the connection is pinned to a validated address, a DNS
  answer cannot change between the safety check and the connect
  (DNS-rebinding).
* **Standalone helpers** — :func:`validate_url` performs an eager
  scheme / allowlist / private-IP check on a URL string, and
  :func:`safe_http_request` wraps a request so it never raises, returning an
  :class:`HttpResult` describing the outcome instead.
"""

from __future__ import annotations

import asyncio
import dataclasses
import ipaddress
import logging
import socket
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpcore
import httpx

logger = logging.getLogger(__name__)

#: URL schemes permitted for outbound requests by default.
ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class SSRFError(ValueError):
    """Raised when a request target fails SSRF-safety validation.

    Subclasses :class:`ValueError` so callers that already treat malformed
    targets as value errors keep working.
    """


# ---------------------------------------------------------------------------
# Private-range / scheme / allowlist checks
# ---------------------------------------------------------------------------


def _ip_is_blocked(ip: str | _IPAddress) -> bool:
    """Return ``True`` when *ip* is not a routable public address.

    Blocks loopback, private (RFC 1918 / ULA), link-local, multicast,
    reserved, and unspecified ranges.  IPv4-mapped IPv6 addresses
    (``::ffff:a.b.c.d``) are unwrapped so an attacker cannot smuggle a
    private IPv4 target through an IPv6 literal.
    """
    addr: _IPAddress = ipaddress.ip_address(ip) if isinstance(ip, str) else ip
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _host_is_private(host: str) -> bool:
    """Return ``True`` when *host* is, or resolves to, a blocked address.

    A literal IP is checked directly.  A hostname is resolved with
    :func:`socket.getaddrinfo` (a **blocking** call — do not use on the
    async request path; the transport guard resolves without blocking) and
    is considered private when *any* resolved address is blocked, so a name
    that returns both a public and a private record is still rejected.
    """
    try:
        return _ip_is_blocked(host)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        # Unresolvable — treat as unsafe rather than let the request proceed.
        return True
    return any(_ip_is_blocked(str(info[4][0])) for info in infos)


def _check_hostname_allowlist(host: str, allowlist: Sequence[str] | None) -> bool:
    """Return ``True`` when *host* is permitted by *allowlist*.

    An empty or ``None`` allowlist permits every host.  Matching is
    case-insensitive and covers exact matches plus subdomains of an allowed
    domain (``api.example.com`` matches an ``example.com`` entry).
    """
    if not allowlist:
        return True
    candidate = host.lower().rstrip(".")
    for raw in allowlist:
        entry = raw.lower().strip().lstrip(".").rstrip(".")
        if not entry:
            continue
        if candidate == entry or candidate.endswith("." + entry):
            return True
    return False


def _validate_url_scheme(url: str, allowed_schemes: Iterable[str] = ALLOWED_SCHEMES) -> None:
    """Raise :class:`SSRFError` when *url*'s scheme is not allowed."""
    allowed = {s.lower() for s in allowed_schemes}
    scheme = urlsplit(url).scheme.lower()
    if scheme not in allowed:
        raise SSRFError(f"URL scheme {scheme!r} is not permitted (allowed: {sorted(allowed)})")


def validate_url(
    url: str,
    *,
    allowlist: Sequence[str] | None = None,
    allowed_schemes: Iterable[str] = ALLOWED_SCHEMES,
) -> None:
    """Eagerly validate *url* against scheme, allowlist, and private-IP rules.

    Raises :class:`SSRFError` on the first failing check.  This performs a
    blocking DNS lookup for hostname targets; the authoritative,
    non-blocking, DNS-rebinding-safe enforcement is the transport guard
    installed by :func:`guarded_async_client`.
    """
    _validate_url_scheme(url, allowed_schemes)
    host = urlsplit(url).hostname
    if not host:
        raise SSRFError(f"URL {url!r} has no host component")
    if not _check_hostname_allowlist(host, allowlist):
        raise SSRFError(f"Host {host!r} is not in the allowlist")
    if _host_is_private(host):
        raise SSRFError(f"Host {host!r} resolves to a blocked (non-public) address")


# ---------------------------------------------------------------------------
# SSRF-guard network backend + transport
# ---------------------------------------------------------------------------


class _SSRFGuardBackend(httpcore.AsyncNetworkBackend):
    """Wrap a network backend, validating every TCP target before connecting.

    Delegates all connections to *backend* but first resolves the requested
    host, rejects it if it is not on *allowlist* or resolves to a blocked
    address, and pins the connection to a validated IP so the address cannot
    change between validation and connect.
    """

    def __init__(
        self,
        backend: httpcore.AsyncNetworkBackend,
        *,
        allowlist: Sequence[str] | None = None,
    ) -> None:
        self._backend = backend
        self._allowlist: tuple[str, ...] | None = tuple(allowlist) if allowlist else None

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        safe_ip = await self._resolve_and_validate(host)
        return await self._backend.connect_tcp(
            safe_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(self, *args: Any, **kwargs: Any) -> httpcore.AsyncNetworkStream:
        del args, kwargs  # explicitly unrouted: unix sockets are never permitted
        raise SSRFError("Unix-domain-socket connections are blocked by the SSRF guard")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)

    async def _resolve_and_validate(self, host: str) -> str:
        """Return a validated IP literal to connect to, or raise ``SSRFError``."""
        if self._allowlist is not None and not _check_hostname_allowlist(host, self._allowlist):
            raise SSRFError(f"Host {host!r} is not in the allowlist")

        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            # Host is already an IP literal — validate it directly.
            if _ip_is_blocked(host):
                raise SSRFError(f"Address {host!r} is a blocked (non-public) address")
            return host

        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise SSRFError(f"Could not resolve host {host!r}: {exc}") from exc

        addresses = [str(info[4][0]) for info in infos]
        if not addresses:
            raise SSRFError(f"Host {host!r} did not resolve to any address")
        for address in addresses:
            if _ip_is_blocked(address):
                raise SSRFError(f"Host {host!r} resolves to blocked address {address}")
        return addresses[0]


class SSRFGuardTransport(httpx.AsyncHTTPTransport):
    """An :class:`httpx.AsyncHTTPTransport` with an SSRF-guarded backend.

    Accepts the same keyword arguments as :class:`httpx.AsyncHTTPTransport`,
    plus *allowlist*: an optional sequence of permitted hostnames/domains.
    """

    def __init__(self, *, allowlist: Sequence[str] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._pool._network_backend = _SSRFGuardBackend(
            self._pool._network_backend, allowlist=allowlist
        )


def guarded_async_client(
    *,
    allowlist: Sequence[str] | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Build an :class:`httpx.AsyncClient` whose transport blocks SSRF targets.

    Parameters:
        allowlist: Optional sequence of permitted hostnames/domains.  When
            provided, any host not matching (exactly or as a subdomain) is
            rejected before connecting.
        **kwargs: Forwarded to :class:`httpx.AsyncClient` (e.g. ``timeout``,
            ``headers``, ``limits``).

    The caller owns the returned client's lifecycle and should close it
    (``async with`` or ``await client.aclose()``).
    """
    transport = SSRFGuardTransport(allowlist=allowlist)
    return httpx.AsyncClient(transport=transport, **kwargs)


# ---------------------------------------------------------------------------
# Never-raises request wrapper
# ---------------------------------------------------------------------------


class _AsyncRequester(Protocol):
    """Structural type for anything exposing an async ``request`` method.

    Satisfied by both :class:`httpx.AsyncClient` and
    :class:`robotsix_http.client.RetryClient`.
    """

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response: ...


@dataclasses.dataclass(frozen=True)
class HttpResult:
    """Outcome of a :func:`safe_http_request` call.

    Attributes:
        ok: ``True`` when a response was received and its status is not an
            error (< 400).
        url: The final request URL (post-redirect on success, else the
            requested URL).
        status_code: The HTTP status code, or ``None`` when no response was
            received.
        text: The decoded response body, or ``""`` when unavailable.
        headers: The response headers, or an empty mapping.
        error: A ``"<ExcType>: <message>"`` string when the request failed,
            else ``None``.
        response: The underlying :class:`httpx.Response`, or ``None`` on
            failure.
    """

    ok: bool
    url: str
    status_code: int | None = None
    text: str = ""
    headers: Mapping[str, str] = dataclasses.field(default_factory=dict)
    error: str | None = None
    response: httpx.Response | None = None


async def safe_http_request(
    client: _AsyncRequester,
    method: str,
    url: str,
    *,
    validate: bool = True,
    allowlist: Sequence[str] | None = None,
    allowed_schemes: Iterable[str] = ALLOWED_SCHEMES,
    **kwargs: Any,
) -> HttpResult:
    """Perform an HTTP request that never raises, returning an :class:`HttpResult`.

    Any exception — validation failure, connection error, timeout, or an
    error status surfaced by the client — is captured in
    :attr:`HttpResult.error` instead of propagating.

    Parameters:
        client: An object with an async ``request(method, url, **kwargs)``
            method (e.g. :class:`httpx.AsyncClient` or
            :class:`~robotsix_http.client.RetryClient`).  Use a client built
            by :func:`guarded_async_client` for private-IP protection.
        method: HTTP method.
        url: Target URL.
        validate: When ``True`` (default), the URL scheme and *allowlist*
            are checked before the request is issued.
        allowlist: Optional hostname/domain allowlist for the eager check.
        allowed_schemes: Permitted URL schemes for the eager check.
        **kwargs: Forwarded to the client's ``request`` method.
    """
    try:
        if validate:
            _validate_url_scheme(url, allowed_schemes)
            host = urlsplit(url).hostname
            if host is not None and not _check_hostname_allowlist(host, allowlist):
                raise SSRFError(f"Host {host!r} is not in the allowlist")
        response = await client.request(method, url, **kwargs)
        return HttpResult(
            ok=not response.is_error,
            url=str(response.url),
            status_code=response.status_code,
            text=response.text,
            headers=dict(response.headers),
            response=response,
        )
    except Exception as exc:  # never-raises contract: any failure becomes a result
        logger.debug("safe_http_request %s %s failed: %s", method, url, exc)
        return HttpResult(ok=False, url=url, error=f"{type(exc).__name__}: {exc}")
