from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from tweetnook import cli_service as service
from tweetnook.cli import app
from tweetnook.job_supervisor import WORKER_INTERRUPT_TIMEOUT, WORKER_TERMINATE_TIMEOUT


def account():
    return SimpleNamespace(pw_name="archiver", pw_dir="/home/archiver", pw_uid=1000)


def test_unit_uses_exact_environment_and_bounded_graceful_cgroup_shutdown():
    unit = service.render_unit(account(), python="/opt/archive env/bin/python")
    assert 'ExecStart="/opt/archive env/bin/python" -m tweetnook serve' in unit
    assert "User=archiver" in unit
    assert "Restart=on-failure" in unit
    assert "KillMode=control-group" in unit
    assert "KillSignal=SIGINT" in unit
    assert service.SERVICE_STOP_TIMEOUT > WORKER_INTERRUPT_TIMEOUT + WORKER_TERMINATE_TIMEOUT
    assert 'Environment="XDG_DATA_HOME=/home/archiver/.local/share"' in unit


def test_unit_escaping_and_invalid_paths():
    assert service._quote("$HOME%") == '"$HOME%%"'
    assert service._quote("$HOME%", expand_variables=True) == '"$$HOME%%"'
    with pytest.raises(ValueError):
        service._quote("injected\nUser=root")
    with pytest.raises(ValueError):
        service.render_unit(account(), python="/python", data_home=service.Path("relative"))


def test_install_enable_start_and_uninstall_preserve_data(tmp_path, monkeypatch):
    unit_path = tmp_path / "tweetnook.service"
    data = tmp_path / "archive.db"
    data.write_bytes(b"preserve")
    calls = []
    monkeypatch.setattr(service, "UNIT_PATH", unit_path)
    monkeypatch.setattr(service, "_systemd", lambda **kwargs: None)
    monkeypatch.setattr(service, "service_user", lambda name: account())
    monkeypatch.setattr(service, "_systemctl", lambda *args: calls.append(args))
    runner = CliRunner()
    result = runner.invoke(service.service_app, ["install", "--user", "archiver"])
    assert result.exit_code == 0, result.output
    assert service.sys.executable in unit_path.read_text()
    assert calls == [("daemon-reload",), ("enable", "--now", service.UNIT_NAME)]
    result = runner.invoke(service.service_app, ["uninstall"])
    assert result.exit_code == 0
    assert calls[-2:] == [("disable", "--now", service.UNIT_NAME), ("daemon-reload",)]
    assert not unit_path.exists()
    assert data.read_bytes() == b"preserve"


def test_install_refuses_unmanaged_unit(tmp_path, monkeypatch):
    unit = tmp_path / "unit"
    unit.write_text("[Service]\nUser=someone\n")
    monkeypatch.setattr(service, "UNIT_PATH", unit)
    with pytest.raises(ValueError, match="not managed"):
        service._owned_unit()


def test_service_user_sudo_default_and_direct_root_requires_user(monkeypatch):
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setattr(service.os, "geteuid", lambda: 0)
    with pytest.raises(ValueError, match="--user"):
        service.service_user(None)
    monkeypatch.setenv("SUDO_USER", "archiver")
    monkeypatch.setattr(service.pwd, "getpwnam", lambda name: account())
    assert service.service_user(None).pw_name == "archiver"
    with pytest.raises(ValueError, match="Invalid"):
        service.service_user("archiver\nUser=root")


def test_service_reports_missing_systemd(monkeypatch):
    monkeypatch.setattr(service.sys, "platform", "darwin")
    result = CliRunner().invoke(service.service_app, ["status"])
    assert result.exit_code == 1
    assert "systemd" in result.output


def test_service_and_serve_help():
    runner = CliRunner()
    assert "foreground" in runner.invoke(app, ["serve", "--help"]).output
    result = runner.invoke(app, ["service", "install", "--help"])
    assert result.exit_code == 0
    for flag in ["--user", "--data-home", "--config-home", "--cache-home"]:
        assert flag in result.output
    for command in ["start", "stop", "restart", "status", "uninstall"]:
        assert runner.invoke(app, ["service", command, "--help"]).exit_code == 0
