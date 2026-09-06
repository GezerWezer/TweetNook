from __future__ import annotations

import hashlib
import signal
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import tweetnook.cli as cli
import tweetnook.cli_web as cli_web
import tweetnook.web.address as web_address
from tweetnook.config import AppConfig, XDGPaths

runner = CliRunner()


def _configured(tmp_path: Path, *, archive: bool = False) -> tuple[AppConfig, XDGPaths]:
    paths = XDGPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
    )
    paths.data_dir.mkdir(parents=True)
    if archive:
        paths.database_path.write_bytes(b"sqlite")
    return AppConfig(), paths


def _stub_start_prerequisites(monkeypatch, config: AppConfig, paths: XDGPaths) -> None:
    monkeypatch.setattr(cli_web, "_require_web_dependencies", lambda _console: None)
    monkeypatch.setattr(cli_web, "load_config", lambda: (config, paths))
    monkeypatch.setattr(cli_web, "_find_pid_by_port", lambda _port: None)


def test_default_web_host_accepts_lan_connections() -> None:
    assert AppConfig().web.host == "0.0.0.0"


def test_pid_file_uses_hidden_file_in_data_directory(tmp_path: Path) -> None:
    assert cli_web._get_pid_file(tmp_path) == tmp_path / ".web.pid"


def test_display_web_url_uses_device_ip_for_ipv4_wildcard(monkeypatch) -> None:
    monkeypatch.setattr(
        web_address,
        "_discover_device_address",
        lambda family: "192.168.1.42" if family == web_address.socket.AF_INET else None,
    )

    assert web_address.display_web_url("0.0.0.0", 9123) == "http://192.168.1.42:9123"


def test_display_web_url_preserves_concrete_hosts_and_formats_ipv6() -> None:
    assert web_address.display_web_url("127.0.0.1", 8000) == "http://127.0.0.1:8000"
    assert web_address.display_web_url("archive.local", 8000) == "http://archive.local:8000"
    assert web_address.display_web_url("::1", 8000) == "http://[::1]:8000"


def test_display_web_url_uses_loopback_when_device_ip_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(web_address, "_discover_device_address", lambda _family: None)

    assert web_address.display_web_url("0.0.0.0", 8000) == "http://127.0.0.1:8000"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("123\n", 123),
        (" 42 ", 42),
        ("0", None),
        ("-2", None),
        ("not-a-pid", None),
        ("", None),
    ],
)
def test_read_pid_validates_file_content(
    tmp_path: Path, content: str, expected: int | None
) -> None:
    pid_file = tmp_path / ".web.pid"
    pid_file.write_text(content)

    assert cli_web._read_pid(pid_file) == expected


def test_read_pid_handles_missing_file(tmp_path: Path) -> None:
    assert cli_web._read_pid(tmp_path / "missing.pid") is None


@pytest.mark.parametrize("pid", [0, -1])
def test_is_running_rejects_nonpositive_pids_without_signaling(monkeypatch, pid: int) -> None:
    monkeypatch.setattr(
        cli_web.os,
        "kill",
        lambda *_args: pytest.fail("nonpositive PID must not be signaled"),
    )

    assert cli_web._is_running(pid) is False


def test_is_running_checks_positive_pid_with_signal_zero(monkeypatch) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        cli_web.os,
        "kill",
        lambda pid, sig: calls.append((pid, sig)),
    )

    assert cli_web._is_running(123) is True
    assert calls == [(123, 0)]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ProcessLookupError(), False),
        (PermissionError(), True),
        (OSError("unknown"), False),
    ],
)
def test_is_running_interprets_signal_errors(monkeypatch, error, expected: bool) -> None:
    def fail(_pid, _sig):
        raise error

    monkeypatch.setattr(cli_web.os, "kill", fail)

    assert cli_web._is_running(123) is expected


def test_find_pid_by_port_prefers_lsof(monkeypatch) -> None:
    calls: list[tuple[list[str], dict]] = []

    def check_output(command, **kwargs):
        calls.append((command, kwargs))
        return "321\n654\n"

    monkeypatch.setattr(cli_web.subprocess, "check_output", check_output)

    assert cli_web._find_pid_by_port(8123) == 321
    assert calls == [
        (
            ["lsof", "-t", "-nP", "-iTCP:8123", "-sTCP:LISTEN"],
            {"text": True, "stderr": subprocess.DEVNULL},
        )
    ]


