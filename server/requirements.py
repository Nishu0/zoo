"""What the API needs to serve. Each check raises with what's wrong; main.py runs them all before serving and exits
with one line per failure, and /readyz reports each by name."""

import os

from db.connection import db_manager
from server import objects
from server.security import require_secrets_key


def database():
    with db_manager.session() as db:
        list(db.list_all_servers())


def object_store():
    objects.require()
    objects.listing("readyz/")


def jwt_secret():
    if not os.environ.get("JWT_SECRET"):
        raise RuntimeError("JWT_SECRET is not set")


def api_url():
    if not os.environ.get("ZOO_API_URL"):
        raise RuntimeError("ZOO_API_URL is not set; set it to the URL sandboxes and hosts reach the API on")


CHECKS = {
    "database": database,
    "object_store": object_store,
    "jwt_secret": jwt_secret,
    "secrets_key": require_secrets_key,
    "api_url": api_url,
}


def failures() -> list[str]:
    failed = []
    for name, check in CHECKS.items():
        try:
            check()
        except Exception as e:
            failed.append(f"{name}: {str(e) or e.__class__.__name__}")
    return failed
