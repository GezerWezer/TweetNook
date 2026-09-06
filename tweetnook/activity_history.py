"""Durable command activity history shared by CLI, Web, and scheduled jobs."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ACTIVITY_ORIGIN_ENV = "TWEETNOOK_ACTIVITY_ORIGIN"
ACTIVITY_RUN_ID_ENV = "TWEETNOOK_ACTIVITY_RUN_ID"
RUN_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}$")
SECRET_ARGUMENTS = {
    "--auth-token",
    "--ct0",
    "--password",
    "--api-key",
}


def new_run_id(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:12]}"


def activity_root(data_dir: Path) -> Path:
    return data_dir / "activity"


def runs_root(data_dir: Path) -> Path:
    return activity_root(data_dir) / "runs"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def sanitized_argv(argv: list[str] | None = None) -> list[str]:
    values = list(sys.argv if argv is None else argv)
    sanitized: list[str] = []
    redact_next = False
    for value in values:
        if redact_next:
            sanitized.append("********")
            redact_next = False
            continue
        option, separator, _option_value = value.partition("=")
        if option in SECRET_ARGUMENTS:
            sanitized.append(f"{option}=********" if separator else option)
            redact_next = not separator
            continue
        sanitized.append(value)
    return sanitized


def reserve_run(
    data_dir: Path,
    *,
    run_id: str,
    title: str,
    command: list[str],
    origin: str,
    started_at: float | None = None,
) -> Path:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("Invalid activity run ID.")
    run_dir = runs_root(data_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = run_dir / "metadata.json"
    current = _read_json(metadata_path) or {}
    current.update(
        {
            "version": 1,
            "run_id": run_id,
            "title": title,
            "command": sanitized_argv(command),
            "origin": origin,
            "status": current.get("status", "starting"),
            "started_at": current.get("started_at", started_at or time.time()),
            "completed_at": current.get("completed_at"),
            "pid": current.get("pid"),
            "exit_code": current.get("exit_code"),
            "success": current.get("success"),
            "summary": current.get("summary", ""),
        }
    )
    _atomic_json(metadata_path, current)
    return run_dir


class ActivityRunWriter:
    """Append-only semantic log plus atomic snapshots for one pipeline run."""

    def __init__(self, state_path: Path, title: str) -> None:
        self.data_dir = state_path.parent
        supplied_id = os.environ.get(ACTIVITY_RUN_ID_ENV, "")
        self.run_id = supplied_id if RUN_ID_RE.fullmatch(supplied_id) else new_run_id()
        self.origin = os.environ.get(ACTIVITY_ORIGIN_ENV, "cli").strip() or "cli"
        self.run_dir = reserve_run(
            self.data_dir,
            run_id=self.run_id,
            title=title,
            command=list(sys.argv),
            origin=self.origin,
        )
        self.metadata_path = self.run_dir / "metadata.json"
        self.events_path = self.run_dir / "events.jsonl"
        self.output_path = self.run_dir / "output.log"

    def start(self, *, pid: int, started_at: float) -> None:
        self.update_metadata(status="running", pid=pid, started_at=started_at)

    def append(self, *, scope: str, event: str, message: str, line: str) -> None:
        timestamp = time.time()
        payload = {
            "timestamp": timestamp,
            "scope": scope,
            "event": event,
            "message": message,
            "line": line,
        }
        try:
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, separators=(",", ":")) + "\n")
            with self.output_path.open("a", encoding="utf-8") as handle:
                stamp = datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z")
                handle.write(f"{stamp} {line}\n")
        except OSError:
            pass

    def snapshot(self, snapshot: dict[str, Any]) -> None:
        payload = dict(snapshot)
        payload["run_id"] = self.run_id
        payload["origin"] = self.origin
        try:
            _atomic_json(self.run_dir / "snapshot.json", payload)
            if not payload.get("running"):
                _atomic_json(self.run_dir / "final.json", payload)
                self.update_metadata(
                    status=(
                        "completed"
                        if payload.get("success")
                        else (
                            "stopped"
                            if str(payload.get("summary", "")).lower().startswith("stopped")
                            else "failed"
                        )
                    ),
                    completed_at=payload.get("completed_at") or time.time(),
                    success=payload.get("success"),
                    summary=payload.get("summary", ""),
                )
        except OSError:
            pass

    def update_metadata(self, **changes: Any) -> None:
        try:
            metadata = _read_json(self.metadata_path) or {"run_id": self.run_id}
            metadata.update(changes)
            _atomic_json(self.metadata_path, metadata)
        except OSError:
            pass


def mark_run_process_result(
    data_dir: Path,
    run_id: str,
    *,
    exit_code: int,
    stopped: bool = False,
) -> None:
    run_dir = get_run_dir(data_dir, run_id)
    metadata_path = run_dir / "metadata.json"
    metadata = _read_json(metadata_path) or {"run_id": run_id}
    final = _read_json(run_dir / "final.json")
    if final is None:
        snapshot = _read_json(run_dir / "snapshot.json") or {}
        snapshot.update(
            {
                "run_id": run_id,
                "running": False,
                "success": False,
                "completed_at": time.time(),
                "updated_at": time.time(),
                "summary": (
                    "Stopped by user." if stopped else f"Worker exited with status {exit_code}."
                ),
            }
        )
        _atomic_json(run_dir / "final.json", snapshot)
        final = snapshot
    metadata.update(
        {
            "status": "stopped" if stopped else ("completed" if exit_code == 0 else "failed"),
            "completed_at": final.get("completed_at") or time.time(),
            "exit_code": exit_code,
            "success": exit_code == 0 and bool(final.get("success", True)),
            "summary": final.get("summary", ""),
        }
    )
    _atomic_json(metadata_path, metadata)


def update_run_metadata(data_dir: Path, run_id: str, **changes: Any) -> None:
    run_dir = get_run_dir(data_dir, run_id)
    metadata_path = run_dir / "metadata.json"
    metadata = _read_json(metadata_path) or {"run_id": run_id}
    metadata.update(changes)
    _atomic_json(metadata_path, metadata)


def get_run_dir(data_dir: Path, run_id: str) -> Path:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("Invalid activity run ID.")
    root = runs_root(data_dir).resolve()
    candidate = (root / run_id).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("Invalid activity run ID.")
    return candidate


def read_run(data_dir: Path, run_id: str) -> dict[str, Any] | None:
    run_dir = get_run_dir(data_dir, run_id)
    metadata = _read_json(run_dir / "metadata.json")
    if metadata is None:
        return None
    snapshot = _read_json(run_dir / "final.json") or _read_json(run_dir / "snapshot.json")
    return {**metadata, "snapshot": snapshot}


def list_runs(data_dir: Path, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    root = runs_root(data_dir)
    if not root.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for entry in root.iterdir():
        if not entry.is_dir() or not RUN_ID_RE.fullmatch(entry.name):
            continue
        metadata = _read_json(entry / "metadata.json")
        if metadata is not None:
            records.append(metadata)
    records.sort(
        key=lambda item: (item.get("started_at") or 0, item.get("run_id", "")),
        reverse=True,
    )
    return records[offset : offset + limit]


def latest_finished_snapshot(
    data_dir: Path,
    *,
    title: str | None = None,
) -> dict[str, Any] | None:
    for record in list_runs(data_dir, limit=25):
        if record.get("status") in {"starting", "running"}:
            continue
        if title is not None and record.get("title") != title:
            continue
        run = read_run(data_dir, str(record["run_id"]))
        if run and isinstance(run.get("snapshot"), dict):
            return run["snapshot"]
    return None


def read_run_log(data_dir: Path, run_id: str, *, max_bytes: int = 2_000_000) -> str:
    run_dir = get_run_dir(data_dir, run_id)
    candidates = (run_dir / "console.log", run_dir / "output.log")
    for path in candidates:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if not data:
            continue
        prefix = ""
        if len(data) > max_bytes:
            data = data[-max_bytes:]
            prefix = "[earlier log output truncated]\n"
        return (prefix + data.decode("utf-8", errors="replace")).strip()
    return ""


def cleanup_runs(data_dir: Path, *, max_runs: int, retention_days: int) -> None:
    records = list_runs(data_dir, limit=100_000)
    cutoff = time.time() - max(retention_days, 1) * 86400
    for index, record in enumerate(records):
        if record.get("status") in {"starting", "running"}:
            continue
        if index < max(max_runs, 1) and (record.get("completed_at") or time.time()) >= cutoff:
            continue
        run_dir = get_run_dir(data_dir, str(record["run_id"]))
        for child in run_dir.iterdir():
            if child.is_file():
                child.unlink(missing_ok=True)
        try:
            run_dir.rmdir()
        except OSError:
            pass
