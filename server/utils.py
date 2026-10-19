"""Shared helpers: password hashing, and timestamps. The database stores timezone-aware UTC datetimes, while the
dashboard parses `YYYY-MM-DD HH:MM:SS` strings (web/src/lib/utils.ts appends the Z itself)."""

from datetime import UTC, datetime
from typing import overload

import bcrypt

FORMAT = "%Y-%m-%d %H:%M:%S"


class PaswwordUtils:
    @staticmethod
    def hash_pass(orignal: str) -> str:
        bytes = orignal.encode("utf-8")
        salt = bcrypt.gensalt()
        hashed_bytes = bcrypt.hashpw(bytes, salt=salt)
        return hashed_bytes.decode("utf-8")

    @staticmethod
    def verify_pass(hashed: str, orignal: str) -> bool:
        hashed_btyes = hashed.encode("utf-8")
        orignal_btyes = orignal.encode("utf-8")
        return bcrypt.checkpw(orignal_btyes, hashed_password=hashed_btyes)


@overload
def to_stamp(value: datetime) -> str: ...


@overload
def to_stamp(value: None) -> None: ...


def to_stamp(value: datetime | None) -> str | None:
    """A datetime in the dashboard's format, or None; a time without a zone is taken as UTC."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(UTC)
    return value.strftime(FORMAT)
