"""Archive-scoped records for personal, foreground and service Web processes."""

from __future__ import annotations

import json
from pathlib import Path

from tweetnook.core.fileio import atomic_write_json
from tweetnook.core.process_identity import ProcessIdentity, process_identity


def record_web_process(data_dir: Path, identity: ProcessIdentity, port: int) -> None:
    atomic_write_json(
        data_dir / f".web-process-{identity.pid}.json",
        {"identity": identity.record(), "data_dir": str(data_dir.resolve()), "port": port},
    )


def recorded_web_process(data_dir: Path, pid: int) -> ProcessIdentity | None:
    try:
        record = json.loads((data_dir / f".web-process-{pid}.json").read_text())
        if record.get("data_dir") == str(data_dir.resolve()):
            identity = ProcessIdentity.from_record(record.get("identity"))
            if identity is not None and identity.pid == pid:
                return identity
    except (OSError, ValueError, AttributeError):
        pass
    return None


def find_web_process(data_dir: Path, port: int) -> ProcessIdentity | None:
    for path in sorted(data_dir.glob(".web-process-*.json")):
        try:
            record = json.loads(path.read_text())
            identity = ProcessIdentity.from_record(record.get("identity"))
            if (
                record.get("data_dir") == str(data_dir.resolve())
                and record.get("port") == port
                and identity is not None
                and path.name == f".web-process-{identity.pid}.json"
                and process_identity(identity.pid) == identity
            ):
                return identity
        except (OSError, ValueError, AttributeError):
            continue
    return None


def forget_web_process(data_dir: Path, identity: ProcessIdentity) -> None:
    recorded = recorded_web_process(data_dir, identity.pid)
    if recorded is not None and recorded != identity:
        return
    if recorded == identity:
        (data_dir / f".web-process-{identity.pid}.json").unlink(missing_ok=True)
    pid_file = data_dir / ".web.pid"
    try:
        if pid_file.read_text().strip() == str(identity.pid):
            pid_file.unlink(missing_ok=True)
    except FileNotFoundError:
        pass
