from __future__ import annotations

import errno
import re
import sys
from datetime import datetime
from io import StringIO

import pytest

from tweetnook.activity_history import read_run_log_since
from tweetnook.config import AppConfig, XDGPaths
from tweetnook.job_supervisor import JobSupervisor
from tweetnook.worker_logging import TimestampedWorkerStream, configure_worker_output


def _messages(output: str) -> list[str]:
    messages = []
    for line in output.splitlines():
        if not line:
            continue
        stamp, message = line.split(" ", 1)
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z", stamp)
        assert datetime.fromisoformat(stamp).utcoffset().total_seconds() == 0
        messages.append(message)
    return messages


def test_stream_timestamps_physical_lines_without_buffering_or_splitting_fragments():
    output = StringIO()
    stream = TimestampedWorkerStream(output)
    assert stream.write("Likes: pro") == 10
    stream.flush()
    assert _messages(output.getvalue()) == ["Likes: pro"]
    stream.write("gress · 😀\n\nWARNING: one\nERROR: two")
    stream.write("")
    stream.writelines([" continued", "\n", "Complete\n"])
    assert _messages(output.getvalue()) == [
        "Likes: progress · 😀",
        "WARNING: one",
        "ERROR: two continued",
        "Complete",
    ]
    assert stream.isatty() is False
    assert stream.encoding == output.encoding


def test_full_disk_errors_propagate_and_do_not_break_later_timestamp_prefixes():
    class FullOnce(StringIO):
        full = True

        def write(self, text):
            if self.full:
                raise OSError(errno.ENOSPC, "No space left on device")
            return super().write(text)

    output = FullOnce()
    stream = TimestampedWorkerStream(output)
    with pytest.raises(OSError) as caught:
        stream.write("Cannot write")
    assert caught.value.errno == errno.ENOSPC
    output.full = False
    stream.write("Recovered\n")
    assert _messages(output.getvalue()) == ["Recovered"]


@pytest.mark.parametrize("origin", ["web", "schedule"])
def test_managed_output_timestamps_both_streams_once(monkeypatch, origin):
    output = StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stderr", output)
    monkeypatch.setenv("TWEETNOOK_ACTIVITY_RUN_ID", "test-worker")
    monkeypatch.setenv("TWEETNOOK_ACTIVITY_ORIGIN", origin)
    configure_worker_output()
    stream = sys.stdout
    configure_worker_output()
    assert sys.stdout is stream
    print("Starting", end="")
    print(" worker", file=sys.stderr)
    print("ERROR: failed", file=sys.stderr, flush=True)
    assert _messages(output.getvalue()) == ["Starting worker", "ERROR: failed"]


@pytest.mark.parametrize("run_id, origin", [(None, "web"), ("manual", "cli"), (None, None)])
def test_regular_cli_streams_are_unchanged(monkeypatch, run_id, origin):
    output, errors = StringIO(), StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stderr", errors)
    for key, value in [
        ("TWEETNOOK_ACTIVITY_RUN_ID", run_id),
        ("TWEETNOOK_ACTIVITY_ORIGIN", origin),
    ]:
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    configure_worker_output()
    assert sys.stdout is output
    assert sys.stderr is errors
    print("Unchanged")
    assert output.getvalue() == "Unchanged\n"


def test_real_supervised_worker_timestamps_startup_errors_and_offset_reads(tmp_path, monkeypatch):
    paths = XDGPaths(
        config_dir=tmp_path / "config", data_dir=tmp_path, cache_dir=tmp_path / "cache"
    )
    for key in ["XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"]:
        monkeypatch.setenv(key, str(tmp_path / key.lower()))
    supervisor = JobSupervisor(paths, AppConfig())
    result = supervisor.start(
        kind="sync",
        cli_args=["--unknown-worker-option"],
        origin="web",
        title="tweetnook sync",
    )
    supervisor._monitor_thread.join(timeout=10)
    assert not supervisor._monitor_thread.is_alive()
    assert supervisor.last_result["exit_code"] == 2
    output, offset = read_run_log_since(tmp_path, result["run_id"], 0)
    assert any("unknown-worker-option" in message for message in _messages(output))
    assert offset == len(output.encode("utf-8"))
    assert read_run_log_since(tmp_path, result["run_id"], offset) == ("", offset)
