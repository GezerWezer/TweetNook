"""Process control follows a kernel lifetime rather than PID liveness."""

from __future__ import annotations

import ctypes
import errno
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tweetnook.core import process_identity as processes
from tweetnook.core.process_identity import ProcessIdentity, process_identity


def test_native_identity_matches_the_current_process():
    identity = process_identity(os.getpid())
    assert identity is not None
    assert identity.pid == os.getpid()
    assert identity.group == os.getpgrp()
    assert identity.start
    assert process_identity(os.getpid()) == identity
    assert ProcessIdentity.from_record(identity.record()) == identity


@pytest.mark.parametrize("value", [None, [], {}, {"pid": True, "start": "x", "group": 1}])
def test_invalid_process_records_are_not_owned(value):
    assert ProcessIdentity.from_record(value) is None


def test_linux_identity_reads_birth_ticks_after_the_full_command_name(monkeypatch):
    monkeypatch.setattr(processes, "sys", SimpleNamespace(platform="linux"))
    files = {
        "/proc/77/stat": (
            "77 (worker (name)) S 1 77 77 0 -1 4194304 4 0 0 0 0 0 0 0 20 0 1 0 987654 0"
        ),
        "/proc/sys/kernel/random/boot_id": "boot-id\n",
    }
    monkeypatch.setattr(Path, "read_text", lambda path: files[str(path)])
    assert process_identity(77) == ProcessIdentity(77, "linux:boot-id:987654", 77)


def test_darwin_identity_uses_microsecond_birth_time(monkeypatch):
    monkeypatch.setattr(processes, "sys", SimpleNamespace(platform="darwin"))

    def inspect(pid, flavor, arg, pointer, size):
        assert (pid, flavor, arg, size) == (77, 3, 0, 136)
        info = ctypes.cast(pointer, ctypes.POINTER(processes._BsdInfo)).contents
        info.ids[3] = 77
        info.scheduling[1] = 55
        info.start_sec, info.start_usec = 123456, 789
        return size

    monkeypatch.setattr(processes, "_darwin_pidinfo", lambda: inspect)
    assert process_identity(77) == ProcessIdentity(77, "darwin:123456:789", 55)


def test_reused_pid_never_receives_the_signal(monkeypatch):
    monkeypatch.setattr(processes, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(processes, "process_identity", lambda _pid: ProcessIdentity(77, "new", 77))
    monkeypatch.setattr(os, "kill", lambda *_: pytest.fail("unrelated process signalled"))
    with pytest.raises(ProcessLookupError):
        ProcessIdentity(77, "old", 77).send_signal(signal.SIGTERM)


def test_unreadable_identity_is_not_confirmed_exit(monkeypatch):
    monkeypatch.setattr(processes, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(processes, "process_identity", lambda _pid: None)
    monkeypatch.setattr(processes, "process_has_exited", lambda _pid: False)
    calls = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append((pid, sig)))
    with pytest.raises(PermissionError, match="Cannot verify"):
        ProcessIdentity(77, "old", 77).send_signal(signal.SIGTERM)
    assert calls == [(77, 0)]


@pytest.mark.parametrize("reused", [False, True])
def test_linux_pins_the_signal_target_and_closes_the_descriptor(monkeypatch, reused):
    monkeypatch.setattr(processes, "sys", SimpleNamespace(platform="linux"))
    descriptor = os.open(os.devnull, os.O_RDONLY)
    monkeypatch.setattr(os, "pidfd_open", lambda pid: descriptor, raising=False)
    calls = []
    monkeypatch.setattr(
        signal, "pidfd_send_signal", lambda fd, sig: calls.append((fd, sig)), raising=False
    )
    identity = ProcessIdentity(77, "original", 77)
    monkeypatch.setattr(
        processes,
        "process_identity",
        lambda _pid: ProcessIdentity(77, "replacement", 77) if reused else identity,
    )
    if reused:
        with pytest.raises(ProcessLookupError):
            identity.send_signal(signal.SIGTERM)
    else:
        identity.send_signal(signal.SIGTERM)
    assert calls == ([] if reused else [(descriptor, signal.SIGTERM)])
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_native_owned_child_can_be_stopped():
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    try:
        identity = process_identity(child.pid)
        assert identity is not None
        assert identity.group == child.pid
        identity.send_signal(signal.SIGTERM)
        assert child.wait(timeout=2) == -signal.SIGTERM
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=2)


def test_zombies_are_exited_even_while_the_pid_still_exists(monkeypatch):
    monkeypatch.setattr(processes, "sys", SimpleNamespace(platform="linux"))
    files = {
        "/proc/77/stat": "77 (worker) Z 1 77 77 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 123 0",
        "/proc/sys/kernel/random/boot_id": "boot-id",
    }
    monkeypatch.setattr(Path, "read_text", lambda path: files[str(path)])
    assert processes.process_identity(77) is None
    assert processes.process_has_exited(77) is True


def test_linux_without_pidfd_support_still_verifies_before_signalling(monkeypatch):
    monkeypatch.setattr(processes, "sys", SimpleNamespace(platform="linux"))

    def unsupported(_pid):
        raise OSError(errno.ENOSYS, "not implemented")

    monkeypatch.setattr(os, "pidfd_open", unsupported, raising=False)
    monkeypatch.setattr(
        signal, "pidfd_send_signal", lambda *_: pytest.fail("unsupported"), raising=False
    )
    identity = ProcessIdentity(77, "birth", 77)
    monkeypatch.setattr(processes, "process_identity", lambda _pid: identity)
    calls = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append((pid, sig)))
    identity.send_signal(signal.SIGTERM)
    assert calls == [(77, signal.SIGTERM)]


@pytest.mark.parametrize("error", [errno.ESRCH, errno.EPERM])
def test_darwin_distinguishes_exit_from_permission_failure(monkeypatch, error):
    monkeypatch.setattr(processes, "sys", SimpleNamespace(platform="darwin"))

    def unavailable(*_args):
        ctypes.set_errno(error)
        return 0

    monkeypatch.setattr(processes, "_darwin_pidinfo", lambda: unavailable)
    assert processes.process_identity(77) is None
    assert processes.process_has_exited(77) is (error == errno.ESRCH)


def test_native_exit_is_recognized_before_parent_reaps():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        deadline = time.monotonic() + 2
        while not processes.process_has_exited(child.pid) and time.monotonic() < deadline:
            time.sleep(0.001)
        assert processes.process_has_exited(child.pid)
        assert processes.process_identity(child.pid) is None
    finally:
        child.wait(timeout=2)
