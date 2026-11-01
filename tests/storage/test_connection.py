from __future__ import annotations

import subprocess
import sys

import pytest

# The freeze stops every Python thread, so only another process can observe it. The function holds
# the connection's SQLite mutex while it waits, which forces the interleaving that froze a server.
FREEZE_SCRIPT = """
import sys
import threading
import time
from pathlib import Path

from tweetnook.storage.backend import ArchiveStore

store = ArchiveStore(Path(sys.argv[1]), create=True)
entered = threading.Event()
release = threading.Event()


def held(value):
    entered.set()
    release.wait(10)
    return value


store.conn.create_function("held_by_test", 1, held)
# The first row comes back from execute, so the function runs in the step that reads the second.
later_row = "SELECT 1 UNION ALL SELECT held_by_test(2)"
stepping_calls = {
    "execute": lambda: store.conn.execute("SELECT held_by_test(1)").fetchall(),
    "fetchall": lambda: store.conn.execute(later_row).fetchall(),
    "fetchone": lambda: [(c := store.conn.execute(later_row)).fetchone(), c.fetchone()],
    "fetchmany": lambda: store.conn.execute(later_row).fetchmany(2),
    "iterate": lambda: list(store.conn.execute(later_row)),
}
results = {}
stepping = threading.Thread(
    target=lambda: results.update(stepped=stepping_calls[sys.argv[2]]())
)
stepping.start()
entered.wait(10)
other = threading.Thread(
    target=lambda: results.update(other=store.conn.execute("SELECT ?", (7,)).fetchone()[0])
)
other.start()
time.sleep(0.2)
# A frozen process never gets here: the other thread keeps the GIL while it waits for the mutex.
release.set()
stepping.join()
other.join()
assert results["stepped"]
assert results["other"] == 7
store.close()
print("finished")
"""


@pytest.mark.parametrize(
    "stepping_call", ["execute", "fetchall", "fetchone", "fetchmany", "iterate"]
)
def test_python_sql_function_does_not_freeze_a_concurrent_call(tmp_path, stepping_call):
    result = subprocess.run(
        [sys.executable, "-c", FREEZE_SCRIPT, str(tmp_path / "archive.db"), stepping_call],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "finished"
