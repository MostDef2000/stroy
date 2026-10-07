"""SSRF-safe outbound HTTP fetch guard (R3 product URL import).

The server fetches owner-supplied URLs (product pages, preview images). A
malicious or compromised URL must never be able to reach the internal
network, cloud metadata services, or non-HTTP services. The guard is split
into two layers:

``pure_decision(url, resolved_ips, redirect_chain, *, port_policy)``
    The complete policy decision with NO I/O: scheme, userinfo, port and IP
    classification checks plus redirect-chain rules. Fully unit-testable.

``fetch_guarded(...)``
    The I/O wrapper: resolves DNS, runs ``pure_decision`` for every hop, and
    connects to one of the *validated* public IPs (IP-pinned request with
    ``Host`` header + TLS SNI preserved), enforcing body-size, content-type
    and time budgets. IP pinning closes the classic DNS-rebinding TOCTOU
    window: the address that was validated is the address that is dialed —
    the hostname is never re-resolved at connect time.

Deny reason codes (closed taxonomy, surfaced as 422 ``url_rejected``):
``invalid_scheme``, ``userinfo_rejected``, ``port_rejected``,
``private_address``, ``redirect_private_address``, ``too_many_redirects``,
``dns_failed``, ``timeout``, ``response_too_large``,
``unsupported_content_type``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal
from urllib.parse import SplitResult, urljoin, urlsplit

import httpx

__all__ = [
    "Allow",
    "Deny",
    "GuardError",
    "GuardedFetch",
    "MAX_REDIRECTS",
    "fetch_guarded",
    "pure_decision",
    "sniff_image_media_type",
]

MAX_REDIRECTS = 3
DEFAULT_CONNECT_TIMEOUT = 2.0
DEFAULT_TOTAL_TIMEOUT = 8.0
DEFAULT_MAX_BYTES = 5_000_000  # 5 MB hard cap on response body

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}

Resolver = Callable[[str], Awaitable[list[str]]]


class GuardError(Exception):
    """Raised when a guarded fetch violates the outbound policy.

    ``reason`` is one of the closed deny-reason codes and is surfaced by the
    API as ``422 {"code": "url_rejected", "reason": ...}``.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason


@dataclass(frozen=True)
class Allow:
    """Policy decision: the fetch may proceed."""


@dataclass(frozen=True)
class Deny:
    """Policy decision: the fetch is refused."""

    reason: str
    detail: str = ""


@dataclass(frozen=True)
class GuardedFetch:
    """Result of a successful guarded fetch."""

    url: str  # final URL (original form, after redirects)
    status: int
    content_type: str | None
    body: bytes
    redirect_chain: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure policy (no I/O)
# ---------------------------------------------------------------------------

# Only implicit (scheme default), 80 (http) and 443 (https) ports are allowed.
WebStandardPortPolicy = Literal["web_standard"]

_IMPLICIT_PORT = {"http": 80, "https": 443}
_ALLOWED_EXPLICIT_PORT = {"http": 80, "https": 443}


def _static_url_checks(
    url: str,
    *,
    port_policy: WebStandardPortPolicy = "web_standard",
) -> Deny | None:
    """Scheme/userinfo/port validation for a single URL. Pure."""
    if port_policy != "web_standard":
        raise ValueError(f"unknown port policy: {port_policy}")
    try:
        parts = urlsplit(url)
        explicit_port = parts.port  # may raise ValueError for malformed ports
    except ValueError:
        return Deny("port_rejected", f"malformed port in URL: {url!r}")
    if parts.scheme.lower() not in _IMPLICIT_PORT:
        return Deny("invalid_scheme", f"scheme must be http or https: {url!r}")
    if not parts.hostname:
        return Deny("invalid_scheme", f"URL has no host: {url!r}")
    if parts.username is not None or parts.password is not None:
        return Deny("userinfo_rejected", f"userinfo is not allowed: {url!r}")
    if (
        explicit_port is not None
        and explicit_port != _ALLOWED_EXPLICIT_PORT[parts.scheme.lower()]
    ):
        return Deny(
            "port_rejected",
            f"port {explicit_port} is not allowed for {parts.scheme}: {url!r}",
        )
    return None


