"""Cursor pagination for list endpoints: `?limit=` (default 50, at most 200) and `?cursor=`, answered with
`{"data": [...], "next_cursor": "..." | null}`. Lists are newest first; a cursor is the opaque (created_at, id) of the
last row of the page before, so pages stay stable while rows are added (keyset, not OFFSET)."""

import base64
from collections.abc import Callable, Iterable
from datetime import datetime

from fastapi import Query
from pydantic import BaseModel

from apps.server.errors import ZooError

DEFAULT = 50
MAX = 200


class Page[T](BaseModel):
    data: list[T]
    # pass back as `cursor` for the next page; null on the last
    next_cursor: str | None


def encode(at: datetime, id: str) -> str:
    return base64.urlsafe_b64encode(f"{at.isoformat()}|{id}".encode()).decode()


def decode(cursor: str | None) -> tuple[datetime | None, str | None]:
    if not cursor:
        return None, None
    try:
        at, _, id = base64.urlsafe_b64decode(cursor.encode()).decode().partition("|")
        return datetime.fromisoformat(at), id
    except ValueError:
        raise ZooError("validation_failed", "that cursor isn't one this API gave out")


class Params:
    def __init__(
        self,
        limit: int = Query(default=DEFAULT, ge=1, le=MAX),
        cursor: str | None = Query(default=None, description="next_cursor of the page before"),
    ):
        self.limit = limit
        self.cursor = cursor


def collect[R](
    fetch: Callable[[datetime | None, str | None, int], Iterable[R]],
    keep: Callable[[R], bool],
    key: Callable[[R], tuple[datetime, str]],
    params: Params,
) -> tuple[list[R], str | None]:
    """A page of rows that `keep` accepts, fetching in batches until it's full or the rows run out."""
    before_at, before_id = decode(params.cursor)
    found: list[R] = []
    batch = params.limit + 1
    while True:
        rows = list(fetch(before_at, before_id, batch))
        for row in rows:
            if keep(row):
                found.append(row)
                if len(found) > params.limit:
                    last = found[params.limit - 1]
                    return found[: params.limit], encode(*key(last))
        if len(rows) < batch:
            return found, None
        before_at, before_id = key(rows[-1])
