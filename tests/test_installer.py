"""Exercise the standalone installer without network, sudo, or a real archive."""

from __future__ import annotations

import json
import os
import pty
import select
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"

FAKE_PYTHON = """\
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ['TEST_EVENTS'], 'a') as log:
    log.write(json.dumps(['python', sys.argv[0], *args]) + '\\n')
if args[:1] == ['-I']:
    args = args[1:]
root = Path(sys.argv[0]).parent.parent
if args[:1] == ['-c']:
    if 'importlib.metadata' in args[1]:
        state = root / 'installed-version'
        if not state.exists():
            sys.exit(1)
        print(state.read_text())
    elif 'tweetnook.system_install' in args[1]:
        sys.exit(1 if os.environ.get('TEST_OLD_PACKAGE') else 0)
    elif 'sys.prefix' not in args[1]:
        raise AssertionError(args)
elif args[:2] == ['-m', 'pip']:
    if '--version' in args:
        sys.exit(0 if (root / 'pip-ready').exists() else 1)
    if os.environ.get('TEST_FAIL_INSTALL'):
        sys.exit(9)
    version = args[-1].split('==')[-1] if '==' in args[-1] else '9.8.7'
    (root / 'installed-version').write_text(version)
elif args[:1] == ['-m'] and args[1] in ('tweetnook', 'tweetnook.system_install'):
    if args[2:] == ['--version']:
        print('tweetnook ' + (root / 'installed-version').read_text())
    elif args[2:] == ['--help']:
        sys.exit(5 if os.environ.get('TEST_FAIL_HELP') else 0)
    elif args[2:4] != ['service', 'install'] and args[2:] not in (
        ['serve'], ['update'], ['service', 'restart']
    ):
        raise AssertionError(args)
elif args == ['-']:
    sys.prefix = str(root)
    exec(compile(sys.stdin.read(), '<installer-check>', 'exec'))
else:
    raise AssertionError(args)
"""

FAKE_UV = """\
import json
import os
import shutil
import sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ['TEST_EVENTS'], 'a') as log:
    log.write(json.dumps(['uv', *args, os.environ.get('UV_PYTHON_INSTALL_DIR')]) + '\\n')
if 'venv' in args:
    root = Path(args[-1])
    root.mkdir(exist_ok='--allow-existing' in args)
    if os.environ.get('TEST_FAIL_SEED'):
        sys.exit(8)
    (root / 'bin').mkdir()
    (root / 'pyvenv.cfg').write_text('home = fake\\n')
    shutil.copy(os.environ['TEST_PYTHON'], root / 'bin/python')
    (root / 'pip-ready').touch()
elif 'pip' in args:
    python = Path(args[args.index('--python') + 1])
    (python.parent.parent / 'pip-ready').touch()
else:
    raise AssertionError(args)
"""


