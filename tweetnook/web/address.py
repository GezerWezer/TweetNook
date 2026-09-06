"""Resolve user-facing Web UI addresses without changing the server bind host."""

from __future__ import annotations

import ipaddress
import socket


def _usable_device_address(value: str) -> bool:
    candidate = value.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return not (address.is_unspecified or address.is_loopback or address.is_multicast)


def _discover_device_address(family: socket.AddressFamily) -> str | None:
    destination = ("192.0.2.1", 80) if family == socket.AF_INET else ("2001:db8::1", 80, 0, 0)
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as probe:
            probe.connect(destination)
            candidate = str(probe.getsockname()[0])
            if _usable_device_address(candidate):
                return candidate
    except OSError:
        pass

    try:
        addresses = socket.getaddrinfo(
            socket.gethostname(),
            None,
            family=family,
            type=socket.SOCK_DGRAM,
        )
    except OSError:
        return None
    for address in addresses:
        candidate = str(address[4][0])
        if _usable_device_address(candidate):
            return candidate
    return None


def display_web_host(bind_host: str) -> str:
    """Return a reachable host for display while leaving concrete bind hosts unchanged."""

    host = bind_host.strip()
    if host == "0.0.0.0":
        return _discover_device_address(socket.AF_INET) or "127.0.0.1"
    if host in {"::", "[::]"}:
        return _discover_device_address(socket.AF_INET6) or "::1"
    return host


def display_web_url(bind_host: str, port: int) -> str:
    """Build the Web UI URL a user can actually open in a browser."""

    host = display_web_host(bind_host)
    if host.startswith("[") and host.endswith("]"):
        url_host = host
    elif ":" in host:
        url_host = f"[{host.replace('%', '%25')}]"
    else:
        url_host = host
    return f"http://{url_host}:{port}"
