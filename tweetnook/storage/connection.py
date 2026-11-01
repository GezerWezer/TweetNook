"""The archive connection that concurrent Web requests share."""

from __future__ import annotations

import sqlite3
import threading
from typing import Any


class SerializedCursor(sqlite3.Cursor):
    def __init__(self, connection: SerializedConnection) -> None:
        super().__init__(connection)
        self._statement_lock = connection.statement_lock

    def execute(self, *args: Any) -> sqlite3.Cursor:
        with self._statement_lock:
            return super().execute(*args)

    def executemany(self, *args: Any) -> sqlite3.Cursor:
        with self._statement_lock:
            return super().executemany(*args)

    def executescript(self, *args: Any) -> sqlite3.Cursor:
        with self._statement_lock:
            return super().executescript(*args)

    def fetchone(self) -> Any:
        with self._statement_lock:
            return super().fetchone()

    def fetchmany(self, *args: Any, **kwargs: Any) -> list[Any]:
        with self._statement_lock:
            return super().fetchmany(*args, **kwargs)

    def fetchall(self) -> list[Any]:
        with self._statement_lock:
            return super().fetchall()

    def __next__(self) -> Any:
        with self._statement_lock:
            return super().__next__()

    def close(self) -> None:
        with self._statement_lock:
            super().close()


class SerializedConnection(sqlite3.Connection):
    """Run one SQLite call at a time, so Python SQL functions can't freeze the process.

    A statement holds the connection's SQLite mutex while it steps, and a Python SQL function
    called from that step needs the GIL. CPython binds parameters and reads columns holding the
    GIL, and those calls need the mutex. One thread running a function and another doing either
    wait on each other forever, and every other Python thread waits on the GIL. Waiting for this
    lock releases the GIL, so that cycle can't form.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.statement_lock = threading.RLock()
        super().__init__(*args, **kwargs)

    def cursor(self, factory: Any = SerializedCursor) -> Any:
        return super().cursor(factory)

    def execute(self, *args: Any) -> sqlite3.Cursor:
        return self.cursor().execute(*args)

    def executemany(self, *args: Any) -> sqlite3.Cursor:
        return self.cursor().executemany(*args)

    def executescript(self, *args: Any) -> sqlite3.Cursor:
        return self.cursor().executescript(*args)

    def commit(self) -> None:
        with self.statement_lock:
            super().commit()

    def rollback(self) -> None:
        with self.statement_lock:
            super().rollback()

    def close(self) -> None:
        with self.statement_lock:
            super().close()

    def create_function(self, *args: Any, **kwargs: Any) -> None:
        with self.statement_lock:
            super().create_function(*args, **kwargs)

    def __exit__(self, *args: Any) -> Any:
        with self.statement_lock:
            return super().__exit__(*args)
