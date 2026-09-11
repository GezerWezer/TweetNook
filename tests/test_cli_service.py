import subprocess
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
    assert calls == [
        ("daemon-reload",),
        ("enable", service.UNIT_NAME),
        ("restart", service.UNIT_NAME),
    ]
    result = runner.invoke(service.service_app, ["uninstall"])
    assert result.exit_code == 0
    assert calls[-2:] == [("disable", "--now", service.UNIT_NAME), ("daemon-reload",)]
    assert not unit_path.exists()
    assert data.read_bytes() == b"preserve"


def test_reinstall_reloads_code_in_an_already_active_service(tmp_path, monkeypatch):
    """Model start's no-op on active units, the cause of mixed API/static versions."""
    unit_path = tmp_path / "tweetnook.service"
    state = {"installed": "old", "running": None, "starts": 0}

    def systemctl(*args):
        if args[0] == "restart" or (args[:2] == ("enable", "--now") and state["running"] is None):
            assert service.sys.executable in unit_path.read_text()
            state["running"] = state["installed"]
            state["starts"] += 1

    monkeypatch.setattr(service, "UNIT_PATH", unit_path)
    monkeypatch.setattr(service, "_systemd", lambda **kwargs: None)
    monkeypatch.setattr(service, "service_user", lambda name: account())
    monkeypatch.setattr(service, "_systemctl", systemctl)
    runner = CliRunner()
    assert runner.invoke(app, ["service", "install"]).exit_code == 0
    assert state["running"] == "old"
    assert state["starts"] == 1
    # A package upgrade replaces static files but leaves imported Python in memory.
    state["installed"] = "new"
    result = runner.invoke(app, ["service", "install"])
    assert result.exit_code == 0, result.output
    assert state["running"] == "new"
    assert state["starts"] == 2
    assert "enabled and restarted" in result.output


@pytest.mark.parametrize("failed_action", ["daemon-reload", "enable", "restart"])
def test_install_reports_activation_failure_without_claiming_success(
    tmp_path, monkeypatch, failed_action
):
    calls = []

    def systemctl(*args):
        calls.append(args)
        if args[0] == failed_action:
            raise subprocess.CalledProcessError(1, ["systemctl", *args])

    monkeypatch.setattr(service, "UNIT_PATH", tmp_path / "tweetnook.service")
    monkeypatch.setattr(service, "_systemd", lambda **kwargs: None)
    monkeypatch.setattr(service, "service_user", lambda name: account())
    monkeypatch.setattr(service, "_systemctl", systemctl)
    result = CliRunner().invoke(app, ["service", "install"])
    assert result.exit_code == 1
    assert calls[-1][0] == failed_action
    assert "enabled and restarted" not in result.output


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


def write_managed_unit(unit_path, python):
    unit_path.write_text(service.render_unit(account(), python=str(python)))


def test_update_uses_managed_service_python_in_order(tmp_path, monkeypatch):
    unit_path = tmp_path / "tweetnook.service"
    service_python = tmp_path / "service-python"
    service_python.touch()
    write_managed_unit(unit_path, service_python)
    events = []
    versions = iter(["0.0.9\n", "0.0.10\n"])

    def systemd(**kwargs):
        events.append(("systemd", kwargs))

    def systemctl(*args):
        events.append(("systemctl", *args))

    def verify_service_running():
        events.append(("verify-service",))

    def run(command, **kwargs):
        events.append(("run", command, kwargs))
        stdout = next(versions) if command[1] == "-c" else ""
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(service, "UNIT_PATH", unit_path)
    monkeypatch.setattr(service, "_systemd", systemd)
    monkeypatch.setattr(service, "_systemctl", systemctl)
    monkeypatch.setattr(service, "_verify_service_running", verify_service_running)
    monkeypatch.setattr(service.subprocess, "run", run)

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    assert events == [
        ("systemd", {"admin": True}),
        (
            "run",
            [str(service_python), "-m", "pip", "--version"],
            {"check": True, "capture_output": True, "text": True},
        ),
        (
            "run",
            [str(service_python), "-c", "import tweetnook; print(tweetnook.__version__)"],
            {"check": True, "capture_output": True, "text": True},
        ),
        ("systemctl", "stop", service.UNIT_NAME),
        (
            "run",
            [str(service_python), "-m", "pip", "install", "--upgrade", "tweetnook"],
            {"check": True},
        ),
        ("systemctl", "start", service.UNIT_NAME),
        ("verify-service",),
        (
            "run",
            [str(service_python), "-c", "import tweetnook; print(tweetnook.__version__)"],
            {"check": True, "capture_output": True, "text": True},
        ),
    ]
    assert "TweetNook updated: 0.0.9 → 0.0.10" in result.output
    assert "TweetNook service restarted." in result.output


