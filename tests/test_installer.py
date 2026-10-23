"""Exercise the standalone installer without network, sudo, or a real archive."""

from __future__ import annotations

import json
import os
import pty
import select
import shlex
import signal
import subprocess
import sys
import time
import tomllib
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
    if 'find_spec' in args[1]:
        extra = 'automated-tagging' if 'google.genai' in args[2:] else 'legacy-migration'
        extras = (root / 'extras').read_text().split(',') if (root / 'extras').exists() else []
        sys.exit(0 if extra in extras else 1)
    elif 'importlib.metadata' in args[1]:
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
        print('Simulated package download failed.', file=sys.stderr)
        sys.exit(9)
    print('Downloading dependency metadata ...')
    requirement = args[-1]
    latest = os.environ.get('TEST_LATEST', '9.8.7')
    version = requirement.split('==')[-1] if '==' in requirement else latest
    (root / 'installed-version').write_text(version)
    if '[' in requirement:
        (root / 'extras').write_text(requirement.split('[', 1)[1].split(']', 1)[0])
elif args[:1] == ['-m'] and args[1] in ('tweetnook', 'tweetnook.system_install'):
    if args[2:] == ['--version']:
        print('tweetnook ' + (root / 'installed-version').read_text())
    elif args[2:] == ['--help']:
        sys.exit(5 if os.environ.get('TEST_FAIL_HELP') else 0)
    elif args[2:4] == ['service', 'install']:
        print('Created symlink /etc/systemd/system/example.service')
        print('TweetNook service enabled and restarted.')
    elif args[2:4] != ['service', 'install'] and args[2:] not in (
        ['serve'], ['update'], ['service', 'restart'], ['service', 'status']
    ):
        raise AssertionError(args)
elif args[:1] == ['-']:
    sys.prefix = str(root)
    source = sys.stdin.read()
    if 'update_config_values' in source:
        pass  # The access script is exercised against real config in its own test.
    elif 'def wait_for_web(' in source:
        if os.environ.get('TEST_WEB_FAIL'):
            print('The web app did not respond on port 8000.', file=sys.stderr)
            sys.exit(4)
        print(os.environ.get('TEST_WEB_STATUS', 'network http://192.0.2.10:8000 8000'))
    else:
        exec(compile(source, '<installer-check>', 'exec'))
else:
    raise AssertionError(args)
"""

FAKE_UV = """\
import json
import os
import shutil
import sys
import time
from pathlib import Path

