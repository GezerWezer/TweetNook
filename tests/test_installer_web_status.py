"""Exercise the installer's embedded check against real package config/address APIs.

It stays embedded so the standalone installer can check older published packages.
Load its functions without running the privileged entry point.
"""

import http.client
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from tweetnook.web import address


@pytest.fixture
def status():
    script = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
    source = script.split("<<'WEB_STATUS'\n", 1)[1].split("\nWEB_STATUS", 1)[0]
    module = ModuleType("installer_web_status")
    exec(compile(source, "install.sh:WEB_STATUS", "exec"), module.__dict__)
    return module


@pytest.mark.parametrize(
    ("host", "device", "expected_scope", "expected_url"),
    [
        ("0.0.0.0", "192.168.1.42", "network", "http://192.168.1.42:9123"),
        ("0.0.0.0", None, "unknown", "http://127.0.0.1:9123"),
        ("::", None, "unknown", "http://[::1]:9123"),
        ("127.0.0.1", None, "local", "http://127.0.0.1:9123"),
        ("::1", None, "local", "http://[::1]:9123"),
        ("localhost", None, "local", "http://localhost:9123"),
        ("archive.local", None, "network", "http://archive.local:9123"),
        ("[2001:db8::10]", None, "network", "http://[2001:db8::10]:9123"),
    ],
)
def test_address_scope_preserves_configured_port_and_ipv6(
    status, monkeypatch, host, device, expected_scope, expected_url
):
    monkeypatch.setattr(address, "_discover_device_address", lambda _family: device)
    assert status.setup_address(host, 9123) == (expected_scope, expected_url, 9123)


def install_fake_http(status, monkeypatch, responses):
    calls = []
    clock = {"now": 0}
    sequence = iter(responses)
    monkeypatch.setattr(
        status,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock["now"],
            sleep=lambda seconds: clock.update(now=clock["now"] + seconds),
        ),
    )

    class Connection:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def request(self, method, path):
            calls.append((method, path))

        def getresponse(self):
            response = next(sequence)
            if isinstance(response, Exception):
                raise response
            code, body, auth = response
            return SimpleNamespace(
                status=code, read=lambda _size: body, getheader=lambda _name: auth
            )

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(status.http.client, "HTTPConnection", Connection)
    return calls, clock


@pytest.mark.parametrize(
    ("host", "probe_host"),
    [("0.0.0.0", "127.0.0.1"), ("::", "::1"), ("[::]", "::1"), ("archive.local", "archive.local")],
)
def test_http_retries_until_app_is_ready_and_probes_bound_interface(
    status, monkeypatch, host, probe_host
):
    calls, clock = install_fake_http(
        status,
        monkeypatch,
        [
            ConnectionRefusedError(),
            (200, b"<title>Another app</title>", None),
            (200, b"<title>TweetNook</title>", None),
        ],
    )
    status.wait_for_web(host, 9123)
    assert [call for call in calls if call[0] == "connect"] == [
        ("connect", probe_host, 9123, 1)
    ] * 3
    assert calls.count(("GET", "/")) == 3
    assert calls.count(("close",)) == 3
    assert clock["now"] == 0.5


def test_password_protected_app_is_ready_without_sending_credentials(status, monkeypatch):
    calls, _clock = install_fake_http(
        status, monkeypatch, [(401, b'{"detail":"Incorrect password"}', "Basic")]
    )
    status.wait_for_web("127.0.0.1", 8000)
    assert ("GET", "/") in calls
    assert calls.count(("close",)) == 1


@pytest.mark.parametrize(
    "response",
    [
        (503, b"Starting", None),
        (200, b"Other service", None),
        (302, b"Redirect to another host", None),
        (401, b'{"detail":"Other app"}', "Basic"),
        (401, b"Malformed response", "Basic"),
        http.client.BadStatusLine("invalid"),
        TimeoutError(),
    ],
)
def test_http_timeout_is_bounded_and_rejects_unrelated_responses(status, monkeypatch, response):
    calls, clock = install_fake_http(status, monkeypatch, [response] * 4)
    with pytest.raises(ValueError, match="configured port \\(9123\\).*within 1 seconds"):
        status.wait_for_web("0.0.0.0", 9123, timeout=1)
    assert clock["now"] == 1
    assert calls.count(("close",)) == 4