def _ip_is_disallowed(ip_text: str) -> str | None:
    """Return a detail string when the address must never be dialed. Pure."""
    addr = ipaddress.ip_address(ip_text)
    # IPv4-mapped IPv6 (::ffff:10.0.0.1) must be judged by its IPv4 value.
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
        or addr.is_reserved
    ):
        return f"disallowed address: {addr}"
    return None


def pure_decision(
    url: str,
    resolved_ips: list[str],
    redirect_chain: list[str],
    *,
    port_policy: WebStandardPortPolicy = "web_standard",
) -> Allow | Deny:
    """Full policy decision for fetching ``url`` — performs no I/O.

    ``resolved_ips`` are ALL addresses (A + AAAA) the caller resolved for
    ``url``; the fetch is denied if ANY of them is disallowed. ``redirect_chain``
    holds the already-followed redirect URLs (oldest first): when non-empty,
    a disallowed target address is reported as ``redirect_private_address``
    (public→private redirect) instead of ``private_address``. Every hop in
    the chain is re-checked with the same static rules; more than
    ``MAX_REDIRECTS`` hops is ``too_many_redirects``.
    """
    if len(redirect_chain) > MAX_REDIRECTS:
        return Deny(
            "too_many_redirects",
            f"redirect chain exceeds {MAX_REDIRECTS} hops",
        )
    for hop in redirect_chain:
        deny = _static_url_checks(hop, port_policy=port_policy)
        if deny is not None:
            return deny
    deny = _static_url_checks(url, port_policy=port_policy)
    if deny is not None:
        return deny

    if not resolved_ips:
        return Deny("dns_failed", f"no addresses resolved for: {url!r}")
    for ip_text in resolved_ips:
        try:
            detail = _ip_is_disallowed(ip_text)
        except ValueError:
            detail = f"unparseable resolved address: {ip_text!r}"
        if detail is not None:
            reason = (
                "redirect_private_address" if redirect_chain else "private_address"
            )
            return Deny(reason, detail)
    return Allow()


# ---------------------------------------------------------------------------
# Guarded fetch (I/O)
# ---------------------------------------------------------------------------


async def _default_resolver(host: str) -> list[str]:
    """Resolve all A/AAAA addresses for ``host`` (thread off the event loop)."""

    def _lookup() -> list[str]:
        infos = socket.getaddrinfo(host, None)
        ips: list[str] = []
        for family, _type, _proto, _canon, sockaddr in infos:
            if family not in (socket.AF_INET, socket.AF_INET6):
                continue
            ip_text = str(sockaddr[0])
            if ip_text not in ips:
                ips.append(ip_text)
        return ips

    try:
        return await asyncio.to_thread(_lookup)
    except (socket.gaierror, OSError) as exc:
        raise GuardError("dns_failed", f"DNS resolution failed for {host}: {exc}") from exc


def _pinned_target(parts: SplitResult, ip_text: str) -> str:
    """URL form that dials the validated IP directly (keeps scheme/path/query)."""
    host_form = f"[{ip_text}]" if ":" in ip_text else ip_text
    port = parts.port or _IMPLICIT_PORT[parts.scheme.lower()]
    path = parts.path or "/"
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{host_form}:{port}{path}{query}"