args = sys.argv[1:]
if os.environ.get('TEST_PAUSE_UV'):
    time.sleep(4)
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
elif [[ $1 == stop || $1 == start ]]; then printf "systemctl %s\\n" "$*" >> "$TEST_ADMIN_EVENTS"
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
        "TMPDIR": str(tmp_path),
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

        def pasted(self, *args, **changes):
            args = self.system_args(args, changes)
            source = INSTALLER.read_text()
            # -i enables real interactive history expansion even with piped stdin.
            # The trailing commands must still run after installer exit/error.
            commands = (
                "set -H\n"
                f"set -- {shlex.join(args)}\n"
                f"{source}\n"
                'printf "PARENT_ALIVE:%s:%s\\n" "$?" "$-"\n'
                'printf "PARENT_PATH:%s\\n" "$PATH"\n'
                "trap -p EXIT INT TERM\n"
                "exit 0\n"
            )
            return subprocess.run(
                ["/bin/bash", "--noprofile", "--norc", "--login", "-i"],
                input=commands,
                env={**env, **changes},
                capture_output=True,
                text=True,
                timeout=20,
                cwd=tmp_path,
            )

        def interactive(self, choice, *, interrupt_at=None, **changes):
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
                    start_new_session=True,
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
                    if interrupt_at and interrupt_at in output.decode():
                        os.killpg(process.pid, signal.SIGINT)
                        interrupt_at = None
                return process.wait(timeout=2), output.decode()
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait()
                os.close(master)
                if slave is not None:
                    os.close(slave)

        def events(self):
            return (
                [json.loads(line) for line in events.read_text().splitlines()]
                if events.exists()
                else []
            )

        def access_writes(self):
            return [
                e[4:]
                for e in self.events()
                if e[0] == "python" and e[2:4] == ["-I", "-"] and len(e) == 7
            ]

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
        "--details",
        "--access",
    ):
        assert flag in result.stdout
    assert "--access local|network Who can open the Web app" in result.stdout
    assert "Linux with running systemd defaults to a system-wide service" in result.stdout
    for path in ("/opt/tweetnook", "/usr/local/bin", "/var/lib/tweetnook", "/etc/tweetnook"):
        assert path in result.stdout
    assert "These defaults also apply without a terminal" in result.stdout
    assert "recommended choice is always option 1" in result.stdout
    assert "--details              Show file locations and maintenance commands" in result.stdout
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
        (["--access", "everyone"], {}, "local or network"),
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
    result = installation.run("--details")
    assert result.returncode == 0, result.stderr
    assert "tweetnook 9.8.7" in result.stdout
    assert "TweetNook is installed" in result.stdout
    assert "TweetNook is running" not in result.stdout
    assert "export PATH=" in result.stdout
    assert "run this installer again" in result.stdout
    creation = next(e for e in installation.events() if e[0] == "uv")
    assert "--seed" in creation
    assert "--no-project" in creation
    assert creation[-1] == str(installation.runtime / "python")
    assert installation.pip_installs()[0][-1] == "tweetnook"
    assert "--upgrade" not in installation.pip_installs()[0]
    assert archive.read_bytes() == b"existing archive"
    # Without a terminal, a rerun updates the existing environment in place.
    result = installation.run("--no-service")
    assert result.returncode == 0, result.stderr
    assert "TweetNook is updated" in result.stdout
    assert "Already up to date (9.8.7)." in result.stdout
    assert len(installation.pip_installs()) == 2
    assert "--upgrade" in installation.pip_installs()[-1]
    assert len([e for e in installation.events() if e[0] == "uv"]) == 1
    assert archive.read_bytes() == b"existing archive"
    # Run the generated launcher, including literal quotes, dollars, and spaces in its path.
    result = subprocess.run(
        [str(installation.launcher), "serve"],
        env={"PATH": os.defpath, "TEST_EVENTS": str(archive.parent / "launcher-events")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not (installation.runtime / ".install-lock").exists()


@pytest.mark.parametrize("choice", ["\n\n\n\n", "1\n\n\n\n", "2\n\n\n\n"])
def test_interactive_linux_startup_choice(installation, choice):
    code, output = installation.interactive(choice, TEST_OS="Linux", TEST_SYSTEMD="yes")
    assert code == 0, output
    assert "How should TweetNook run?" in output
    assert "Choose [1]" in output
    assert "1. Background service (recommended)" in output
    assert "Add automated tagging?" in output
    assert installation.pip_installs()[0][-1] == "tweetnook"
    assert ("TweetNook is running" in output) == (choice != "2\n\n\n\n")
    assert ("TweetNook is installed" in output) == (choice == "2\n\n\n\n")
    assert output.count("Ready to install") == 1


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
    assert ("TweetNook is running" in result.stdout) == service_expected
    assert any("service" in event for event in installation.events()) == service_expected


def test_no_service_overrides_linux_service_default(installation):
    result = installation.run("--no-service", TEST_OS="Linux", TEST_SYSTEMD="yes")
    assert result.returncode == 0, result.stderr
    assert "TweetNook is installed" in result.stdout
    assert not any("service" in event for event in installation.events())


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ({}, ["manual", "127.0.0.1", "false"]),
        ({"TEST_OS": "Linux"}, ["manual", "127.0.0.1", "false"]),
        ({"TEST_OS": "Linux", "TEST_SYSTEMD": "yes"}, ["service", "0.0.0.0", "false"]),
    ],
)
def test_unattended_access_default_keeps_a_saved_host(installation, platform, expected):
    result = installation.run(**platform)
    assert result.returncode == 0, result.stderr
    assert installation.access_writes() == [expected]