def test_update_reports_already_current(tmp_path, monkeypatch):
    unit_path = tmp_path / "tweetnook.service"
    service_python = tmp_path / "service-python"
    service_python.touch()
    write_managed_unit(unit_path, service_python)
    monkeypatch.setattr(service, "UNIT_PATH", unit_path)
    monkeypatch.setattr(service, "_systemd", lambda **kwargs: None)
    monkeypatch.setattr(service, "_systemctl", lambda *args: None)
    monkeypatch.setattr(service, "_verify_service_running", lambda: None)
    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(stdout="0.0.9\n"),
    )

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    assert "TweetNook is already up to date (0.0.9)." in result.output
    assert "TweetNook service restarted." in result.output


def test_update_restarts_service_after_pip_failure(tmp_path, monkeypatch):
    unit_path = tmp_path / "tweetnook.service"
    service_python = tmp_path / "service-python"
    service_python.touch()
    write_managed_unit(unit_path, service_python)
    events = []

    def systemctl(*args):
        events.append(("systemctl", *args))

    def verify_service_running():
        events.append(("verify-service",))

    def run(command, **kwargs):
        if command[1:4] == ["-m", "pip", "install"]:
            events.append(("pip", command))
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(stdout="0.0.9\n")

    monkeypatch.setattr(service, "UNIT_PATH", unit_path)
    monkeypatch.setattr(service, "_systemd", lambda **kwargs: None)
    monkeypatch.setattr(service, "_systemctl", systemctl)
    monkeypatch.setattr(service, "_verify_service_running", verify_service_running)
    monkeypatch.setattr(service.subprocess, "run", run)

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 1
    assert events == [
        ("systemctl", "stop", service.UNIT_NAME),
        (
            "pip",
            [str(service_python), "-m", "pip", "install", "--upgrade", "tweetnook"],
        ),
        ("systemctl", "start", service.UNIT_NAME),
        ("verify-service",),
    ]
    assert "TweetNook update failed" in result.output
    assert "TweetNook service restarted." in result.output