def prepare_main(status, monkeypatch, tmp_path, states=None):
    events = []
    config = tmp_path / "config.toml"
    config.write_text('[web]\nhost = "0.0.0.0"\nport = 9123\n')
    environment = {"TWEETNOOK_AUTH_TOKEN": "synthetic-caller-value", "XDG_CONFIG_HOME": "/wrong"}
    monkeypatch.setattr(
        status,
        "os",
        SimpleNamespace(
            environ=environment,
            initgroups=lambda *args: events.append(("groups", *args)),
            setgid=lambda gid: events.append(("gid", gid)),
            setuid=lambda uid: events.append(("uid", uid)),
        ),
    )
    monkeypatch.setattr(
        status.pwd,
        "getpwnam",
        lambda _user: SimpleNamespace(
            pw_name="tweetnook", pw_uid=999, pw_gid=998, pw_dir="/var/lib/tweetnook"
        ),
    )

    def paths(env):
        assert events[:3] == [("groups", "tweetnook", 998), ("gid", 998), ("uid", 999)]
        assert events[-1] == ("verify",)
        assert env == status.SERVICE_ENVIRONMENT
        return SimpleNamespace(config_file=config)

    monkeypatch.setattr(status, "resolve_paths", paths)
    monkeypatch.setattr(status, "_verify_service_running", lambda: events.append(("verify",)))
    sequence = iter(states or [("active", "running", 123)] * 2)
    monkeypatch.setattr(status, "_service_runtime_state", lambda: next(sequence))
    monkeypatch.setattr(
        status, "wait_for_web", lambda host, port: events.append(("web", host, port))
    )
    monkeypatch.setattr(address, "_discover_device_address", lambda _family: "192.168.1.42")
    return events, environment


def test_main_drops_privileges_before_reading_only_service_configuration(
    status, monkeypatch, tmp_path, capsys
):
    events, environment = prepare_main(status, monkeypatch, tmp_path)
    status.main()
    assert events == [
        ("groups", "tweetnook", 998),
        ("gid", 998),
        ("uid", 999),
        ("verify",),
        ("web", "0.0.0.0", 9123),
    ]
    assert environment == status.SERVICE_ENVIRONMENT
    assert capsys.readouterr().out == "network http://192.168.1.42:9123 9123\n"


@pytest.mark.parametrize("final", [("failed", "failed", 0), ("active", "running", 456)])
def test_service_failure_or_restart_during_http_check_cannot_report_ready(
    status, monkeypatch, tmp_path, capsys, final
):
    prepare_main(status, monkeypatch, tmp_path, [("active", "running", 123), final])
    with pytest.raises(ValueError, match="stopped or restarted"):
        status.main()
    assert not capsys.readouterr().out


def test_main_rejects_a_root_service_account_before_dropping_privileges(
    status, monkeypatch, tmp_path
):
    events, _environment = prepare_main(status, monkeypatch, tmp_path)
    monkeypatch.setattr(
        status.pwd, "getpwnam", lambda _user: SimpleNamespace(pw_uid=0, pw_dir="/var/lib/tweetnook")
    )
    with pytest.raises(ValueError, match="invalid UID or home"):
        status.main()
    assert not events


def test_missing_first_start_config_uses_defaults_without_creating_files(
    status, monkeypatch, tmp_path, capsys
):
    events, _environment = prepare_main(status, monkeypatch, tmp_path)
    config = tmp_path / "config.toml"
    config.unlink()
    status.main()
    assert ("web", "0.0.0.0", 8000) in events
    assert capsys.readouterr().out == "network http://192.168.1.42:8000 8000\n"
    assert not config.exists()


def test_config_created_while_service_settles_preserves_custom_port(
    status, monkeypatch, tmp_path, capsys
):
    events, _environment = prepare_main(status, monkeypatch, tmp_path)
    config = tmp_path / "config.toml"
    config.unlink()

    def settle():
        events.append(("verify",))
        config.write_text('[web]\nhost = "127.0.0.1"\nport = 9443\n')

    monkeypatch.setattr(status, "_verify_service_running", settle)
    status.main()
    assert ("web", "127.0.0.1", 9443) in events
    assert capsys.readouterr().out == "local http://127.0.0.1:9443 9443\n"


def test_missing_config_still_requires_a_responding_web_app(status, monkeypatch, tmp_path, capsys):
    prepare_main(status, monkeypatch, tmp_path)
    (tmp_path / "config.toml").unlink()

    def unavailable(host, port):
        assert (host, port) == ("0.0.0.0", 8000)
        raise ValueError("Web startup timed out")

    monkeypatch.setattr(status, "wait_for_web", unavailable)
    with pytest.raises(ValueError, match="Web startup timed out"):
        status.main()
    assert not capsys.readouterr().out


@pytest.mark.parametrize("contents", ["[web]\nport = -1\n", "[web\nport = 8000\n"])
def test_existing_invalid_config_is_not_replaced_by_defaults(
    status, monkeypatch, tmp_path, capsys, contents
):
    events, _environment = prepare_main(status, monkeypatch, tmp_path)
    (tmp_path / "config.toml").write_text(contents)
    with pytest.raises(ValueError):
        status.main()
    assert not any(event[0] == "web" for event in events)
    assert not capsys.readouterr().out


def test_unreadable_config_is_not_replaced_by_defaults(status, monkeypatch, tmp_path, capsys):
    events, _environment = prepare_main(status, monkeypatch, tmp_path)

    class Unreadable:
        def open(self, _mode):
            raise PermissionError("Cannot read service config")

    monkeypatch.setattr(
        status, "resolve_paths", lambda _env: SimpleNamespace(config_file=Unreadable())
    )
    with pytest.raises(PermissionError, match="Cannot read service config"):
        status.main()
    assert not any(event[0] == "web" for event in events)
    assert not capsys.readouterr().out