def test_find_pid_by_port_falls_back_to_fuser(monkeypatch) -> None:
    commands: list[list[str]] = []

    def check_output(command, **_kwargs):
        commands.append(command)
        if command[0] == "lsof":
            raise FileNotFoundError("lsof")
        return " 777 "

    monkeypatch.setattr(cli_web.subprocess, "check_output", check_output)

    assert cli_web._find_pid_by_port(8000) == 777
    assert commands == [
        ["lsof", "-t", "-nP", "-iTCP:8000", "-sTCP:LISTEN"],
        ["fuser", "8000/tcp"],
    ]


def test_find_pid_by_port_handles_command_failures_and_invalid_output(
    monkeypatch,
) -> None:
    calls = 0

    def check_output(_command, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.CalledProcessError(1, "lsof")
        return "not-a-pid 0"

    monkeypatch.setattr(cli_web.subprocess, "check_output", check_output)

    assert cli_web._find_pid_by_port(8000) is None
    assert calls == 2


def test_start_reports_missing_required_web_dependencies(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_web,
        "import_module",
        lambda _name: (_ for _ in ()).throw(ImportError("fastapi missing")),
    )
    monkeypatch.setattr(
        cli_web,
        "load_config",
        lambda: pytest.fail("config should not load without dependencies"),
    )

    result = runner.invoke(cli_web.web_app, ["start"])

    assert result.exit_code == 1
    assert "Web dependencies are missing" in result.stdout
    assert "Reinstall tweetnook" in result.stdout


def test_dependency_check_requires_server_stack_and_uvicorn(monkeypatch) -> None:
    imported: list[str] = []
    monkeypatch.setattr(
        cli_web,
        "import_module",
        lambda name: imported.append(name),
    )

    cli_web._require_web_dependencies(SimpleNamespace(print=lambda *_args: None))

    assert imported == ["tweetnook.web.server", "uvicorn"]


def test_start_without_archive(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path)
    _stub_start_prerequisites(monkeypatch, config, paths)
    monkeypatch.setattr(
        cli_web.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=2468),
    )

    result = runner.invoke(cli_web.web_app, ["start"])

    assert result.exit_code == 0
    assert not paths.database_path.exists()


def test_start_returns_success_when_recorded_server_is_running(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path, archive=True)
    _stub_start_prerequisites(monkeypatch, config, paths)
    cli_web._get_pid_file(paths.data_dir).write_text("4321")
    monkeypatch.setattr(cli_web, "_is_running", lambda pid: pid == 4321)
    monkeypatch.setattr(
        cli_web.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("duplicate server must not start"),
    )

    result = runner.invoke(cli_web.web_app, ["start"])

    assert result.exit_code == 0
    assert "already running (PID: 4321)" in result.stdout


def test_start_rejects_configured_port_collision(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path, archive=True)
    _stub_start_prerequisites(monkeypatch, config, paths)
    monkeypatch.setattr(cli_web, "_find_pid_by_port", lambda port: 99)
    monkeypatch.setattr(cli_web, "_is_running", lambda pid: pid == 99)
    monkeypatch.setattr(
        cli_web.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("colliding server must not start"),
    )

    result = runner.invoke(cli_web.web_app, ["start"])

    assert result.exit_code == 1
    assert "port 8000 is already in use (PID: 99)" in result.stdout


def test_start_preserves_unset_password_spawns_detached_process_and_writes_pid(
    monkeypatch, tmp_path: Path
) -> None:
    config, paths = _configured(tmp_path, archive=True)
    _stub_start_prerequisites(monkeypatch, config, paths)
    monkeypatch.setattr(cli_web, "_is_running", lambda _pid: False)
    saved: list[tuple[XDGPaths, dict[str, str]]] = []
    popen_calls: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(
        cli_web,
        "update_config_values",
        lambda actual_paths, changes: saved.append((actual_paths, changes)),
    )

    def popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        return SimpleNamespace(pid=2468)

    monkeypatch.setattr(cli_web.subprocess, "Popen", popen)
    monkeypatch.setattr(
        cli_web,
        "display_web_url",
        lambda host, port: f"http://192.168.1.25:{port}" if host == "0.0.0.0" else "",
    )

    result = runner.invoke(cli_web.web_app, ["start"])

    assert result.exit_code == 0
    assert config.web.password_hash is None
    assert saved == []
    assert "default password" not in result.stdout
    assert "http://192.168.1.25:8000" in result.stdout
    assert "http://0.0.0.0:8000" not in result.stdout
    assert popen_calls == [
        (
            [sys.executable, "-m", "tweetnook", "serve"],
            {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "start_new_session": True,
            },
        )
    ]
    assert cli_web._get_pid_file(paths.data_dir).read_text() == "2468"
    assert "Started background server (PID: 2468)" in result.stdout