def _media_type(content_type: str | None) -> str:
    if not content_type:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def sniff_image_media_type(head: bytes) -> str | None:
    """Magic-byte sniff for the preview download (content-type may lie)."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    if head.startswith(b"BM"):
        return "image/bmp"
    return None


def _validate_response_shape(
    *,
    expect: str,
    content_type: str | None,
    first_chunk: bytes,
) -> None:
    """Content-type (plus first-byte sniff for images) gate. Raises GuardError."""
    media = _media_type(content_type)
    if expect == "html":
        # HTML (xhtml) is expected for metadata extraction; a missing
        # content-type header is tolerated (the parser simply finds nothing).
        if media and media not in {"text/html", "application/xhtml+xml"}:
            raise GuardError(
                "unsupported_content_type",
                f"expected html, got content-type: {media or content_type!r}",
            )
        return
    if expect == "image":
        if media and not media.startswith("image/"):
            raise GuardError(
                "unsupported_content_type",
                f"expected image/*, got content-type: {media}",
            )
        sniffed = sniff_image_media_type(first_chunk)
        if sniffed is None:
            raise GuardError(
                "unsupported_content_type",
                "body does not start with a known image signature",
            )
        return
    raise ValueError(f"unknown expect kind: {expect}")


async def fetch_guarded(
    url: str,
    *,
    max_redirects: int = MAX_REDIRECTS,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    total_timeout: float = DEFAULT_TOTAL_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    expect: Literal["html", "image"] = "html",
    resolver: Resolver | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> GuardedFetch:
    """Fetch ``url`` under the SSRF guard.

    Per hop: static URL checks → resolve ALL A/AAAA → ``pure_decision`` on
    the validated IPs → dial one of the validated IPs directly. The request
    carries the original ``Host`` header and, for https, the ``sni_hostname``
    extension (httpcore dials the IP but performs TLS SNI + certificate
    hostname verification against the original hostname), so redirect hops
    to a different origin cannot silently reuse a validated connection and
    the DNS-rebinding TOCTOU window is closed.

    Redirects are manual: at most ``max_redirects`` hops, each hop fully
    re-validated; a public→private transition is denied.

    HTTP status semantics are NOT policy: non-2xx responses are returned
    with their status so the caller decides (a 404 product page yields an
    empty/partial extraction rather than a transport error).
    """
    if max_redirects > MAX_REDIRECTS:
        # The pure policy caps redirects at MAX_REDIRECTS regardless.
        max_redirects = MAX_REDIRECTS
    resolve = resolver or _default_resolver

    redirect_chain: list[str] = []
    current = url
    try:
        async with asyncio.timeout(total_timeout):
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=httpx.Timeout(connect_timeout),
                transport=transport,
            ) as client:
                while True:
                    parts = urlsplit(current)
                    deny = _static_url_checks(
                        current, port_policy="web_standard"
                    )
                    if deny is not None:
                        raise GuardError(deny.reason, deny.detail)
                    assert parts.hostname is not None  # checked above

                    ips = await resolve(parts.hostname)
                    decision = pure_decision(current, ips, redirect_chain)
                    if isinstance(decision, Deny):
                        raise GuardError(decision.reason, decision.detail)
                    target_ip = ipaddress.ip_address(ips[0])
                    pinned = _pinned_target(parts, str(target_ip))
                    headers = {"host": parts.netloc}
                    extensions = (
                        {"sni_hostname": parts.hostname}
                        if parts.scheme.lower() == "https"
                        else {}
                    )
                    try:
                        async with client.stream(
                            "GET",
                            pinned,
                            headers=headers,
                            extensions=extensions,
                        ) as response:
                            if (
                                response.status_code in _REDIRECT_STATUSES
                                and response.headers.get("location")
                            ):
                                if len(redirect_chain) >= max_redirects:
                                    raise GuardError(
                                        "too_many_redirects",
                                        f"more than {max_redirects} redirects",
                                    )
                                redirect_chain.append(current)
                                current = urljoin(
                                    current, response.headers["location"]
                                )
                                continue

                            content_type = response.headers.get("content-type")
                            status_code = response.status_code
                            chunks: list[bytes] = []
                            total = 0
                            first_chunk = b""
                            async for chunk in response.aiter_bytes():
                                if len(first_chunk) < 16:
                                    first_chunk = (first_chunk + chunk)[:16]
                                total += len(chunk)
                                if total > max_bytes:
                                    raise GuardError(
                                        "response_too_large",
                                        f"body exceeds {max_bytes} bytes",
                                    )
                                chunks.append(chunk)
                            body = b"".join(chunks)
                    except httpx.TimeoutException as exc:
                        raise GuardError("timeout", f"request timed out: {exc}") from exc
                    except httpx.HTTPError as exc:
                        # Residual reachability failures (refused/reset/TLS
                        # errors) bucket to dns_failed in v1: the deny-reason
                        # taxonomy is closed and DNS is the dominant cause.
                        # The detail preserves the underlying error.
                        raise GuardError(
                            "dns_failed", f"request failed: {exc}"
                        ) from exc

                    _validate_response_shape(
                        expect=expect,
                        content_type=content_type,
                        first_chunk=first_chunk,
                    )
                    return GuardedFetch(
                        url=current,
                        status=status_code,
                        content_type=content_type,
                        body=body,
                        redirect_chain=list(redirect_chain),
                    )
    except asyncio.TimeoutError as exc:
        raise GuardError(
            "timeout", f"total fetch budget of {total_timeout}s exceeded"
        ) from exc
