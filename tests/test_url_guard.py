"""SSRF guard tests (R3): pure policy matrix + guarded fetch via test seams.

The pure_decision matrix runs with NO network I/O — URLs and resolved-IP
lists are plain data. Guarded-fetch behaviour is exercised through the
injectable ``resolver`` / ``transport`` seams (httpx.MockTransport), which
keep every test offline.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from stroy.net.guard import (
    Allow,
    Deny,
    GuardError,
    fetch_guarded,
    pure_decision,
)

# Real public addresses (py3.14 quirk: documentation TEST-NET ranges are
# classified is_private=True, so they cannot stand in for "public").
PUBLIC_IP = "8.8.8.8"
PUBLIC_IP2 = "93.184.216.34"
PUBLIC_V6 = "2606:4700:4700::1111"
PUBLIC_URL = "https://shop.example.com/product/sofa-1"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"0" * 10


def allow(url: str, ips: list[str], chain: list[str] | None = None) -> None:
    decision = pure_decision(url, ips, chain or [])
    assert isinstance(decision, Allow), decision


def deny(url: str, ips: list[str], chain: list[str] | None = None) -> Deny:
    decision = pure_decision(url, ips, chain or [])
    assert isinstance(decision, Deny), f"expected Deny, got {decision}"
    return decision


# ---------------------------------------------------------------------------
# pure_decision: schemes / userinfo / ports
# ---------------------------------------------------------------------------


def test_schemes_http_https_only() -> None:
    assert deny("file:///etc/passwd", [PUBLIC_IP]).reason == "invalid_scheme"
    assert deny("ftp://shop.example.com/x", [PUBLIC_IP]).reason == "invalid_scheme"
    assert deny("javascript:alert(1)", [PUBLIC_IP]).reason == "invalid_scheme"
    assert deny("gopher://shop.example.com", [PUBLIC_IP]).reason == "invalid_scheme"
    allow("http://shop.example.com/p", [PUBLIC_IP])
    allow("https://shop.example.com/p", [PUBLIC_IP])


def test_userinfo_urls_rejected() -> None:
    assert (
        deny("https://user:pass@shop.example.com/p", [PUBLIC_IP]).reason
        == "userinfo_rejected"
    )
    assert (
        deny("https://user@shop.example.com/p", [PUBLIC_IP]).reason
        == "userinfo_rejected"
    )


def test_ports_restricted_to_web_standard() -> None:
    for port in ("22", "8000", "8080", "6379"):
        assert (
            deny(f"http://shop.example.com:{port}/p", [PUBLIC_IP]).reason
            == "port_rejected"
        )
    allow("http://shop.example.com/p", [PUBLIC_IP])  # implicit 80
    allow("http://shop.example.com:80/p", [PUBLIC_IP])
    allow("https://shop.example.com/p", [PUBLIC_IP])  # implicit 443
    allow("https://shop.example.com:443/p", [PUBLIC_IP])
    # scheme/port mismatch is still rejected
    assert deny("https://shop.example.com:80/p", [PUBLIC_IP]).reason == "port_rejected"


def test_malformed_port_rejected() -> None:
    with pytest.raises(ValueError):
        # pure_decision propagates the unknown-policy programming error
        pure_decision(PUBLIC_URL, [PUBLIC_IP], [], port_policy="bogus")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# pure_decision: IP classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip",
    [
        "10.1.2.3",
        "127.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "169.254.0.1",  # link-local
        "0.0.0.0",  # unspecified
        "240.0.0.1",  # reserved
        "224.0.0.1",  # multicast
        "::1",  # loopback
        "fc00::1",  # unique local
        "fe80::1",  # link-local
        "::",  # unspecified
        "::ffff:10.0.0.1",  # IPv4-mapped private
    ],
)
def test_private_addresses_denied(ip: str) -> None:
    assert deny("http://shop.example.com/p", [ip]).reason == "private_address"


def test_public_addresses_allowed() -> None:
    allow("http://shop.example.com/p", [PUBLIC_IP])
    allow("http://shop.example.com/p", [PUBLIC_V6])
    allow("http://shop.example.com/p", [PUBLIC_IP, PUBLIC_IP2, PUBLIC_V6])


def test_any_private_ip_in_resolution_denies() -> None:
    # ALL resolved A/AAAA must be public: one bad address denies the fetch.
    assert (
        deny("http://shop.example.com/p", [PUBLIC_IP, "10.0.0.9"]).reason
        == "private_address"
    )


def test_empty_resolution_is_dns_failed() -> None:
    assert deny("http://shop.example.com/p", []).reason == "dns_failed"


def test_literal_private_host_in_url_denied_without_resolution() -> None:
    # A URL that literally names a private host is denied on its own address.
    assert deny("http://127.0.0.1/p", ["127.0.0.1"]).reason == "private_address"
    assert deny("http://[::1]/p", ["::1"]).reason == "private_address"


# ---------------------------------------------------------------------------
# pure_decision: redirect chain
# ---------------------------------------------------------------------------


def test_redirect_public_to_private_denied() -> None:
    decision = deny(
        "http://internal.example.com/p",
        ["10.0.0.5"],
        ["https://shop.example.com/a"],
    )
    assert decision.reason == "redirect_private_address"


def test_redirect_chain_rechecks_every_hop() -> None:
    # A hop with userinfo / bad scheme / bad port denies, even mid-chain.
    assert (
        deny(
            PUBLIC_URL,
            [PUBLIC_IP],
            ["https://shop.example.com:8080/a"],
        ).reason
        == "port_rejected"
    )
    assert (
        deny(
            PUBLIC_URL,
            [PUBLIC_IP],
            ["https://user:pass@shop.example.com/a"],
        ).reason
        == "userinfo_rejected"
    )
    assert (
        deny(PUBLIC_URL, [PUBLIC_IP], ["file:///etc/passwd"]).reason
        == "invalid_scheme"
    )


def test_redirect_cap_three_hops() -> None:
    chain = [f"https://shop.example.com/hop{i}" for i in range(4)]
    assert deny(PUBLIC_URL, [PUBLIC_IP], chain).reason == "too_many_redirects"
    allow(PUBLIC_URL, [PUBLIC_IP], chain[:3])  # exactly 3 hops is fine


# ---------------------------------------------------------------------------
# fetch_guarded: guarded fetch through resolver/transport seams (offline)
# ---------------------------------------------------------------------------


def make_resolver(mapping: dict[str, list[str]]):
    async def resolve(host: str) -> list[str]:
        if host not in mapping:
            raise GuardError("dns_failed", f"unexpected host {host}")
        return mapping[host]

    return resolve


def make_transport(
    handler, seen: list[httpx.Request] | None = None
) -> httpx.MockTransport:
    def record(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return handler(request)

    return httpx.MockTransport(record)


async def test_fetch_guarded_pins_connection_ip_and_keeps_host_sni() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>ok</html>", headers={
            "Content-Type": "text/html"
        })

    result = await fetch_guarded(
        PUBLIC_URL,
        resolver=make_resolver({"shop.example.com": [PUBLIC_IP2]}),
        transport=make_transport(handler, seen),
    )
    assert result.status == 200
    assert len(seen) == 1
    sent = seen[0]
    # The connection dials the validated IP, never the hostname…
    assert sent.url.host == PUBLIC_IP2
    # …while Host header and TLS SNI keep the original hostname (so the
    # response is the real vhost and cert verification stays meaningful).
    assert sent.headers["host"] == "shop.example.com"
    assert sent.url.scheme == "https"
    assert sent.extensions.get("sni_hostname") == "shop.example.com"


async def test_fetch_guarded_follows_redirects_and_reports_chain() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(302, headers={"Location": "/product/real"})
        return httpx.Response(200, content=b"<html>real</html>", headers={
            "Content-Type": "text/html"
        })

    result = await fetch_guarded(
        "http://shop.example.com/a",
        resolver=make_resolver({"shop.example.com": [PUBLIC_IP]}),
        transport=make_transport(handler),
    )
    assert result.status == 200
    # The final URL is reported in its original (hostname) form.
    assert result.url == "http://shop.example.com/product/real"
    assert result.redirect_chain == ["http://shop.example.com/a"]
    assert calls["n"] == 2


async def test_fetch_guarded_denies_redirect_into_private_address() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://internal.test/p"})

    with pytest.raises(GuardError) as excinfo:
        await fetch_guarded(
            "http://shop.example.com/a",
            resolver=make_resolver(
                {"shop.example.com": [PUBLIC_IP], "internal.test": ["10.0.0.5"]}
            ),
            transport=make_transport(handler),
        )
    assert excinfo.value.reason == "redirect_private_address"


async def test_fetch_guarded_redirect_cap() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(302, headers={"Location": f"/hop{calls['n']}"})

    with pytest.raises(GuardError) as excinfo:
        await fetch_guarded(
            "http://shop.example.com/a",
            resolver=make_resolver({"shop.example.com": [PUBLIC_IP]}),
            transport=make_transport(handler),
        )
    assert excinfo.value.reason == "too_many_redirects"
    # initial request + 3 redirects, then the 4th redirect is refused
    assert calls["n"] == 4


async def test_fetch_guarded_abort_on_oversize() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        async def chunks():
            for _ in range(100):
                yield b"x" * 1024  # 100 KiB total, cap is 4 KiB

        return httpx.Response(200, content=chunks(), headers={
            "Content-Type": "text/html"
        })

    with pytest.raises(GuardError) as excinfo:
        await fetch_guarded(
            "http://shop.example.com/big",
            max_bytes=4096,
            resolver=make_resolver({"shop.example.com": [PUBLIC_IP]}),
            transport=make_transport(handler),
        )
    assert excinfo.value.reason == "response_too_large"


async def test_fetch_guarded_timeout_reason() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        raise AssertionError("should never finish")  # pragma: no cover

    with pytest.raises(GuardError) as excinfo:
        await fetch_guarded(
            "http://shop.example.com/slow",
            total_timeout=0.1,
            resolver=make_resolver({"shop.example.com": [PUBLIC_IP]}),
            transport=make_transport(handler),
        )
    assert excinfo.value.reason == "timeout"


@pytest.mark.parametrize(
    ("expect", "content_type", "body", "ok"),
    [
        ("html", "text/html", b"<html/>", True),
        ("html", "application/xhtml+xml; charset=utf-8", b"<html/>", True),
        ("html", None, b"<html/>", True),  # header missing: tolerated
        ("html", "application/pdf", b"%PDF-1.4", False),
        ("html", "text/plain", b"hello", False),
        ("image", "image/png", PNG_MAGIC, True),
        ("image", "image/jpeg", b"\xff\xd8\xff\xe0" + b"0" * 12, True),
        ("image", None, PNG_MAGIC, True),  # header missing: sniff saves it
        ("image", "image/png", b"<html>not an image</html>", False),  # sniff
        ("image", "text/html", b"<html/>", False),
        ("image", "application/pdf", b"%PDF-1.4", False),
    ],
)
async def test_fetch_guarded_content_type_gate(
    expect: str, content_type: str | None, body: bytes, ok: bool
) -> None:
    headers: dict[str, str] = {}
    if content_type is not None:
        headers["Content-Type"] = content_type

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers=headers)

    kwargs: dict[str, Any] = dict(
        resolver=make_resolver({"shop.example.com": [PUBLIC_IP]}),
        transport=make_transport(handler),
    )
    if ok:
        result = await fetch_guarded("http://shop.example.com/x", expect=expect, **kwargs)  # type: ignore[arg-type]
        assert result.status == 200
    else:
        with pytest.raises(GuardError) as excinfo:
            await fetch_guarded("http://shop.example.com/x", expect=expect, **kwargs)  # type: ignore[arg-type]
        assert excinfo.value.reason == "unsupported_content_type"


async def test_fetch_guarded_maps_resolution_failure_to_dns_failed() -> None:
    async def resolve(_host: str) -> list[str]:
        raise GuardError("dns_failed", "resolution exploded")

    with pytest.raises(GuardError) as excinfo:
        await fetch_guarded(
            "http://shop.example.com/x",
            resolver=resolve,
            transport=make_transport(lambda r: httpx.Response(200)),
        )
    assert excinfo.value.reason == "dns_failed"


async def test_fetch_guarded_private_target_denied_before_any_request() -> None:
    seen: list[httpx.Request] = []

    with pytest.raises(GuardError) as excinfo:
        await fetch_guarded(
            "http://shop.example.com/x",
            resolver=make_resolver({"shop.example.com": ["127.0.0.1"]}),
            transport=make_transport(
                lambda r: httpx.Response(200), seen
            ),
        )
    assert excinfo.value.reason == "private_address"
    assert seen == []  # nothing was ever sent