@pytest.mark.parametrize(
    ("platform", "first", "answer", "expected"),
    [
        ({}, "1. This computer only (recommended)", "2", ["manual", "0.0.0.0", "true"]),
        ({}, "1. This computer only (recommended)", "", ["manual", "127.0.0.1", "true"]),
        (
            {"TEST_OS": "Linux", "TEST_SYSTEMD": "yes"},
            "1. Other devices on your network (recommended)",
            "2",
            ["service", "127.0.0.1", "true"],
        ),
    ],
)
def test_interactive_access_page(installation, platform, first, answer, expected):
    code, output = installation.interactive(f"\n{answer}\n\n\n", **platform)
    assert code == 0, output
    assert "Who can access TweetNook?" in output
    assert first in output
    assert "subject to your firewall" in output
    label = "this computer only" if expected[1] == "127.0.0.1" else "other devices on your network"
    assert f"Access: {label}" in output
    # The sudo re-run receives the choice instead of applying the service default.
    assert installation.access_writes() == [expected]


def test_updates_keep_saved_access_unless_given(installation):
    assert installation.run("--no-service").returncode == 0
    code, output = installation.interactive("\n\n\n")
    assert code == 0, output
    assert "Who can access TweetNook?" not in output
    assert "Access:" not in output
    assert len(installation.access_writes()) == 1
    result = installation.run("--access", "network")
    assert result.returncode == 0, result.stderr
    assert "Access: other devices on your network" in result.stdout
    assert installation.access_writes()[-1] == ["manual", "0.0.0.0", "true"]


def test_access_script_writes_host_and_keeps_a_saved_one(tmp_path):
    source = INSTALLER.read_text()
    script = source.split("<<'ACCESS'\n", 1)[1].split("\nACCESS\n", 1)[0]
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path), "HOME": str(tmp_path)}
    config = tmp_path / "tweetnook/config.toml"

    def apply(host, explicit):
        subprocess.run(
            [sys.executable, "-", "manual", host, explicit],
            input=script,
            env=env,
            text=True,
            check=True,
            timeout=30,
        )
        return tomllib.loads(config.read_text()).get("web", {})

    assert apply("127.0.0.1", "false") == {"host": "127.0.0.1"}
    assert apply("0.0.0.0", "false") == {"host": "127.0.0.1"}
    # 0.0.0.0 is the configuration default, so a deliberate choice clears the key.
    assert apply("0.0.0.0", "true") == {}


def test_interactive_invalid_choice_on_macos_reprompts(installation):
    code, output = installation.interactive("2\n1\n\n\n\n")
    assert code == 0, output
    assert "Choose 1, or q to cancel" in output
    assert "TweetNook is installed" in output


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


def test_rerun_updates_to_the_requested_version(installation):
    assert installation.run("--no-service").returncode == 0
    result = installation.run("--no-service", "--version", "1.0.0")
    assert result.returncode == 0, result.stderr
    assert "Updated 9.8.7 \u2192 1.0.0" in result.stdout
    assert installation.pip_installs()[-1][-2:] == ["--upgrade", "tweetnook==1.0.0"]


def test_interrupted_install_does_not_switch_versions(installation):
    # Without a unit, a previous service run is resumed rather than updated.
    result = installation.run(TEST_OS="Linux", TEST_SYSTEMD="yes", TEST_UID="0")
    assert result.returncode == 0, result.stderr
    result = installation.run(
        "--version", "1.0.0", TEST_OS="Linux", TEST_SYSTEMD="yes", TEST_UID="0"
    )
    assert result.returncode != 0
    assert "Rerun without --version" in result.stderr
    assert len(installation.pip_installs()) == 1


def test_rerun_update_keeps_installed_addons(installation):
    assert installation.run("--no-service", "--automated-tagging").returncode == 0
    result = installation.run("--no-service", TEST_LATEST="9.9.0")
    assert result.returncode == 0, result.stderr
    assert "Ready to update" in result.stdout
    assert "Add-on: Gemini automated tagging (installed)" in result.stdout
    assert "Updated 9.8.7 \u2192 9.9.0" in result.stdout
    assert "\u2713 Update TweetNook" in result.stdout
    assert installation.pip_installs()[-1][-2:] == ["--upgrade", "tweetnook[automated-tagging]"]