def executable(path: Path, body: str, *, python: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((f"#!{sys.executable}\n" if python else "#!/bin/bash\n") + body)
    path.chmod(0o755)
    return path


@pytest.fixture
def installation(tmp_path):
    home = tmp_path / "home with spaces 'quote' $literal"
    home.mkdir()
    commands = tmp_path / "commands"
    commands.mkdir()
    events = tmp_path / "events.jsonl"
    fake_python = executable(tmp_path / "fake-python", FAKE_PYTHON, python=True)
    fake_uv = executable(commands / "uv", FAKE_UV, python=True)
    executable(commands / "uname", 'printf "%s\\n" "${TEST_OS:-Darwin}"\n')
    executable(
        commands / "id",
        'if [[ $1 == -u ]]; then echo "${TEST_UID:-1000}"; else echo archiver; fi\n',
    )
    executable(
        commands / "systemctl",
        """\
if [[ $* == *LoadState* ]]; then echo "${TEST_UNIT_STATE:-not-found}"
elif [[ $* == *Version* ]]; then
    [[ ${TEST_SYSTEMD:-no} == yes ]] || exit 1
    echo 256
elif [[ $1 == is-active ]]; then exit "${TEST_SERVICE_EXIT:-0}"
else exit 99
fi
""",
    )
    executable(
        commands / "sudo",
        'printf "sudo\\n" >> "$TEST_ADMIN_EVENTS"\nexec env TEST_UID=0 "$@"\n',
    )
    executable(
        commands / "getent",
        """\
if [[ ${TEST_ACCOUNT:-} ]]; then printf '%s\\n' "$TEST_ACCOUNT"
elif [[ -f $TEST_ACCOUNT_CREATED ]]; then
    printf 'tweetnook:x:999:999::/var/lib/tweetnook:%s\\n' "$(command -v nologin)"
else exit 2
fi
""",
    )
    executable(
        commands / "useradd",
        'printf "useradd %s\\n" "$*" >> "$TEST_ADMIN_EVENTS"\ntouch "$TEST_ACCOUNT_CREATED"\n',
    )
    executable(
        commands / "install",
        'printf "install %s\\n" "$*" >> "$TEST_ADMIN_EVENTS"\n',
    )
    executable(commands / "nologin", "exit 1\n")
    executable(
        commands / "stat",
        """\
if [[ $2 == %u ]]; then
    case "$3" in
        /var/lib/tweetnook|/etc/tweetnook|/var/cache/tweetnook) echo 999 ;;
        *)
            if [[ $3 == "${TEST_UNSAFE_PARENT:-}" ]]; then
                echo "${TEST_OWNER:-0}"
            else echo 0
            fi ;;
    esac
elif [[ $2 == %a ]]; then
    if [[ $3 == "${TEST_UNSAFE_PARENT:-}" ]]; then echo "${TEST_MODE:-755}"; else echo 755; fi
else exit 99
fi
""",
    )
    executable(commands / "ps", 'printf "%s\\n" "${TEST_PROCESSES:-}"\n')
    env = {
        "HOME": str(home),
        "PATH": f"{commands}{os.pathsep}{os.defpath}",
        "TEST_EVENTS": str(events),
        "TEST_PYTHON": str(fake_python),
        "TEST_UV": str(fake_uv),
        "TEST_ADMIN_EVENTS": str(tmp_path / "admin-events"),
        "TEST_ACCOUNT_CREATED": str(tmp_path / "account-created"),
    }

    class Installation:
        runtime = home / ".local/share/tweetnook-runtime"
        launcher = home / ".local/bin/tweetnook"
        system_runtime = tmp_path / "system runtime"
        system_bin = tmp_path / "system bin"

        def system_args(self, args, changes):
            # Keep system runtime/launcher writes inside this sandbox. Account and
            # state-directory provisioning commands are fakes, never host mutations.
            if changes.get("TEST_OS") == "Linux" and changes.get("TEST_SYSTEMD") == "yes":
                if "--no-service" not in args:
                    if "--install-dir" not in args:
                        args = (*args, "--install-dir", str(self.system_runtime))
                    if "--bin-dir" not in args:
                        args = (*args, "--bin-dir", str(self.system_bin))
            return args

        def run(self, *args, **changes):
            args = self.system_args(args, changes)
            return subprocess.run(
                ["/bin/bash", str(INSTALLER), *args],
                env={**env, **changes},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=20,
                cwd=tmp_path,
            )

        def interactive(self, choice, **changes):
            master, slave = pty.openpty()
            process = None
            try:
                process = subprocess.Popen(
                    ["/bin/bash", str(INSTALLER), *self.system_args((), changes)],
                    env={**env, **changes},
                    stdin=slave,
                    stdout=slave,
                    stderr=slave,
                    cwd=tmp_path,
                )
                os.close(slave)
                slave = None
                os.write(master, choice.encode())
                output = b""
                deadline = time.monotonic() + 20
                while True:
                    readable, _, _ = select.select(
                        [master], [], [], max(0, deadline - time.monotonic())
                    )
                    if not readable:
                        raise AssertionError(f"Interactive installer timed out: {output.decode()}")
                    try:
                        chunk = os.read(master, 4096)
                    except OSError:
                        break  # The terminal reports EIO when its slave closes on Linux.
                    if not chunk:
                        break
                    output += chunk
                return process.wait(timeout=2), output.decode()
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait()
                os.close(master)
                if slave is not None:
                    os.close(slave)

        def events(self):
            return [json.loads(line) for line in events.read_text().splitlines()]

        def pip_installs(self):
            return [e for e in self.events() if e[0] == "python" and "install" in e and "pip" in e]

        def admin_events(self):
            path = Path(env["TEST_ADMIN_EVENTS"])
            return path.read_text().splitlines() if path.exists() else []

    return Installation()


def test_help_is_available_without_python_or_root_permissions(installation):
    result = installation.run("--help", TEST_UID="0")
    assert result.returncode == 0
    for flag in (
        "--service",
        "--no-service",
        "--automated-tagging",
        "--legacy-migration",
        "--version",
        "--python",
        "--install-dir",
        "--bin-dir",
    ):
        assert flag in result.stdout
    assert "Linux with running systemd defaults to a system-wide service" in result.stdout
    for path in ("/opt/tweetnook", "/usr/local/bin", "/var/lib/tweetnook", "/etc/tweetnook"):
        assert path in result.stdout
    assert "These defaults also apply without a terminal" in result.stdout
    assert not installation.runtime.exists()


@pytest.mark.parametrize(
    ("args", "changes", "message"),
    [
        (["--unknown"], {}, "Unknown option"),
        (["--version"], {}, "requires a value"),
        (["--version", "--service"], {}, "requires a value"),
        (["--version", "package @ https://example.org"], {}, "version number"),
        (["--install-dir", "relative"], {}, "absolute directories"),
        (["--service", "--no-service"], {}, "Choose only one"),
        ([], {"TEST_UID": "0"}, "without sudo"),
        (["--no-service"], {"TEST_OS": "Linux", "TEST_UID": "0"}, "without sudo"),
        ([], {"TEST_OS": "MINGW64_NT"}, "macOS and Linux"),
        (["--service"], {}, "running systemd"),
        (["--service"], {"TEST_OS": "Linux"}, "running systemd"),
        ([], {"TEST_OS": "Linux", "TEST_UNIT_STATE": "loaded"}, "unit already exists"),
        ([], {"TEST_OS": "Linux", "TEST_UNIT_STATE": "masked"}, "unit already exists"),
    ],
)
def test_preflight_errors_leave_runtime_untouched(installation, args, changes, message):
    result = installation.run(*args, **changes)
    assert result.returncode != 0
    assert message in result.stderr
    assert not installation.runtime.exists()


def test_fresh_manual_install_rerun_and_launcher_preserve_data(installation):
    archive = installation.runtime.parent / "tweetnook/archive.db"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"existing archive")
    result = installation.run()
    assert result.returncode == 0, result.stderr
    assert "tweetnook 9.8.7" in result.stdout
    assert "To start TweetNook" in result.stdout
    assert "service is running" not in result.stdout
    assert "export PATH=" in result.stdout
    assert "--upgrade" in result.stdout
    creation = next(e for e in installation.events() if e[0] == "uv")
    assert "--seed" in creation
    assert "--no-project" in creation
    assert creation[-1] == str(installation.runtime / "python")
    assert installation.pip_installs()[0][-1] == "tweetnook"
    assert "--upgrade" not in installation.pip_installs()[0]
    assert archive.read_bytes() == b"existing archive"
    result = installation.run("--no-service")
    assert result.returncode == 0, result.stderr
    assert len(installation.pip_installs()) == 1
    assert len([e for e in installation.events() if e[0] == "uv"]) == 1
    # Run the generated launcher, including literal quotes, dollars, and spaces in its path.
    result = subprocess.run(
        [str(installation.launcher), "serve"],
        env={"PATH": os.defpath, "TEST_EVENTS": str(archive.parent / "launcher-events")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not (installation.runtime / ".install-lock").exists()


@pytest.mark.parametrize("choice", ["\n", "1\n", "2\n"])
def test_interactive_linux_startup_choice(installation, choice):
    code, output = installation.interactive(choice, TEST_OS="Linux", TEST_SYSTEMD="yes")
    assert code == 0, output
    assert "How should TweetNook run?" in output
    assert "Choose [2]:" in output
    assert ("service is running" in output) == (choice != "1\n")
    assert ("To start TweetNook" in output) == (choice == "1\n")


@pytest.mark.parametrize(
    ("platform", "service_expected"),
    [
        ({}, False),
        ({"TEST_OS": "Linux"}, False),
        ({"TEST_OS": "Linux", "TEST_SYSTEMD": "yes"}, True),
    ],
)
def test_unattended_platform_default(installation, platform, service_expected):
    result = installation.run(**platform)
    assert result.returncode == 0, result.stderr
    assert ("service is running" in result.stdout) == service_expected
    assert any("service" in event for event in installation.events()) == service_expected


def test_no_service_overrides_linux_service_default(installation):
    result = installation.run("--no-service", TEST_OS="Linux", TEST_SYSTEMD="yes")
    assert result.returncode == 0, result.stderr
    assert "To start TweetNook" in result.stdout
    assert not any("service" in event for event in installation.events())


def test_interactive_service_selection_on_macos_is_rejected(installation):
    code, output = installation.interactive("2\n")
    assert code != 0
    assert "requires Linux with running systemd" in output
    assert not installation.runtime.exists()


def test_extras_exact_version_and_custom_directories(installation, tmp_path):
    runtime = tmp_path / "custom runtime"
    binary = tmp_path / "custom bin"
    result = installation.run(
        "--no-service",
        "--automated-tagging",
        "--legacy-migration",
        "--version",
        "1.2.3rc1",
        "--python",
        "/custom/python3.12",
        "--install-dir",
        str(runtime),
        "--bin-dir",
        str(binary),
    )
    assert result.returncode == 0, result.stderr
    assert installation.pip_installs()[0][-1] == (
        "tweetnook[automated-tagging,legacy-migration]==1.2.3rc1"
    )
    assert "/custom/python3.12" in installation.events()[0]
    assert (binary / "tweetnook").is_file()


@pytest.mark.parametrize("kind", ["file", "symlink", "other-path"])
def test_existing_commands_are_not_replaced(installation, tmp_path, kind):
    existing = installation.launcher
    existing.parent.mkdir(parents=True)
    if kind == "symlink":
        existing.symlink_to(tmp_path / "missing-target")
    elif kind == "file":
        existing.write_text("keep this command")
    else:
        existing = executable(tmp_path / "other-bin/tweetnook", "exit 0\n")
    changes = {"PATH": f"{existing.parent}{os.pathsep}{os.defpath}"} if kind == "other-path" else {}
    result = installation.run("--no-service", **changes)
    assert result.returncode != 0
    assert "existing command" in result.stderr or "already on PATH" in result.stderr
    assert not installation.runtime.exists()
    assert existing.is_symlink() if kind == "symlink" else existing.is_file()


def test_unmanaged_runtime_is_preserved(installation):
    installation.runtime.mkdir(parents=True)
    sentinel = installation.runtime / "keep"
    sentinel.write_text("untouched")
    result = installation.run("--no-service")
    assert result.returncode != 0
    assert "unmanaged runtime" in result.stderr
    assert sentinel.read_text() == "untouched"


def test_symlinked_environment_is_not_followed(installation, tmp_path):
    assert installation.run("--no-service").returncode == 0
    original = installation.runtime / "venv"
    target = tmp_path / "original-environment"
    original.rename(target)
    original.symlink_to(target, target_is_directory=True)
    result = installation.run("--no-service")
    assert result.returncode != 0
    assert "environment must not be a symbolic link" in result.stderr
    assert (target / "installed-version").read_text() == "9.8.7"


def test_package_failure_does_not_publish_launcher_and_can_be_retried(installation):
    result = installation.run("--no-service", TEST_FAIL_INSTALL="yes")
    assert result.returncode != 0
    assert not installation.launcher.exists()
    assert not (installation.runtime / ".install-lock").exists()
    result = installation.run("--no-service")
    assert result.returncode == 0, result.stderr
    assert installation.launcher.is_file()


def test_failed_cli_smoke_check_does_not_publish_launcher(installation):
    result = installation.run("--no-service", TEST_FAIL_HELP="yes")
    assert result.returncode != 0
    assert not installation.launcher.exists()


def test_existing_environment_can_restore_missing_pip(installation):
    assert installation.run("--no-service").returncode == 0
    (installation.runtime / "venv/pip-ready").unlink()
    result = installation.run("--no-service")
    assert result.returncode == 0, result.stderr
    assert (installation.runtime / "venv/pip-ready").exists()
    assert "pip" in next(e for e in installation.events() if e[:3] == ["uv", "--no-config", "pip"])


def test_version_change_requires_deliberate_upgrade(installation):
    assert installation.run("--no-service").returncode == 0
    result = installation.run("--no-service", "--version", "1.0.0")
    assert result.returncode != 0
    assert "Stop it and upgrade explicitly" in result.stderr
    assert len(installation.pip_installs()) == 1


@pytest.mark.parametrize("running", [True, False])
def test_adding_extras_requires_stopped_processes_in_this_environment(installation, running):
    assert installation.run("--no-service").returncode == 0
    python = (
        installation.runtime / "venv/bin/python" if running else Path("/another/env/bin/python")
    )
    result = installation.run(
        "--no-service",
        "--automated-tagging",
        TEST_PROCESSES=f"1234 {python} -I -m tweetnook serve",
    )
    if running:
        assert result.returncode != 0
        assert "Stop TweetNook processes" in result.stderr
        assert len(installation.pip_installs()) == 1
    else:
        assert result.returncode == 0, result.stderr
        assert installation.pip_installs()[-1][-1] == "tweetnook[automated-tagging]"


def test_installer_lock_prevents_overlapping_runs(installation):
    assert installation.run("--no-service").returncode == 0
    lock = installation.runtime / ".install-lock"
    lock.mkdir()
    result = installation.run("--no-service")
    assert result.returncode != 0
    assert "Another installer" in result.stderr
    assert lock.is_dir()


def test_interrupted_environment_creation_resumes_without_deleting_files(installation):
    result = installation.run("--no-service", TEST_FAIL_SEED="yes")
    assert result.returncode != 0
    sentinel = installation.runtime / "venv/keep"
    sentinel.write_text("diagnostic data")
    result = installation.run("--no-service")
    assert result.returncode == 0, result.stderr
    assert sentinel.read_text() == "diagnostic data"
    assert installation.launcher.is_file()


@pytest.mark.parametrize("uid", ["0", "1000"])
def test_linux_system_service_uses_dedicated_account_and_paths(installation, tmp_path, uid):
    homes = {
        f"XDG_{kind}_HOME": str(tmp_path / f"custom {kind}") for kind in ("DATA", "CONFIG", "CACHE")
    }
    bin_dir = tmp_path / "custom command directory"
    result = installation.run(
        "--service",
        "--bin-dir",
        str(bin_dir),
        TEST_OS="Linux",
        TEST_SYSTEMD="yes",
        TEST_UID=uid,
        **homes,
    )
    assert result.returncode == 0, result.stderr
    service = next(e for e in installation.events() if "service" in e)
    assert service[1] == str(installation.system_runtime / "venv/bin/python")
    assert service[2:7] == ["-I", "-m", "tweetnook", "service", "install"]
    for kind, option in (
        ("/var/lib", "--data-home"),
        ("/etc", "--config-home"),
        ("/var/cache", "--cache-home"),
    ):
        assert service[service.index(option) + 1] == kind
    assert service[service.index("--user") + 1] == "tweetnook"
    admin = installation.admin_events()
    assert ("sudo" in admin) == (uid != "0")
    assert sum(event.startswith("useradd ") for event in admin) == 1
    for path in ("/var/lib/tweetnook", "/etc/tweetnook", "/var/cache/tweetnook"):
        assert f"install -d -m 750 -o 999 -g 999 {path}" in admin
    assert (
        installation.system_runtime / ".tweetnook-installer"
    ).read_text().strip() == "tweetnook-system-installer-v1"
    assert "service is running" in result.stdout
    commands = [
        shlex.split(line.strip())
        for line in result.stdout.splitlines()
        if line.startswith("  tweetnook ")
    ]
    assert commands == [["tweetnook", "update"], ["tweetnook", "service", "restart"]]
    # Exercise both printed commands through PATH, including a custom bin directory.
    for index, command in enumerate(commands):
        events_path = tmp_path / f"command-{index}.jsonl"
        subprocess.run(
            command,
            env={"PATH": f"{bin_dir}{os.pathsep}{os.defpath}", "TEST_EVENTS": str(events_path)},
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        event = json.loads(events_path.read_text())
        assert event[1] == service[1]
        assert event[2:] == ["-I", "-m", "tweetnook.system_install", *command[1:]]


def test_failed_service_activation_does_not_claim_running(installation):
    result = installation.run(
        "--service", TEST_OS="Linux", TEST_SYSTEMD="yes", TEST_SERVICE_EXIT="1"
    )
    assert result.returncode != 0
    assert "did not remain active" in result.stderr
    assert "service is running" not in result.stdout


def test_service_install_refuses_existing_personal_environment(installation):
    assert installation.run("--no-service").returncode == 0
    python = installation.runtime / "venv/bin/python"
    result = installation.run(
        "--service",
        TEST_OS="Linux",
        TEST_SYSTEMD="yes",
        TEST_PROCESSES=f"1234 {python} -I -m tweetnook serve",
    )
    assert result.returncode != 0
    assert "existing personal installation" in result.stderr
    assert not any("service" in event for event in installation.events())


def test_root_service_install_does_not_require_sudo_or_home(installation, tmp_path):
    (tmp_path / "commands/sudo").unlink()
    result = installation.run(TEST_OS="Linux", TEST_SYSTEMD="yes", TEST_UID="0", HOME="")
    assert result.returncode == 0, result.stderr
    assert "service is running as tweetnook" in result.stdout
    assert "sudo" not in installation.admin_events()
    assert (installation.system_bin / "tweetnook").is_file()


@pytest.mark.parametrize("uid", ["0", "1000"])
def test_existing_service_is_never_migrated_or_reinstalled(installation, uid):
    result = installation.run(
        "--service", TEST_OS="Linux", TEST_SYSTEMD="yes", TEST_UID=uid, TEST_UNIT_STATE="loaded"
    )
    assert result.returncode != 0
    assert "Use tweetnook update" in result.stderr
    assert not installation.system_runtime.exists()
    assert not installation.admin_events()


def test_older_package_cannot_publish_system_launcher(installation):
    result = installation.run(
        "--service", TEST_OS="Linux", TEST_SYSTEMD="yes", TEST_UID="0", TEST_OLD_PACKAGE="yes"
    )
    assert result.returncode != 0
    assert "does not support system-wide installation" in result.stderr
    assert not installation.admin_events()
    assert not (installation.system_bin / "tweetnook").exists()


@pytest.mark.parametrize(
    "account",
    ["tweetnook:x:0:0::/var/lib/tweetnook:{shell}", "tweetnook:x:999:999::/home/existing:{shell}"],
)
def test_existing_incompatible_account_is_not_changed(installation, tmp_path, account):
    result = installation.run(
        "--service",
        TEST_OS="Linux",
        TEST_SYSTEMD="yes",
        TEST_UID="0",
        TEST_ACCOUNT=account.format(shell=tmp_path / "commands/nologin"),
    )
    assert result.returncode != 0
    assert "incompatible tweetnook account" in result.stderr
    assert not installation.admin_events()
    assert not (installation.system_bin / "tweetnook").exists()


def test_interrupted_system_install_reuses_compatible_account(installation, tmp_path):
    result = installation.run(
        "--service",
        TEST_OS="Linux",
        TEST_SYSTEMD="yes",
        TEST_UID="0",
        TEST_ACCOUNT=f"tweetnook:x:999:999::/var/lib/tweetnook:{tmp_path / 'commands/nologin'}",
    )
    assert result.returncode == 0, result.stderr
    assert not any(event.startswith("useradd ") for event in installation.admin_events())


def test_account_creation_failure_does_not_publish_command(installation, tmp_path):
    executable(tmp_path / "commands/useradd", "exit 17\n")
    result = installation.run("--service", TEST_OS="Linux", TEST_SYSTEMD="yes", TEST_UID="0")
    assert result.returncode == 17
    assert not (installation.system_bin / "tweetnook").exists()
    assert not (installation.system_runtime / ".install-lock").exists()


@pytest.mark.parametrize("unsafe", [{"TEST_OWNER": "1000"}, {"TEST_MODE": "775"}])
def test_system_install_refuses_user_replaceable_runtime_paths(installation, tmp_path, unsafe):
    result = installation.run(
        "--service",
        TEST_OS="Linux",
        TEST_SYSTEMD="yes",
        TEST_UID="0",
        TEST_UNSAFE_PARENT=str(tmp_path),
        **unsafe,
    )
    assert result.returncode != 0
    assert "root-owned and not writable by other users" in result.stderr
    assert not installation.system_runtime.exists()
    assert not (installation.system_bin / "tweetnook").exists()
    assert not installation.admin_events()


def test_uv_bootstrap_is_private_and_does_not_edit_profiles(installation, tmp_path):
    # Mask the uv on PATH and emulate the official download without network access.
    commands = tmp_path / "commands"
    uv = commands / "uv"
    private_uv = tmp_path / "bootstrap-uv"
    uv.rename(private_uv)
    installer = tmp_path / "download.sh"
    installer.write_text(
        'mkdir -p "$UV_UNMANAGED_INSTALL"\n'
        f'cp {shlex.quote(str(private_uv))} "$UV_UNMANAGED_INSTALL/uv"\n'
    )
    executable(
        commands / "curl",
        f'cp {shlex.quote(str(installer))} "${{@:$#}}"\n',
    )
    result = installation.run("--no-service")
    assert result.returncode == 0, result.stderr
    assert (installation.runtime / "bootstrap/uv").exists()
    assert not (installation.launcher.parent.parent.parent / ".profile").exists()
