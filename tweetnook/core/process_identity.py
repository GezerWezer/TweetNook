"""Identify a process lifetime independently of its reusable PID."""

from __future__ import annotations

import ctypes
import errno
import os
import signal
import sys
from dataclasses import asdict, dataclass
from functools import cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    pid: int
    start: str
    group: int

    def record(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_record(cls, value: Any) -> ProcessIdentity | None:
        if not isinstance(value, dict):
            return None
        pid, start, group = (value.get(key) for key in ("pid", "start", "group"))
        if (
            type(pid) is not int
            or not 0 < pid < 2**31
            or not isinstance(start, str)
            or not start
            or type(group) is not int
            or not 0 < group < 2**31
        ):
            return None
        return cls(pid, start, group)

    def send_signal(self, sig: signal.Signals, *, process_group: bool = False) -> None:
        if process_group and self.group != self.pid:
            raise PermissionError("The worker does not own its process group.")
        # Linux can pin the process across the identity check and signal, so PID reuse
        # cannot redirect a single-process signal in that interval.
        if (
            not process_group
            and sys.platform == "linux"
            and hasattr(os, "pidfd_open")
            and hasattr(signal, "pidfd_send_signal")
        ):
            try:
                descriptor = os.pidfd_open(self.pid)
            except OSError as error:
                if error.errno != errno.ENOSYS:
                    raise
            else:
                try:
                    self._verify()
                    signal.pidfd_send_signal(descriptor, sig)
                finally:
                    os.close(descriptor)
                return
        self._verify(process_group=process_group)
        if process_group:
            os.killpg(self.pid, sig)
        else:
            os.kill(self.pid, sig)

    def _verify(self, *, process_group: bool = False) -> None:
        current = process_identity(self.pid)
        if current is None:
            if process_has_exited(self.pid):
                raise ProcessLookupError("The recorded process has exited.")
            # Permission failures must not be mistaken for confirmed process exit.
            os.kill(self.pid, 0)
            raise PermissionError("Cannot verify the process identity.")
        if current.start != self.start:
            raise ProcessLookupError("The recorded process has exited.")
        if process_group and current.group != self.group:
            raise PermissionError("The worker no longer owns its process group.")


class _BsdInfo(ctypes.Structure):
    # Darwin's proc_bsdinfo from sys/proc_info.h (PROC_PIDTBSDINFO).
    _fields_ = (
        ("ids", ctypes.c_uint32 * 12),
        ("comm", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32),
        ("scheduling", ctypes.c_uint32 * 6),
        ("start_sec", ctypes.c_uint64),
        ("start_usec", ctypes.c_uint64),
    )


@cache
def _darwin_pidinfo():
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    function = library.proc_pidinfo
    function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    function.restype = ctypes.c_int
    return function


def process_identity(pid: int) -> ProcessIdentity | None:
    """Read the kernel's birth identity; inaccessible and exited processes return None."""
    info = _process_info(pid)
    return info[0] if info is not None and info[1] else None


def process_has_exited(pid: int) -> bool:
    """Recognize kernel-confirmed exit without mistaking inaccessible state for it."""
    info = _process_info(pid)
    return info is not None and not info[1]


def _process_info(pid: int) -> tuple[ProcessIdentity | None, bool] | None:
    if type(pid) is not int or not 0 < pid < 2**31:
        return None
    try:
        if sys.platform == "linux":
            # comm may contain spaces and parentheses; the remaining fields start after
            # its final ')'. starttime is field 22, counted from field 3 (state) here.
            fields = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            return (
                ProcessIdentity(pid, f"linux:{boot}:{int(fields[19])}", int(fields[2])),
                fields[0] not in {"Z", "X"},
            )
        if sys.platform == "darwin":
            info = _BsdInfo()
            if _darwin_pidinfo()(
                pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)
            ) != ctypes.sizeof(info):
                # Darwin returns ESRCH for unreaped zombies too, while kill(pid, 0)
                # still succeeds. EPERM must continue to mean unknown ownership.
                return (None, False) if ctypes.get_errno() == errno.ESRCH else None
            if info.ids[3] != pid:
                return None
            return (
                ProcessIdentity(
                    pid, f"darwin:{info.start_sec}:{info.start_usec}", info.scheduling[1]
                ),
                info.ids[1] != 5,  # SZOMB
            )
    except FileNotFoundError as error:
        return (None, False) if error.filename == f"/proc/{pid}/stat" else None
    except (OSError, ValueError, IndexError):
        return None
    return None
