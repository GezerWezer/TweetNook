import socket

import pytest

from tweetnook.web import address


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("route_available", [False, True])
@pytest.mark.parametrize("global_fallback", [False, True])
def test_ipv6_discovery_does_not_advertise_interface_local_addresses(
    monkeypatch, scoped, route_available, global_fallback
):
    host = "fe80::1234%3" if scoped else "fe80::1234"
    sockets = []

    class Probe:
        closed = False

        def __enter__(self):
            sockets.append(self)
            return self

        def __exit__(self, *args):
            self.closed = True

        def connect(self, destination):
            assert destination == ("2001:db8::1", 80, 0, 0)
            if not route_available:
                raise OSError("no default route")

        def getsockname(self):
            return (host, 0, 0, 3)

    monkeypatch.setattr(address.socket, "socket", lambda *args: Probe())
    monkeypatch.setattr(address.socket, "gethostname", lambda: "fixture-host")
    entries = [(socket.AF_INET6, socket.SOCK_DGRAM, 0, "", (host, 0, 0, 3))]
    if global_fallback:
        entries.append((socket.AF_INET6, socket.SOCK_DGRAM, 0, "", ("2001:db8::42", 0, 0, 0)))
    monkeypatch.setattr(address.socket, "getaddrinfo", lambda *args, **kwargs: entries)

    result = address.display_web_url("::", 9123)

    assert result == ("http://[2001:db8::42]:9123" if global_fallback else "http://[::1]:9123")
    assert all(probe.closed for probe in sockets)


def test_explicit_scoped_listener_host_is_still_formatted_as_configured():
    assert address.display_web_url("fe80::1234%3", 9123) == "http://[fe80::1234%253]:9123"