def test_start_does_not_modify_sparse_configuration(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path, archive=True)
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text(
        """
[auth]
auth_token = ""
ct0 = ""
user_id = ""

[sync]
page_delay = 1

[tagging]
enabled = true
""".lstrip(),
        encoding="utf-8",
    )
    config.sync.max_retries = 9  # Simulates an environment-derived effective value.
    _stub_start_prerequisites(monkeypatch, config, paths)
    monkeypatch.setattr(
        cli_web.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=2469),
    )

    result = runner.invoke(cli_web.web_app, ["start"])

    assert result.exit_code == 0
    raw = tomllib.loads(paths.config_file.read_text(encoding="utf-8"))
    assert "web" not in raw
    assert raw["sync"] == {"page_delay": 1}
    assert raw["tagging"] == {"enabled": True}
    assert "database" not in raw
    assert "max_retries" not in raw["sync"]


def test_start_preserves_existing_password_without_resaving(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path, archive=True)
    config.web.password_hash = hashlib.sha256(b"password").hexdigest()
    _stub_start_prerequisites(monkeypatch, config, paths)
    monkeypatch.setattr(
        cli_web,
        "update_config_values",
        lambda *_args: pytest.fail("existing password should not be resaved"),
    )
    monkeypatch.setattr(
        cli_web.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=2),
    )

    result = runner.invoke(cli_web.web_app, ["start"])

    assert result.exit_code == 0
    assert "WARNING" not in result.stdout


def test_start_with_secure_password_does_not_warn_or_save(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path, archive=True)
    config.web.password_hash = hashlib.sha256(b"secure").hexdigest()
    _stub_start_prerequisites(monkeypatch, config, paths)
    monkeypatch.setattr(
        cli_web,
        "update_config_values",
        lambda *_args: pytest.fail("secure password should not be resaved"),
    )
    monkeypatch.setattr(
        cli_web.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=3),
    )

    result = runner.invoke(cli_web.web_app, ["start"])

    assert result.exit_code == 0
    assert "WARNING" not in result.stdout


def test_start_displays_device_ip_for_ipv4_wildcard(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path, archive=True)
    config.web.host = "0.0.0.0"
    config.web.password_hash = hashlib.sha256(b"secure").hexdigest()
    _stub_start_prerequisites(monkeypatch, config, paths)
    monkeypatch.setattr(
        cli_web,
        "display_web_url",
        lambda host, port: f"http://192.168.1.42:{port}" if host == "0.0.0.0" else "",
    )
    monkeypatch.setattr(
        cli_web.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=4),
    )

    result = runner.invoke(cli_web.web_app, ["start"])

    assert result.exit_code == 0
    assert "Starting web server on http://192.168.1.42:8000" in result.stdout
    assert "0.0.0.0" not in result.stdout


def test_start_reports_subprocess_failure(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path, archive=True)
    config.web.password_hash = hashlib.sha256(b"secure").hexdigest()
    _stub_start_prerequisites(monkeypatch, config, paths)
    monkeypatch.setattr(
        cli_web.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("fork failed")),
    )

    result = runner.invoke(cli_web.web_app, ["start"])

    assert result.exit_code == 1
    assert "Failed to start web server: fork failed" in result.stdout
    assert not cli_web._get_pid_file(paths.data_dir).exists()


def test_start_terminates_child_when_pid_file_cannot_be_written(
    monkeypatch, tmp_path: Path
) -> None:
    config, paths = _configured(tmp_path, archive=True)
    config.web.password_hash = hashlib.sha256(b"secure").hexdigest()
    _stub_start_prerequisites(monkeypatch, config, paths)
    process = SimpleNamespace(pid=2468)
    monkeypatch.setattr(cli_web.subprocess, "Popen", lambda *_args, **_kwargs: process)
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        cli_web.os,
        "kill",
        lambda pid, sig: signals.append((pid, sig)),
    )
    original_write_text = Path.write_text

    def write_text(path: Path, data: str, **kwargs) -> int:
        if path == cli_web._get_pid_file(paths.data_dir):
            raise OSError("read only")
        return original_write_text(path, data, **kwargs)

    monkeypatch.setattr(Path, "write_text", write_text)

    result = runner.invoke(cli_web.web_app, ["start"])

    assert result.exit_code == 1
    assert signals == [(2468, signal.SIGTERM)]
    assert "Failed to record web server PID: read only" in result.stdout
    assert not cli_web._get_pid_file(paths.data_dir).exists()


