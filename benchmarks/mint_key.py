"""Mint a local API key for benchmarking.

The server stores only sha256(key) (apps/server/auth_api.py:hash_key), so we
generate a random key, insert its hash, and keep the raw key in
benchmarks/.bench-key (gitignored). Local dev only — production keys must go
through the dashboard.

Usage: uv run python benchmarks/mint_key.py
"""

import hashlib
import pathlib
import secrets
import sys

import asyncpg
from dotenv import load_dotenv

load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")
import os  # noqa: E402  (after load_dotenv so DATABASE_URL is set)

KEY_FILE = pathlib.Path(__file__).resolve().parent / ".bench-key"


async def mint() -> str:
    raw = f"zoo_bench_{secrets.token_hex(24)}"
    row = {
        "id": str(__import__("uuid").uuid4()),
        "workspace_id": sys.argv[1],
        "created_by": sys.argv[2],
        "name": "benchmarks",
        "key_hash": hashlib.sha256(raw.encode()).hexdigest(),
        "key_prefix": raw[:12],
        "scopes": "[]",
    }
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        await conn.executemany(
            """
            INSERT INTO api_keys (id, workspace_id, created_by, name, key_hash, key_prefix, scopes)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            [tuple(row.values())],
        )
    finally:
        await conn.close()
    return raw


if __name__ == "__main__":
    if len(sys.argv) != 3:
        # default to the first workspace + first member found in the local DB
        async def find_ids():
            conn = await asyncpg.connect(os.environ["DATABASE_URL"])
            try:
                ws = await conn.fetchval("SELECT id FROM workspaces LIMIT 1")
                member = await conn.fetchval("SELECT id FROM users LIMIT 1")
                return ws, member
            finally:
                await conn.close()

        ws, member = __import__("asyncio").run(find_ids())
        if not ws or not member:
            raise SystemExit("no workspace/member in DB; pass workspace_id member_id explicitly")
        sys.argv = [sys.argv[0], ws, member]

    key = __import__("asyncio").run(mint())
    KEY_FILE.write_text(key)
    KEY_FILE.chmod(0o600)
    print(f"wrote {KEY_FILE} (key prefix {key[:12]}…)")
