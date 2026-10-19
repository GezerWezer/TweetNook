"""Share one activity snapshot watcher across connected browsers."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

logger = logging.getLogger(__name__)


class ActivityStream:
    """Latest-state delivery only; slow readers coalesce updates, never build a backlog.

    All subscriber/task state belongs to the server's event loop. Snapshot reads
    run off that loop and hold no database connection between samples. The
    watcher exists only while a browser subscribes.
    """

    def __init__(
        self,
        snapshot: Callable[[], dict[str, Any]],
        *,
        interval: float = 0.25,
        heartbeat: float = 15,
    ) -> None:
        self.snapshot = snapshot
        self.interval = interval
        self.heartbeat = heartbeat
        self._subscribers: set[asyncio.Queue[str | None]] = set()
        self._task: asyncio.Task[None] | None = None
        self._latest: str | None = None

    def _publish(self, payload: str | None) -> None:
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(payload)

    async def _watch(self) -> None:
        while True:
            try:
                snapshot = await asyncio.to_thread(self.snapshot)
                payload = json.dumps(snapshot, separators=(",", ":"), sort_keys=True)
                if payload != self._latest:
                    self._latest = payload
                    self._publish(payload)
            except Exception:
                # Preserve existing connections during transient filesystem errors.
                logger.exception("Could not read activity stream status")
            await asyncio.sleep(self.interval)

    async def events(self) -> AsyncIterator[str]:
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        if self._task is None:
            self._latest = None
            self._task = asyncio.create_task(self._watch(), name="activity-stream")
        elif self._latest is not None:
            queue.put_nowait(self._latest)
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=self.heartbeat)
                except TimeoutError:
                    # Comments keep intermediaries alive; the named event also
                    # lets browsers detect a silently stalled open connection.
                    yield ": keepalive\nevent: heartbeat\ndata: {}\n\n"
                    continue
                if payload is None:
                    break
                yield f"event: activity\ndata: {payload}\n\n"
        finally:
            self._subscribers.discard(queue)
            if not self._subscribers:
                await self._stop()

    async def _stop(self) -> None:
        task, self._task = self._task, None
        self._latest = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def close(self) -> None:
        self._publish(None)
        await self._stop()