def test_reinstall_keeps_version_and_can_add_an_addon(installation):
    assert installation.run("--no-service").returncode == 0
    result = installation.run(
        "--no-service", "--reinstall", "--automated-tagging", TEST_LATEST="9.9.0"
    )
    assert result.returncode == 0, result.stderr
    assert "Ready to reinstall" in result.stdout
    assert "TweetNook is reinstalled" in result.stdout
    assert "Reinstalled 9.8.7." in result.stdout
    repair, addon = installation.pip_installs()[-2:]
    assert repair[-3:] == ["--force-reinstall", "--no-deps", "tweetnook==9.8.7"]
    assert addon[-1] == "tweetnook[automated-tagging]==9.8.7"
    assert "--upgrade" not in addon


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--reinstall",), "nothing to reinstall"),
        (("--reinstall", "--version", "1.0.0"), "nothing to reinstall"),
    ],
)
def test_reinstall_requires_an_existing_installation(installation, args, message):
    result = installation.run("--no-service", *args)
    assert result.returncode != 0
    assert message in result.stderr
    assert not installation.runtime.exists()


def test_reinstall_cannot_change_versions(installation):
    assert installation.run("--no-service").returncode == 0
    result = installation.run("--no-service", "--reinstall", "--version", "1.0.0")
    assert result.returncode != 0
    assert "--reinstall keeps the installed version (9.8.7)" in result.stderr
    assert len(installation.pip_installs()) == 1


@pytest.mark.parametrize(
    ("answers", "reinstalled", "tagging"),
    [("\n\n\n", False, False), ("2\n2\n\n", True, True)],
)
def test_interactive_rerun_offers_update_reinstall_and_addons(
    installation, answers, reinstalled, tagging
):
    assert installation.run("--no-service").returncode == 0
    code, output = installation.interactive(answers)
    assert code == 0, output
    assert "TweetNook 9.8.7 is already installed" in output
    assert "1. Update to the latest version (recommended)" in output
    assert "2. Reinstall 9.8.7" in output
    assert "How should TweetNook run?" not in output
    assert "Add automated tagging?" in output
    assert ("Ready to reinstall" in output) == reinstalled
    assert ("Ready to update" in output) != reinstalled
    assert ("[automated-tagging]" in installation.pip_installs()[-1][-1]) == tagging


def test_interactive_rerun_skips_installed_addon_page(installation):
    assert installation.run("--no-service", "--automated-tagging").returncode == 0
    code, output = installation.interactive("\n\n")
    assert code == 0, output
    assert "Add automated tagging?" not in output
    assert "Add-on: Gemini automated tagging (installed)" in output
    assert "TweetNook is updated" in output


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
        "--details",
        "--bin-dir",
        str(bin_dir),
        TEST_OS="Linux",
        TEST_SYSTEMD="yes",
        TEST_UID=uid,
        **homes,
    )
    assert result.returncode == 0, result.stderr
    service = next(e for e in installation.events() if "install" in e and "service" in e)
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
    assert "TweetNook is running" in result.stdout
    commands = [
        shlex.split(line.strip()) for line in result.stdout.splitlines() if line.startswith("  /")
    ]
    assert commands == [
        [str(bin_dir / "tweetnook"), "service", "status"],
        [str(bin_dir / "tweetnook"), "service", "restart"],
        [str(bin_dir / "tweetnook"), "update"],
    ]
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
    assert "TweetNook is running" not in result.stdout


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


@pytest.mark.parametrize("uid", ["0", "1000"])
def test_existing_service_is_stopped_updated_and_restarted(installation, uid):
    linux = {"TEST_OS": "Linux", "TEST_SYSTEMD": "yes"}
    assert installation.run(TEST_UID="0", **linux).returncode == 0
    result = installation.run(TEST_UID=uid, TEST_UNIT_STATE="loaded", TEST_LATEST="9.9.0", **linux)
    assert result.returncode == 0, result.stderr
    assert "Ready to update" in result.stdout
    assert "The service stops briefly and restarts." in result.stdout
    assert "\u2713 Stop background service" in result.stdout
    assert "Updated 9.8.7 \u2192 9.9.0" in result.stdout
    assert "Open in your browser:" in result.stdout
    assert ("sudo" in installation.admin_events()) == (uid != "0")
    assert "systemctl stop tweetnook.service" in installation.admin_events()
    assert installation.pip_installs()[-1][-2:] == ["--upgrade", "tweetnook"]
    assert sum("service" in e and "install" in e for e in installation.events()) == 2
    assert sum(event.startswith("useradd ") for event in installation.admin_events()) == 1