def test_hidden_serve_daemon_exits_when_web_dependencies_are_missing(
    monkeypatch,
) -> None:
    monkeypatch.setitem(sys.modules, "tweetnook.web.server", None)
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda: pytest.fail("configuration must not load without web dependencies"),
    )

    with pytest.raises(cli.typer.Exit) as exc_info:
        cli.serve_daemon_internal()

    assert exc_info.value.exit_code == 1


def test_foreground_serve_without_archive(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path)
    calls = []
    monkeypatch.setattr(cli, "load_config", lambda: (config, paths))
    monkeypatch.setattr(
        cli, "open_archive_store", lambda *_a, **_k: pytest.fail("CLI must not open storage")
    )
    monkeypatch.setattr("tweetnook.web.server.run_server", lambda *args: calls.append(args))
    result = runner.invoke(cli.app, ["serve"])
    assert result.exit_code == 0
    assert calls == [(config, paths, "0.0.0.0", 8000, None)]
    assert not paths.database_path.exists()


def test_status_reports_recorded_running_server(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path)
    config.web.host = "0.0.0.0"
    config.web.port = 9123
    pid_file = cli_web._get_pid_file(paths.data_dir)
    pid_file.write_text("123")
    monkeypatch.setattr(cli_web, "load_config", lambda: (config, paths))
    monkeypatch.setattr(cli_web, "_is_running", lambda pid: pid == 123)
    monkeypatch.setattr(
        cli_web,
        "_find_pid_by_port",
        lambda _port: pytest.fail("recorded PID is running"),
    )
    monkeypatch.setattr(
        cli_web,
        "display_web_url",
        lambda host, port: f"http://192.168.1.42:{port}" if host == "0.0.0.0" else "",
    )

    result = runner.invoke(cli_web.web_app, ["status"])

    assert result.exit_code == 0
    assert "running (PID: 123)" in result.stdout
    assert "URL: http://192.168.1.42:9123" in result.stdout
    assert "0.0.0.0" not in result.stdout


@pytest.mark.parametrize("pid_content", [None, "invalid", "-1", "123"])
def test_status_reports_stopped_and_cleans_stale_pid(
    monkeypatch, tmp_path: Path, pid_content: str | None
) -> None:
    config, paths = _configured(tmp_path)
    pid_file = cli_web._get_pid_file(paths.data_dir)
    if pid_content is not None:
        pid_file.write_text(pid_content)
    monkeypatch.setattr(cli_web, "load_config", lambda: (config, paths))
    monkeypatch.setattr(cli_web, "_is_running", lambda _pid: False)
    monkeypatch.setattr(cli_web, "_find_pid_by_port", lambda _port: None)
    monkeypatch.setattr(
        cli_web,
        "display_web_url",
        lambda host, port: f"http://192.168.1.25:{port}" if host == "0.0.0.0" else "",
    )

    result = runner.invoke(cli_web.web_app, ["status"])

    assert result.exit_code == 1
    assert "Web server is stopped." in result.stdout
    assert "Configured URL: http://192.168.1.25:8000" in result.stdout
    assert "http://0.0.0.0:8000" not in result.stdout
    assert not pid_file.exists()


def test_status_falls_back_to_running_port_process(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path)
    cli_web._get_pid_file(paths.data_dir).write_text("stale")
    monkeypatch.setattr(cli_web, "load_config", lambda: (config, paths))
    monkeypatch.setattr(cli_web, "_find_pid_by_port", lambda _port: 456)
    monkeypatch.setattr(cli_web, "_is_running", lambda pid: pid == 456)

    result = runner.invoke(cli_web.web_app, ["status"])

    assert result.exit_code == 0
    assert "running (PID: 456)" in result.stdout


def test_stop_cleans_stale_pid_without_signaling(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path)
    pid_file = cli_web._get_pid_file(paths.data_dir)
    pid_file.write_text("123")
    monkeypatch.setattr(cli_web, "load_config", lambda: (config, paths))
    monkeypatch.setattr(cli_web, "_running_pid", lambda *_args: None)
    monkeypatch.setattr(
        cli_web.os,
        "kill",
        lambda *_args: pytest.fail("stale PID must not be signaled"),
    )

    result = runner.invoke(cli_web.web_app, ["stop"])

    assert result.exit_code == 0
    assert "Web server is not running." in result.stdout
    assert not pid_file.exists()


