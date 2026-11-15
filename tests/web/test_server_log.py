from __future__ import annotations

import logging
import logging.config
import re
from logging.handlers import RotatingFileHandler
from types import SimpleNamespace

import pytest

from tweetnook.web import server_log
from tweetnook.web.deps import server_state
from tweetnook.web.routes import activity
from tweetnook.web.server_log import read_full_server_log, read_server_log, server_logging_config

_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "tweetnook")


@pytest.fixture
def restore_logging():
    """The logging setup is process-wide; put the loggers back as they were."""
    saved = {
        name: (list(lg.handlers), lg.level, lg.propagate, lg.disabled)
        for name in _LOGGERS
        for lg in [logging.getLogger(name)]
    }
    yield
    for name, (handlers, level, propagate, disabled) in saved.items():
        lg = logging.getLogger(name)
        for handler in lg.handlers:
            if handler not in handlers:
                handler.close()
        lg.handlers[:] = handlers
        lg.setLevel(level)
        lg.propagate = propagate
        lg.disabled = disabled


def test_server_log_keeps_tweetnook_and_server_lines_but_not_requests(
    tmp_path, restore_logging, capsys
) -> None:
    log_file = tmp_path / "logs" / "server.log"
    logging.config.dictConfig(server_logging_config(log_file))

    logging.getLogger("uvicorn.error").info("Started server process")
    logging.getLogger("uvicorn.access").info("GET /api/search?q=private 200")
    logging.getLogger("tweetnook.jobs.supervisor").info("Started tweetnook sync")
    logging.getLogger("tweetnook.jobs.scheduler").warning("Skipped the scheduled sync")
    try:
        raise OSError("disk full")
    except OSError:
        logging.getLogger("tweetnook.web.setup_coordinator").exception("Could not save Setup")

    lines = log_file.read_text(encoding="utf-8").splitlines()
    stamp = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z "
    assert [line.split("Z ", 1)[1] for line in lines[:4]] == [
        "uvicorn: Started server process",
        "jobs.supervisor: Started tweetnook sync",
        "WARNING: jobs.scheduler: Skipped the scheduled sync",
        "ERROR: web.setup_coordinator: Could not save Setup",
    ]
    assert all(re.match(stamp, line) for line in lines[:4])
    assert lines[4] == "Traceback (most recent call last):"
    assert lines[-1] == "OSError: disk full"
    assert "private" not in log_file.read_text(encoding="utf-8")
    # The terminal still gets TweetNook's lines, which used to reach it through Python's fallback.
    assert "Skipped the scheduled sync" in capsys.readouterr().err


def test_server_log_rotates_and_the_full_log_reads_backups_in_order(
    tmp_path, restore_logging, monkeypatch
) -> None:
    monkeypatch.setattr(server_log, "SERVER_LOG_MAX_BYTES", 400)
    log_file = tmp_path / "logs" / "server.log"
    logging.config.dictConfig(server_logging_config(log_file))
    parent = logging.getLogger("tweetnook")
    # Only the file handler: the terminal copy would just clutter the test output.
    parent.handlers[:] = [h for h in parent.handlers if isinstance(h, RotatingFileHandler)]
    logger = logging.getLogger("tweetnook.test")

    for index in range(40):
        logger.info("line %02d", index)

    assert (tmp_path / "logs" / "server.log.2").is_file()
    assert not (tmp_path / "logs" / "server.log.3").exists()
    full = [line.rsplit(" ", 1)[1] for line in read_full_server_log(log_file).splitlines()]
    # Only the last three files are kept, so the start is gone, but what is left is in order.
    assert full == sorted(full)
    assert full[-1] == "39"
    assert full[0] != "00"
    current = [line.rsplit(" ", 1)[1] for line in read_server_log(log_file).splitlines()]
    assert current == full[-len(current) :]


def test_reading_the_end_of_the_log_starts_at_a_whole_line(tmp_path) -> None:
    log_file = tmp_path / "server.log"
    log_file.write_text("first line\nsecond line\nthird line\n", encoding="utf-8")

    assert read_server_log(log_file, max_bytes=15) == "third line\n"
    assert read_server_log(log_file) == "first line\nsecond line\nthird line\n"
    assert read_server_log(tmp_path / "missing.log") == ""
    assert read_full_server_log(tmp_path / "missing.log") == ""


def test_server_log_route_returns_the_end_or_everything(
    tmp_path, make_web_client, monkeypatch
) -> None:
    log_file = tmp_path / "logs" / "server.log"
    log_file.parent.mkdir()
    (tmp_path / "logs" / "server.log.1").write_text("older\n", encoding="utf-8")
    log_file.write_text("newer\n", encoding="utf-8")
    server_state["paths"] = SimpleNamespace(server_log_file=log_file)
    client = make_web_client(activity.router)

    assert client.get("/api/activity/server-log").text == "newer\n"
    assert client.get("/api/activity/server-log?full=1").text == "older\nnewer\n"


def test_server_log_route_needs_the_password(tmp_path, make_web_client) -> None:
    server_state["paths"] = SimpleNamespace(server_log_file=tmp_path / "server.log")
    client = make_web_client(activity.router, password="secret")

    assert client.get("/api/activity/server-log").status_code == 401