def test_failed_service_update_starts_the_service_again(installation):
    linux = {"TEST_OS": "Linux", "TEST_SYSTEMD": "yes", "TEST_UID": "0"}
    assert installation.run(**linux).returncode == 0
    result = installation.run(TEST_UNIT_STATE="loaded", TEST_FAIL_INSTALL="1", **linux)
    assert result.returncode == 9
    assert "! Update TweetNook" in result.stdout
    assert "The background service was started again." in result.stderr
    assert installation.admin_events()[-2:] == [
        "systemctl stop tweetnook.service",
        "systemctl start tweetnook.service",
    ]


def test_existing_service_cannot_switch_to_manual_startup(installation):
    linux = {"TEST_OS": "Linux", "TEST_SYSTEMD": "yes", "TEST_UID": "0"}
    assert installation.run(**linux).returncode == 0
    result = installation.run(
        "--no-service",
        "--install-dir",
        str(installation.system_runtime),
        TEST_UNIT_STATE="loaded",
        **linux,
    )
    assert result.returncode != 0
    assert "already installed as a background service" in result.stderr
    assert len(installation.pip_installs()) == 1


def test_root_service_install_does_not_require_sudo_or_home(installation, tmp_path):
    (tmp_path / "commands/sudo").unlink()
    result = installation.run(TEST_OS="Linux", TEST_SYSTEMD="yes", TEST_UID="0", HOME="")
    assert result.returncode == 0, result.stderr
    assert "TweetNook is running" in result.stdout
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


@pytest.mark.parametrize("answers", ["q\n", "\nq\n", "\n\nq\n", "\n\n\nn\n", "\n\n\nq\n", "\x04"])
def test_interactive_cancellation_makes_no_changes(installation, tmp_path, answers):
    code, output = installation.interactive(answers, TEST_OS="Linux", TEST_SYSTEMD="yes")
    assert code == 0, output
    assert "Installation cancelled. No changes made." in output
    assert not installation.events()
    assert not installation.admin_events()
    assert not installation.system_runtime.exists()
    assert not list(tmp_path.glob("tweetnook-install.*"))


def test_interactive_invalid_choices_retry_and_review_once(installation):
    code, output = installation.interactive(
        "7\n1\n3\n\n\nmaybe\ny\n", TEST_OS="Linux", TEST_SYSTEMD="yes"
    )
    assert code == 0, output
    assert "Choose 1 or 2, or q to cancel" in output
    assert "Enter y to continue or n to cancel" in output
    assert output.count("Ready to install") == 1
    assert output.count("TweetNook · Install") == 1
    assert "TweetNook is running" in output


def test_compact_success_hides_dependency_and_service_noise(installation, tmp_path):
    result = installation.run("--service", TEST_OS="Linux", TEST_SYSTEMD="yes")
    assert result.returncode == 0, result.stderr
    assert "Downloading dependency metadata" not in result.stdout
    assert "Created symlink" not in result.stdout
    assert "enabled and restarted" not in result.stdout
    assert "Installation details" not in result.stdout
    assert result.stdout.count("TweetNook is running") == 1
    assert "http://192.0.2.10:8000" in result.stdout
    assert "<server-or-container-IP>" not in result.stdout
    assert "\x1b" not in result.stdout
    assert not list(tmp_path.glob("tweetnook-install.*"))


def test_http_startup_failure_retains_diagnostics_without_success(installation, tmp_path):
    result = installation.run("--service", TEST_OS="Linux", TEST_SYSTEMD="yes", TEST_WEB_FAIL="1")
    assert result.returncode == 4
    assert "TweetNook is running" not in result.stdout
    assert "! Check web server" in result.stdout
    assert "web app did not respond" in result.stderr
    assert "Diagnostic log:" in result.stderr
    assert "journalctl -u tweetnook" in result.stderr
    assert "enabled and restarted" not in result.stderr
    logs = list(tmp_path.glob("tweetnook-install.*"))
    assert len(logs) == 1
    assert "web app did not respond" in logs[0].read_text()
    assert logs[0].stat().st_mode & 0o777 == 0o600
    assert not (installation.system_runtime / ".install-lock").exists()


