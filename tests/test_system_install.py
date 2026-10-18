"""Privilege and archive routing for the new global system launcher only."""

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tweetnook import system_install as system


class ExecCalled(BaseException):
    pass


@pytest.mark.skipif(os.geteuid() == 0, reason="real exec smoke uses the current non-root account")
def test_real_exec_uses_service_paths_without_caller_credentials(tmp_path):
    # Substitute only the account lookup/state roots, then actually exec the CLI
    # with its installed interpreter. No privilege changes or host state writes.
    code = """
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from tweetnook import system_install as system

root = Path(sys.argv[1])
account = SimpleNamespace(pw_name='tweetnook', pw_uid=os.geteuid(),
                          pw_gid=os.getegid(), pw_dir=system.SERVICE_HOME)
system.pwd.getpwnam = lambda _: account
system.SERVICE_ENVIRONMENT.update(
    HOME=str(root / 'service/home'),
    XDG_DATA_HOME=str(root / 'service/data'),
    XDG_CONFIG_HOME=str(root / 'service/config'),
    XDG_CACHE_HOME=str(root / 'service/cache'),
)
sys.argv = ['tweetnook', 'stats']
system.main()
"""
    caller = tmp_path / "caller"
    # A file in the current directory must not shadow the installed package.
    (tmp_path / "tweetnook.py").write_text("raise RuntimeError('cwd package shadow')")
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(tmp_path)],
        cwd=tmp_path,
        env={
            **os.environ,
            "HOME": str(caller),
            "XDG_CONFIG_HOME": str(caller / "config"),
            "XDG_DATA_HOME": str(caller / "data"),
            "XDG_CACHE_HOME": str(caller / "cache"),
            "TWEETNOOK_AUTH_TOKEN": "synthetic-caller-token",
            "GEMINI_API_KEY": "synthetic-caller-key",
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1, result.stderr
    assert "No local archive found" in result.stderr
    assert not caller.exists()
    config = tmp_path / "service/config/tweetnook/config.toml"
    assert config.is_file()
    assert "synthetic-caller" not in config.read_text()
    assert not (tmp_path / "service/data/tweetnook/archive.db").exists()


@pytest.fixture
def routing(monkeypatch):
    account = SimpleNamespace(
        pw_name="tweetnook", pw_uid=999, pw_gid=998, pw_dir="/var/lib/tweetnook"
    )
    monkeypatch.setattr(system.pwd, "getpwnam", lambda _: account)
    monkeypatch.setattr(system.sys, "executable", "/opt/custom runtime/venv/bin/python")
    calls = []

    def record_exec(*args):
        calls.append(("exec", *args))
        raise ExecCalled

    for method in ("execv", "execve"):
        monkeypatch.setattr(system.os, method, record_exec)
    for method in ("initgroups", "setgid", "setuid"):
        monkeypatch.setattr(
            system.os, method, lambda *args, method=method: calls.append((method, *args))
        )
    return account, calls


@pytest.mark.parametrize("uid", [0, 999])
def test_archive_command_uses_service_identity_and_clean_environment(monkeypatch, routing, uid):
    _, calls = routing
    monkeypatch.setattr(system.os, "geteuid", lambda: uid)
    monkeypatch.setattr(system.sys, "argv", ["tweetnook", "stats", "--detailed"])
    monkeypatch.setenv("XDG_DATA_HOME", "/root/wrong-archive")
    monkeypatch.setenv("TWEETNOOK_AUTH_TOKEN", "synthetic-caller-token")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-caller-key")
    monkeypatch.setenv("PYTHONPATH", "/caller/code")
    monkeypatch.setenv("TERM", "xterm-256color")
    with pytest.raises(ExecCalled):
        system.main()
    assert calls[:-1] == (
        [("initgroups", "tweetnook", 998), ("setgid", 998), ("setuid", 999)] if uid == 0 else []
    )
    _, executable, arguments, environment = calls[-1]
    assert executable == "/opt/custom runtime/venv/bin/python"
    assert arguments == [executable, "-I", "-m", "tweetnook", "stats", "--detailed"]
    assert environment.items() >= system.SERVICE_ENVIRONMENT.items()
    assert environment["TERM"] == "xterm-256color"
    assert not {"TWEETNOOK_AUTH_TOKEN", "GEMINI_API_KEY", "PYTHONPATH"} & environment.keys()


def test_other_user_requests_sudo_once_before_dropping_identity(monkeypatch, routing):
    _, calls = routing
    monkeypatch.setattr(system.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(system.shutil, "which", lambda _: "/usr/bin/sudo")
    monkeypatch.setattr(
        system.sys, "argv", ["tweetnook", "export", "json", "--out", "my file.json"]
    )
    with pytest.raises(ExecCalled):
        system.main()
    assert calls == [
        (
            "exec",
            "/usr/bin/sudo",
            [
                "/usr/bin/sudo",
                system.sys.executable,
                "-I",
                "-m",
                "tweetnook.system_install",
                "export",
                "json",
                "--out",
                "my file.json",
            ],
        )
    ]


@pytest.mark.parametrize("uid", [0, 1000])
@pytest.mark.parametrize("arguments", [["update"], ["service", "restart"], ["service", "status"]])
def test_management_uses_existing_cli_without_account_or_path_changes(
    monkeypatch, routing, uid, arguments
):
    _, calls = routing
    monkeypatch.setattr(system.os, "geteuid", lambda: uid)
    monkeypatch.setattr(system.sys, "argv", ["tweetnook", *arguments])
    monkeypatch.setattr(system.pwd, "getpwnam", lambda _: pytest.fail("must use recorded service"))
    with pytest.raises(ExecCalled):
        system.main()
    assert calls == [
        (
            "exec",
            system.sys.executable,
            [system.sys.executable, "-I", "-m", "tweetnook", *arguments],
        )
    ]


@pytest.mark.parametrize(
    "problem", ["missing-user", "root-user", "wrong-home", "no-sudo", "drop-fails"]
)
def test_routing_failures_never_launch_archive_as_root(monkeypatch, routing, capsys, problem):
    account, calls = routing
    monkeypatch.setattr(system.sys, "argv", ["tweetnook", "stats"])
    monkeypatch.setattr(system.os, "geteuid", lambda: 0)
    if problem == "missing-user":

        def missing(_):
            raise KeyError("tweetnook")

        monkeypatch.setattr(system.pwd, "getpwnam", missing)
    elif problem == "root-user":
        account.pw_uid = 0
    elif problem == "wrong-home":
        account.pw_dir = "/root"
    elif problem == "no-sudo":
        monkeypatch.setattr(system.os, "geteuid", lambda: 1000)
        monkeypatch.setattr(system.shutil, "which", lambda _: None)
    else:

        def denied(_):
            raise PermissionError("setuid denied")

        monkeypatch.setattr(system.os, "setuid", denied)
    with pytest.raises(SystemExit) as error:
        system.main()
    assert error.value.code == 1
    assert not any(call[0] == "exec" for call in calls)
    assert "system command failed" in capsys.readouterr().err
