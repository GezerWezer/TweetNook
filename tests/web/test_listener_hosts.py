import pytest
from typer.testing import CliRunner

from tweetnook.cli import app
from tweetnook.config import load_config_at, update_config_values


@pytest.mark.parametrize(
    ("configured", "listener", "display"),
    [
        ("127.0.0.1", "127.0.0.1", "127.0.0.1"),
        (" 127.0.0.1 ", "127.0.0.1", "127.0.0.1"),
        ("::1", "::1", "[::1]"),
        ("[::1]", "::1", "[::1]"),
        (" [::1] ", "::1", "[::1]"),
        ("0.0.0.0", "0.0.0.0", "192.168.1.42"),
        (" [::] ", "::", "[2001:db8::42]"),
        (" archive.local ", "archive.local", "archive.local"),
        ("[fe80::1%en0]", "fe80::1%en0", "[fe80::1%25en0]"),
    ],
)
def test_configured_host_has_one_listener_and_display_contract(
    paths, monkeypatch, configured, listener, display
):
    import socket

    update_config_values(paths, {"web.host": configured, "web.port": 9123})
    before = paths.config_file.read_bytes()
    config, _ = load_config_at(paths, {})
    monkeypatch.setattr("tweetnook.cli.server.load_config", lambda: (config, paths))
    monkeypatch.setattr(
        "tweetnook.web.address._discover_device_address",
        lambda family: "192.168.1.42" if family == socket.AF_INET else "2001:db8::42",
    )
    launched = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: launched.append(kwargs))

    result = CliRunner().invoke(app, ["serve"])

    assert result.exit_code == 0, result.output
    assert launched[0]["host"] == listener
    assert launched[0]["port"] == 9123
    assert f"http://{display}:9123" in result.output
    assert paths.config_file.read_bytes() == before


def test_blank_listener_is_rejected_before_bootstrap(paths, monkeypatch):
    from tweetnook.core.exceptions import ConfigError
    from tweetnook.web.server import run_server

    def unexpected(*args, **kwargs):
        pytest.fail("blank listener must fail before bootstrap or launch")

    monkeypatch.setattr("tweetnook.web.server.upgrade_config", unexpected)
    monkeypatch.setattr("uvicorn.run", unexpected)
    with pytest.raises(ConfigError, match="host"):
        run_server(None, paths, "   ", 9123, None)
