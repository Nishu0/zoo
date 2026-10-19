"""Process-local request caches, invalidated by committed workspace notifications."""

import asyncio
import contextlib
import copy
import logging
import os
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import asyncpg

Value = TypeVar("Value")
TTL_SECONDS = 30
MAX_ENTRIES = 10000
logger = logging.getLogger(__name__)


@dataclass
class Entry:
    value: Any
    workspace_id: str | None
    expires: float


class Cache:
    def __init__(self):
        self.entries: OrderedDict[tuple[str, str], Entry] = OrderedDict()
        self.generation = 0
        self.task: asyncio.Task | None = None
        self.ready: asyncio.Event | None = None

    def invalidate(self, workspace_id: str | None = None):
        self.generation += 1
        if workspace_id is None:
            self.entries.clear()
            return
        for key, entry in list(self.entries.items()):
            if entry.workspace_id in (None, workspace_id):
                self.entries.pop(key, None)

    async def get(self, kind: str, identity: str, loader: Callable[[], Awaitable[Value]]) -> Value:
        if self.ready is None or not self.ready.is_set():
            return await loader()
        key = (kind, identity)
        entry = self.entries.get(key)
        if entry is not None and entry.expires > time.monotonic():
            self.entries.move_to_end(key)
            return copy.deepcopy(entry.value)
        self.entries.pop(key, None)
        generation = self.generation
        value = await loader()
        if generation == self.generation and self.ready.is_set() and value is not None:
            workspace_id = getattr(value, "workspace_id", None)
            if isinstance(value, dict):
                workspace_id = value.get("workspace_id", workspace_id)
            self.entries[key] = Entry(copy.deepcopy(value), workspace_id, time.monotonic() + TTL_SECONDS)
            if len(self.entries) > MAX_ENTRIES:
                self.entries.popitem(last=False)
        return value

    def notice(self, connection, process_id: int, channel: str, payload: str):
        self.invalidate(payload or None)

    async def start(self):
        if self.task is not None:
            return
        self.ready = asyncio.Event()
        self.task = asyncio.create_task(self.listen(), name="request-cache-invalidation")
        try:
            async with asyncio.timeout(30):
                await self.ready.wait()
        except BaseException:
            await self.close()
            raise

    async def listen(self):
        assert self.ready is not None
        while True:
            connection = None
            try:
                self.ready.clear()
                self.invalidate()
                connection = await asyncpg.connect(os.environ["DATABASE_URL"], timeout=10)
                stopped = asyncio.Event()
                connection.add_termination_listener(lambda closed, signal=stopped: signal.set())
                await connection.add_listener("zoo_event", self.notice)
                self.ready.set()
                await stopped.wait()
            except (OSError, asyncpg.PostgresError, TimeoutError):
                logger.exception("request cache notification connection failed")
            finally:
                self.ready.clear()
                self.invalidate()
                if connection is not None:
                    await connection.close(timeout=5)
            await asyncio.sleep(1)

    async def close(self):
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.ready = None
        self.invalidate()


cache = Cache()
