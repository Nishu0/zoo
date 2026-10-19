"""Single-use, short-lived tickets for websocket URLs, so a viewer URL never carries a session token or API key.

Tickets live in the database (only their SHA-256), so one API process can issue a ticket and another redeem it, and
they survive a restart for the few seconds they are valid."""

import hashlib
import secrets

from apps.server.jobs import stamp
from db.connection import db_manager

TICKET_TTL = 30


def _hash(ticket: str) -> str:
    return hashlib.sha256(ticket.encode()).hexdigest()


def issue(user_id: str, target: str) -> str:
    ticket = secrets.token_urlsafe(32)
    with db_manager.session() as db:
        db.purge_tickets()
        db.create_ticket(ticket_hash=_hash(ticket), user_id=user_id, target=target, expires_at=stamp(TICKET_TTL))
    return ticket


def redeem(ticket: str, target: str) -> str | None:
    """The user the ticket was issued to, if it is live and for this target. Any ticket is spent on first use."""
    with db_manager.session() as db:
        row = db.redeem_ticket(ticket_hash=_hash(ticket))
    if row is None or row.target != target or row.expires_at <= stamp():
        return None
    return row.user_id
