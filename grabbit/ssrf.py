"""SSRF defense: reject URLs that resolve to private/loopback/link-local ranges."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class SSRFError(ValueError):
    pass


def _is_forbidden(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def check_url(url: str) -> None:
    """Raise SSRFError unless `url` is http(s) to a public address.

    Note: resolution here is advisory (defense in depth) — the primary control
    is extractor matching. A DNS-rebinding attacker could still flip records
    between this check and the engine's fetch; the extractor allowlist is what
    prevents fetching arbitrary attacker-chosen URLs in the first place.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFError(f"scheme {parsed.scheme!r} not allowed")
    host = parsed.hostname
    if not host:
        raise SSRFError("URL has no host")

    try:
        ip = ipaddress.ip_address(host)
        if _is_forbidden(ip):
            raise SSRFError("IP-literal URL in private/reserved range")
        return
    except ValueError:
        pass  # not an IP literal — resolve it

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise SSRFError(f"cannot resolve host: {e}") from e
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if _is_forbidden(addr):
            raise SSRFError(f"host resolves to forbidden address {addr}")