def test_stop_sends_term_and_stops_without_waiting(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path)
    pid_file = cli_web._get_pid_file(paths.data_dir)
    pid_file.write_text("123")
    monkeypatch.setattr(cli_web, "load_config", lambda: (config, paths))
    monkeypatch.setattr(cli_web, "_running_pid", lambda *_args: 123)
    signals: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(
        cli_web.os,
        "kill",
        lambda pid, sig: signals.append((pid, sig)),
    )
    monkeypatch.setattr(cli_web, "_is_running", lambda _pid: False)
    monkeypatch.setattr(
        cli_web.time,
        "sleep",
        lambda _seconds: pytest.fail("already stopped process should not sleep"),
    )

    result = runner.invoke(cli_web.web_app, ["stop"])

    assert result.exit_code == 0
    assert signals == [(123, signal.SIGTERM)]
    assert "Server stopped." in result.stdout
    assert not pid_file.exists()


def test_stop_waits_then_force_kills_unresponsive_process(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path)
    pid_file = cli_web._get_pid_file(paths.data_dir)
    pid_file.write_text("123")
    monkeypatch.setattr(cli_web, "load_config", lambda: (config, paths))
    monkeypatch.setattr(cli_web, "_running_pid", lambda *_args: 123)
    signals: list[tuple[int, signal.Signals]] = []
    sleeps: list[float] = []
    monkeypatch.setattr(
        cli_web.os,
        "kill",
        lambda pid, sig: signals.append((pid, sig)),
    )
    monkeypatch.setattr(cli_web, "_is_running", lambda _pid: True)
    monkeypatch.setattr(cli_web.time, "sleep", sleeps.append)

    result = runner.invoke(cli_web.web_app, ["stop"])

    assert result.exit_code == 0
    assert signals == [(123, signal.SIGTERM), (123, signal.SIGKILL)]
    assert sleeps == [0.1] * (cli_web.WEB_STOP_TIMEOUT * 10) + [0.5]
    assert not pid_file.exists()


def test_stop_treats_process_lookup_race_as_success(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path)
    pid_file = cli_web._get_pid_file(paths.data_dir)
    pid_file.write_text("123")
    monkeypatch.setattr(cli_web, "load_config", lambda: (config, paths))
    monkeypatch.setattr(cli_web, "_running_pid", lambda *_args: 123)
    monkeypatch.setattr(
        cli_web.os,
        "kill",
        lambda *_args: (_ for _ in ()).throw(ProcessLookupError()),
    )

    result = runner.invoke(cli_web.web_app, ["stop"])

    assert result.exit_code == 0
    assert "Web server is not running." in result.stdout
    assert not pid_file.exists()


def test_stop_reports_signal_error_with_nonzero_exit(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path)
    pid_file = cli_web._get_pid_file(paths.data_dir)
    pid_file.write_text("123")
    monkeypatch.setattr(cli_web, "load_config", lambda: (config, paths))
    monkeypatch.setattr(cli_web, "_running_pid", lambda *_args: 123)
    monkeypatch.setattr(
        cli_web.os,
        "kill",
        lambda *_args: (_ for _ in ()).throw(PermissionError("denied")),
    )

    result = runner.invoke(cli_web.web_app, ["stop"])

    assert result.exit_code == 1
    assert "Error stopping server: denied" in result.stdout
    assert not pid_file.exists()


def test_restart_preflights_then_stops_and_starts(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path, archive=True)
    calls: list[str] = []
    monkeypatch.setattr(
        cli_web,
        "_require_web_dependencies",
        lambda _console: calls.append("dependencies"),
    )
    monkeypatch.setattr(cli_web, "load_config", lambda: (config, paths))
    monkeypatch.setattr(cli_web, "stop_web", lambda: calls.append("stop"))
    monkeypatch.setattr(cli_web, "start_web", lambda: calls.append("start"))

    result = runner.invoke(cli_web.web_app, ["restart"])

    assert result.exit_code == 0
    assert calls == ["dependencies", "stop", "start"]


def test_restart_works_without_archive(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path)
    calls = []
    monkeypatch.setattr(cli_web, "_require_web_dependencies", lambda _console: None)
    monkeypatch.setattr(cli_web, "load_config", lambda: (config, paths))
    monkeypatch.setattr(cli_web, "stop_web", lambda: calls.append("stop"))
    monkeypatch.setattr(cli_web, "start_web", lambda: calls.append("start"))
    result = runner.invoke(cli_web.web_app, ["restart"])
    assert result.exit_code == 0
    assert calls == ["stop", "start"]


