"""Outbound request safety for POS URLs that tenant admins configure (SSRF guard)."""
import ipaddress
import socket
from urllib.parse import urlparse

from django.conf import settings


class UnsafeURL(ValueError):
    pass


def validate_outbound_url(url: str) -> str:
    parts = urlparse(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise UnsafeURL("URL must be http(s) with a host.")
    if parts.scheme == "http" and not settings.POS_ALLOW_PRIVATE_NETWORKS:
        raise UnsafeURL("Only https URLs are allowed.")
    if settings.POS_ALLOW_PRIVATE_NETWORKS:
        return url
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURL("Host cannot be resolved.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise UnsafeURL("URL points to a private or reserved network address.")
    return url
