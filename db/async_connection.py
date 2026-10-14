"""Asyncpg connections for the generated async queries on the request path."""

import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import cache
from typing import Any, cast

import asyncpg

from db.async_generated.query import AsyncQuerier


@cache
def asyncpg_sql(statement: str) -> tuple[str, tuple[str, ...]]:
    statement = statement.replace("\\:", ":")
    names = tuple(sorted(set(re.findall(r"(?<!:):p\d+\b", statement)), key=lambda name: int(name[2:])))
    positions = {name: f"${index}" for index, name in enumerate(names, 1)}
    return re.sub(r"(?<!:):p\d+\b", lambda match: positions[match[0]], statement), tuple(name[1:] for name in names)


class Result:
    def __init__(self, rows: list[asyncpg.Record]):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)

    async def __aiter__(self):
        for row in self.rows:
            yield row


class Connection:
    def __init__(self, connection: Any):
        self.connection = connection

    async def execute(self, clause: Any, params: dict | None = None) -> Result:
        statement, names = asyncpg_sql(getattr(clause, "text", clause))
        values = params or {}
        return Result(await self.connection.fetch(statement, *(values[name] for name in names)))

    async def stream(self, clause: Any, params: dict | None = None) -> Result:
        return await self.execute(clause, params)


class AsyncDatabaseManager:
    def __init__(self):
        self.pool: asyncpg.Pool | None = None

    async def start(self):
        if self.pool is not None:
            return
        address = os.environ.get("DATABASE_URL", "")
        if not address.startswith(("postgres://", "postgresql://")):
            raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")
        self.pool = await asyncpg.create_pool(
            address,
            min_size=1,
            max_size=int(os.environ.get("ZOO_DB_POOL_SIZE", "20")),
            timeout=30,
            command_timeout=30,
            server_settings={"timezone": "UTC"},
        )

    def require(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("async database pool is not started")
        return self.pool

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncQuerier]:
        async with self.require().acquire() as connection, connection.transaction():
            yield AsyncQuerier(cast(Any, Connection(connection)))

    async def fetch(self, statement: str, *params) -> list[asyncpg.Record]:
        return await self.require().fetch(statement, *params)

    async def close(self):
        pool, self.pool = self.pool, None
        if pool is not None:
            await pool.close()


async_db = AsyncDatabaseManager()
