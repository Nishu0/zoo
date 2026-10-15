"""Joins the machine the installer runs on as a server, with no visit to the dashboard. The installers run it in the
API container once Zoo is up:

  python -m server.bootstrap --email you@example.com --name my-mac

It makes the admin's account when there is none (printing the generated password once), and prints JSON with a
one-time join token, the control plane's SSH public key and the API URL: what Servers > Add server would show."""

import argparse
import json
import os
import secrets
import uuid

from db.connection import db_manager
from db.generated.query import CreateUserParams
from server import nodes
from server.auth_api import personal_workspace
from server.ssh import public_key
from utils.hash import PaswwordUtils


def admin(email: str) -> tuple[str, str, str | None]:
    """The user id, their personal workspace and, for an account made here, its password."""
    password = None
    with db_manager.session() as db:
        user = db.get_user_by_email(email=email)
        if user is None:
            password = secrets.token_urlsafe(12)
            user = db.create_user(
                CreateUserParams(
                    id=str(uuid.uuid4()),
                    email=email,
                    name=None,
                    avatar_url=None,
                    password=PaswwordUtils.hash_pass(password),
                )
            )
            if user is None:
                raise RuntimeError("the admin account wasn't saved")
    # after the account is committed: personal_workspace makes the workspace in a transaction of its own
    with db_manager.session() as db:
        workspace = personal_workspace(user, db)
    return user.id, workspace, password


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True, help="the server's name in the dashboard")
    arguments = parser.parse_args()
    email = arguments.email.strip().lower()
    admins = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
    if email not in admins:
        raise SystemExit(f"{email} is not in ADMIN_EMAILS")
    api_url = os.environ.get("ZOO_API_URL")
    if not api_url:
        raise SystemExit("ZOO_API_URL is not set")
    db_manager.init_db()
    user_id, workspace_id, password = admin(email)
    token, _ = nodes.create_token(user_id, workspace_id, arguments.name, api_url)
    print(json.dumps({"token": token, "key": public_key(), "control_plane": api_url, "password": password}))


if __name__ == "__main__":
    main()