def test_set_password_hashes_and_persists_value(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path)
    saved: list[tuple[XDGPaths, dict[str, str]]] = []
    monkeypatch.setattr(cli_web, "load_config", lambda: (config, paths))
    monkeypatch.setattr(cli_web.typer, "prompt", lambda *_args, **_kwargs: "correct horse")
    monkeypatch.setattr(
        cli_web,
        "update_config_values",
        lambda actual_paths, changes: saved.append((actual_paths, changes)),
    )

    result = runner.invoke(cli_web.web_app, ["set-password"])

    assert result.exit_code == 0
    expected_hash = hashlib.sha256(b"correct horse").hexdigest()
    assert saved == [(paths, {"web.password_hash": expected_hash})]
    assert "Password updated in config.toml." in result.stdout
    assert "must restart" in result.stdout


def test_set_password_updates_only_hash_and_preserves_sparse_values(
    monkeypatch, tmp_path: Path
) -> None:
    config, paths = _configured(tmp_path)
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text(
        """
[auth]
auth_token = ""
ct0 = ""
user_id = ""

[database]
cache_size_kb = 64
""".lstrip(),
        encoding="utf-8",
    )
    config.sync.page_delay = 7  # Effective-only value must not be persisted.
    monkeypatch.setattr(cli_web, "load_config", lambda: (config, paths))
    monkeypatch.setattr(cli_web.typer, "prompt", lambda *_args, **_kwargs: "new secret")

    result = runner.invoke(cli_web.web_app, ["set-password"])

    assert result.exit_code == 0
    raw = tomllib.loads(paths.config_file.read_text(encoding="utf-8"))
    assert raw["database"] == {"cache_size_kb": 64}
    assert raw["web"] == {"password_hash": hashlib.sha256(b"new secret").hexdigest()}
    assert "sync" not in raw


def test_set_password_rejects_empty_value(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path)
    monkeypatch.setattr(cli_web, "load_config", lambda: (config, paths))
    monkeypatch.setattr(cli_web.typer, "prompt", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        cli_web,
        "update_config_values",
        lambda *_args: pytest.fail("empty password must not save"),
    )

    result = runner.invoke(cli_web.web_app, ["set-password"])

    assert result.exit_code == 1
    assert "Password cannot be empty." in result.stdout


def test_set_password_uses_hidden_confirmation_prompt(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path)
    calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(cli_web, "load_config", lambda: (config, paths))

    def prompt(*args, **kwargs):
        calls.append((args, kwargs))
        return "secret"

    monkeypatch.setattr(cli_web.typer, "prompt", prompt)
    monkeypatch.setattr(cli_web, "update_config_values", lambda *_args: None)

    result = runner.invoke(cli_web.web_app, ["set-password"])

    assert result.exit_code == 0
    assert calls == [
        (
            ("Enter new web password",),
            {"hide_input": True, "confirmation_prompt": True},
        )
    ]


def test_set_password_reports_save_failure(monkeypatch, tmp_path: Path) -> None:
    config, paths = _configured(tmp_path)
    monkeypatch.setattr(cli_web, "load_config", lambda: (config, paths))
    monkeypatch.setattr(cli_web.typer, "prompt", lambda *_args, **_kwargs: "secret")
    monkeypatch.setattr(
        cli_web,
        "update_config_values",
        lambda *_args: (_ for _ in ()).throw(OSError("read only")),
    )

    result = runner.invoke(cli_web.web_app, ["set-password"])

    assert result.exit_code == 1
    assert "Failed to save password: read only" in result.stdout


def test_web_cli_help_lists_daemon_commands_and_descriptions() -> None:
    result = runner.invoke(cli_web.web_app, ["--help"])

    assert result.exit_code == 0
    assert "Manage the background web UI server." in result.stdout
    assert "start" in result.stdout
    assert "Start the background web server." in result.stdout
    assert "stop" in result.stdout
    assert "Stop the background web server." in result.stdout
    assert "restart" in result.stdout
    assert "Restart the background web server." in result.stdout
    assert "status" in result.stdout
    assert "Check if the background web server is running." in result.stdout
    assert "set-password" in result.stdout
    assert "Set the password for the web server." in result.stdout


@pytest.mark.parametrize("command", ["start", "stop", "restart", "status", "set-password"])
def test_web_subcommand_help_exits_successfully(command: str) -> None:
    result = runner.invoke(cli_web.web_app, [command, "--help"])

    assert result.exit_code == 0
    assert "--help" in result.stdout
