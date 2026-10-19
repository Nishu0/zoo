"""Rewraps every workspace's data key with the current ZOO_SECRETS_KEY or ZOO_KMS.

Set the new ZOO_SECRETS_KEY (or ZOO_KMS), put the old key in ZOO_SECRETS_KEY_PREVIOUS (and keep the old KMS's
credentials set), run `make rotate-secrets`, then drop ZOO_SECRETS_KEY_PREVIOUS.
"""

from dotenv import load_dotenv

load_dotenv()

from apps.server.security import rotate
from db.connection import db_manager

if __name__ == "__main__":
    db_manager.init_db()
    with db_manager.session() as db:
        print(rotate(db))
