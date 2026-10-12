"""Migrates Postgres with goose, holding an advisory lock so Zoo pods that start together take turns: the first
applies what's new and the rest find nothing left to do (scripts/start.sh). Waits for the database to accept
connections first, for up to ZOO_DB_WAIT seconds, since on Kubernetes it may still be starting."""

import os
import subprocess
import sys
import time

import psycopg
from dotenv import load_dotenv

# like main.py: a .env in the working directory, for running from source (compose passes the environment itself)
load_dotenv()

# any constant: it names this lock among the database's advisory locks
LOCK = 7_402_118


def connect(url: str, wait: float) -> psycopg.Connection:
    deadline = time.monotonic() + wait
    while True:
        try:
            return psycopg.connect(url, autocommit=True, connect_timeout=10)
        except psycopg.OperationalError as e:
            if time.monotonic() > deadline:
                raise
            print(f"waiting for the database: {str(e).strip()}", file=sys.stderr, flush=True)
            time.sleep(3)


def main() -> int:
    url = os.environ["DATABASE_URL"]
    with connect(url, float(os.environ.get("ZOO_DB_WAIT", "300"))) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK,))
        try:
            return subprocess.run(["goose", "-dir", "db/migrations", "postgres", url, "up"], check=False).returncode
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK,))


if __name__ == "__main__":
    sys.exit(main())