@pytest.mark.parametrize(
    ("status", "expected", "excluded"),
    [
        (
            "unknown http://127.0.0.1:9123 9123",
            "Open that IP address on port 9123",
            "Open in your browser to finish setup",
        ),
        (
            "local http://[::1]:9123 9123",
            "browser on this server",
            "hostname -I",
        ),
    ],
)
def test_local_and_unknown_addresses_have_honest_guidance(installation, status, expected, excluded):
    result = installation.run(
        "--service", TEST_OS="Linux", TEST_SYSTEMD="yes", TEST_WEB_STATUS=status
    )
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout
    assert excluded not in result.stdout


def test_package_failure_exposes_the_error_and_preserves_exit_status(installation):
    result = installation.run("--no-service", TEST_FAIL_INSTALL="1")
    assert result.returncode == 9
    assert "! Install TweetNook" in result.stdout
    assert "Simulated package download failed" in result.stderr
    assert "Diagnostic log:" in result.stderr
    assert "TweetNook is installed" not in result.stdout


def test_malformed_address_check_result_cannot_claim_success(installation):
    result = installation.run(
        "--service", TEST_OS="Linux", TEST_SYSTEMD="yes", TEST_WEB_STATUS="invalid"
    )
    assert result.returncode != 0
    assert "invalid address" in result.stderr
    assert "TweetNook is running" not in result.stdout


def test_ctrl_c_during_installation_preserves_log_and_releases_lock(installation, tmp_path):
    code, output = installation.interactive(
        "\n\n\n\n",
        interrupt_at="· Prepare Python environment",
        TEST_PAUSE_UV="1",
        TERM="xterm-256color",
    )
    assert code == 130, output
    assert "! Prepare Python environment" in output
    assert "Diagnostic log:" in output
    assert "TweetNook is installed" not in output
    assert not (installation.runtime / ".install-lock").exists()
    assert len(list(tmp_path.glob("tweetnook-install.*"))) == 1


@pytest.mark.parametrize(
    ("args", "changes", "status", "message"),
    [
        ((), {"TEST_OS": "Linux", "TEST_SYSTEMD": "yes"}, 0, "TweetNook is running"),
        (("--help",), {}, 0, "Usage: bash install.sh"),
        (("--version", "not-a-version"), {}, 1, "--version must be a version number"),
    ],
)
def test_pasting_in_root_login_shell_preserves_history_options_and_shell(
    installation, args, changes, status, message
):
    result = installation.pasted(*args, TEST_UID="0", **changes)
    assert result.returncode == 0, result.stderr
    if status == 0:
        assert message in result.stdout
    else:
        assert f"Error: {message}" in result.stderr
    assert "event not found" not in result.stderr
    parent = next(line for line in result.stdout.splitlines() if line.startswith("PARENT_ALIVE:"))
    _marker, code, flags = parent.split(":")
    assert int(code) == status
    assert "H" in flags and "i" in flags
    assert "e" not in flags and "u" not in flags
    assert "PARENT_PATH:" in result.stdout
    assert "trap --" not in result.stdout


def test_outer_and_embedded_installer_bash_syntax():
    source = INSTALLER.read_text()
    assert source.isascii(), "Pasted UTF-8 source is corrupted by Bash 3.2 readline in C locales."
    body = source.split("<<'TWEETNOOK_INSTALL_SCRIPT' || :\n", 1)[1].rsplit(
        "\nTWEETNOOK_INSTALL_SCRIPT\n", 1
    )[0]
    for args in ([str(INSTALLER)], ["-c", body]):
        result = subprocess.run(["/bin/bash", "-n", *args], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_non_root_service_paste_requires_a_file_before_sudo(installation):
    result = installation.pasted(TEST_OS="Linux", TEST_SYSTEMD="yes")
    assert "Download install.sh to a file before requesting sudo" in result.stderr
    assert "PARENT_ALIVE:1:" in result.stdout
    assert not installation.admin_events()
    assert not installation.system_runtime.exists()