def test_update_refuses_live_manual_web_server_before_subprocesses(tmp_path, monkeypatch):
    unit_path = tmp_path / "tweetnook.service"
    service_python = tmp_path / "service-python"
    service_python.touch()
    data_home = tmp_path / "data"
    pid_file = data_home / "tweetnook" / ".web.pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text("4321")
    unit_path.write_text(
        service.render_unit(account(), python=str(service_python), data_home=data_home)
    )
    calls = []
    monkeypatch.setattr(service, "UNIT_PATH", unit_path)
    monkeypatch.setattr(service, "_systemd", lambda **kwargs: None)
    monkeypatch.setattr(service, "_is_tweetnook_serve_process", lambda pid: pid == 4321)
    monkeypatch.setattr(service, "_systemctl", lambda *args: calls.append(args))
    monkeypatch.setattr(service.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 1
    assert calls == []
    assert "manual TweetNook Web server is still running (PID: 4321)" in result.output
    assert "tweetnook web stop" in result.output


def test_update_does_not_claim_restart_when_service_dies_after_start(tmp_path, monkeypatch):
    unit_path = tmp_path / "tweetnook.service"
    service_python = tmp_path / "service-python"
    service_python.touch()
    write_managed_unit(unit_path, service_python)
    systemctl_calls = []
    versions = iter(["0.0.9\n"])

    def run(command, **kwargs):
        stdout = next(versions) if command[1] == "-c" else ""
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(service, "UNIT_PATH", unit_path)
    monkeypatch.setattr(service, "_systemd", lambda **kwargs: None)
    monkeypatch.setattr(service, "_systemctl", lambda *args: systemctl_calls.append(args))
    monkeypatch.setattr(
        service,
        "_verify_service_running",
        lambda: (_ for _ in ()).throw(ValueError("inactive/failed")),
    )
    monkeypatch.setattr(service.subprocess, "run", run)

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 1
    assert systemctl_calls == [
        ("stop", service.UNIT_NAME),
        ("start", service.UNIT_NAME),
    ]
    assert "managed service did not stay running" in result.output
    assert "inactive/failed" in result.output
    assert "TweetNook service restarted." not in result.output


def test_verify_service_running_reports_systemd_state(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout="ActiveState=failed\nSubState=failed\nMainPID=0\n")

    monkeypatch.setattr(service.time, "sleep", lambda seconds: calls.append(("sleep", seconds)))
    monkeypatch.setattr(service.subprocess, "run", run)

    with pytest.raises(ValueError, match="failed/failed") as error:
        service._verify_service_running()

    assert "Another process may own the configured Web port" in str(error.value)
    assert calls == [
        ("sleep", service.SERVICE_STARTUP_SETTLE_SECONDS),
        (
            [
                "systemctl",
                "show",
                service.UNIT_NAME,
                "--property=ActiveState",
                "--property=SubState",
                "--property=MainPID",
            ],
            {"check": True, "capture_output": True, "text": True},
        ),
    ]


def test_update_refuses_unmanaged_service_before_subprocesses(tmp_path, monkeypatch):
    unit_path = tmp_path / "tweetnook.service"
    unit_path.write_text("[Service]\nExecStart=/usr/bin/python -m tweetnook serve\n")
    calls = []
    monkeypatch.setattr(service, "UNIT_PATH", unit_path)
    monkeypatch.setattr(service, "_systemd", lambda **kwargs: None)
    monkeypatch.setattr(service, "_systemctl", lambda *args: calls.append(args))
    monkeypatch.setattr(service.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 1
    assert calls == []
    assert "not managed by TweetNook" in result.output


def test_update_missing_pip_leaves_service_running(tmp_path, monkeypatch):
    unit_path = tmp_path / "tweetnook.service"
    service_python = tmp_path / "service-python"
    service_python.touch()
    write_managed_unit(unit_path, service_python)
    systemctl_calls = []
    subprocess_calls = []

    def run(command, **kwargs):
        subprocess_calls.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(service, "UNIT_PATH", unit_path)
    monkeypatch.setattr(service, "_systemd", lambda **kwargs: None)
    monkeypatch.setattr(service, "_systemctl", lambda *args: systemctl_calls.append(args))
    monkeypatch.setattr(service.subprocess, "run", run)

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 1
    assert subprocess_calls == [[str(service_python), "-m", "pip", "--version"]]
    assert systemctl_calls == []
    assert "update preflight failed" in result.output


def test_service_and_serve_help():
    runner = CliRunner()
    assert "foreground" in runner.invoke(app, ["serve", "--help"]).output
    result = runner.invoke(app, ["service", "install", "--help"])
    assert result.exit_code == 0
    assert "restart to load installed code" in " ".join(result.output.split())
    for flag in ["--user", "--data-home", "--config-home", "--cache-home"]:
        assert flag in result.output
    for command in ["start", "stop", "restart", "status", "uninstall"]:
        assert runner.invoke(app, ["service", command, "--help"]).exit_code == 0
    assert runner.invoke(app, ["update", "--help"]).exit_code == 0
