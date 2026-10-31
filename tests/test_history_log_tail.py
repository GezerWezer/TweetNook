from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tweetnook.jobs.activity_history import read_run_log

RUN_ID = "20261030T000000Z-000000000001"


def test_full_log_reads_only_the_requested_tail(tmp_path, monkeypatch):
    run_dir = tmp_path / "activity" / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    (run_dir / "output.log").write_bytes(b"old output\n" * 200000 + b"\nlatest result\n")
    real_open = Path.open
    reads = []

    @contextmanager
    def guarded_open(path, *args, **kwargs):
        with real_open(path, *args, **kwargs) as handle:

            def read(size=-1):
                assert 0 <= size <= 14, "log retrieval must bound the file read"
                reads.append(size)
                return handle.read(size)

            yield SimpleNamespace(seek=handle.seek, read=read)

    monkeypatch.setattr(Path, "open", guarded_open)
    assert read_run_log(tmp_path, RUN_ID, max_bytes=14) == (
        "[earlier log output truncated]\nlatest result"
    )
    assert reads == [14]


@pytest.mark.parametrize(
    ("console", "output", "max_bytes", "expected"),
    [
        (b" console \n", b"other", 100, "console"),
        (b"", b" output \n", 100, "output"),
        (None, b" output \n", 100, "output"),
        (b"", b"", 100, ""),
        (None, None, 100, ""),
        (b"12345678", b"other", 8, "12345678"),
        (b"12345678", b"other", 4, "[earlier log output truncated]\n5678"),
        (None, "old\né!\n".encode(), 3, "[earlier log output truncated]\n\ufffd!"),
    ],
)
def test_full_log_preserves_precedence_fallback_and_decoding(
    tmp_path, console, output, max_bytes, expected
):
    run_dir = tmp_path / "activity" / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    for name, content in (("console.log", console), ("output.log", output)):
        if content is not None:
            (run_dir / name).write_bytes(content)
    assert read_run_log(tmp_path, RUN_ID, max_bytes=max_bytes) == expected
