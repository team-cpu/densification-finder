"""Durable email attempts; callers must authorize recipients before enqueueing.

No background worker or UI is enabled by importing this module.
"""
from __future__ import annotations

import sqlite3
import time
import uuid

from email_delivery import EmailDeliveryError, ResendConfig, send_email

RETRY_WINDOW_SECONDS = 23 * 60 * 60  # Margin before provider's 24-hour expiry.
LEASE_SECONDS = 60


def schema(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS email_outbox (
            id TEXT PRIMARY KEY,
            event_key TEXT NOT NULL UNIQUE,
            recipient TEXT NOT NULL,
            sender TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK(status IN ('queued','sending','uncertain','accepted','review')),
            created_at REAL NOT NULL,
            first_attempt_at REAL,
            lease_until REAL,
            attempt_token TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            provider_id TEXT
        )
    """)


def enqueue(connection: sqlite3.Connection, *, event_key: str, recipient: str,
            sender: str, subject: str, body: str) -> str:
    """Join caller's transaction, allowing domain changes and email to be atomic.

    An event key represents one authorized notification. Reusing it with a
    different payload raises instead of silently sending changed content.
    """
    if not event_key.strip() or len(event_key) > 200:
        raise ValueError("Invalid email event key")
    payload = (recipient, sender, subject, body)
    if any(not value.strip() for value in payload):
        raise ValueError("Email fields are required")
    message_id = uuid.uuid4().hex
    connection.execute(
        "INSERT INTO email_outbox (id,event_key,recipient,sender,subject,body,created_at) "
        "VALUES (?,?,?,?,?,?,?) ON CONFLICT(event_key) DO NOTHING",
        (message_id, event_key, *payload, time.time()),
    )
    row = connection.execute(
        "SELECT id,recipient,sender,subject,body FROM email_outbox WHERE event_key=?",
        (event_key,),
    ).fetchone()
    if tuple(row[1:]) != payload:
        raise ValueError("Email event already exists with a different payload")
    return str(row[0])


def find(connection: sqlite3.Connection, event_key: str) -> tuple[str, str] | None:
    """(message id, status) for an event key, or None when nothing was queued."""
    row = connection.execute(
        "SELECT id, status FROM email_outbox WHERE event_key=?", (event_key,)
    ).fetchone()
    return (str(row[0]), str(row[1])) if row else None


def deliver(db: str, message_id: str, *, config: ResendConfig | None = None) -> str:
    """Claim once, send outside the transaction, then persist acceptance.

    A timed-out/crashed attempt can be retried after its lease with the same
    provider key. Older uncertain attempts require manual reconciliation.
    """
    config = config or ResendConfig.from_environment()
    now = time.time()
    token = uuid.uuid4().hex
    with sqlite3.connect(db) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM email_outbox WHERE id=?", (message_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown email")
        if row["status"] in ("accepted", "review"):
            return row["status"]
        if row["lease_until"] is not None and row["lease_until"] > now:
            return "sending"
        if row["first_attempt_at"] is not None and now - row["first_attempt_at"] >= RETRY_WINDOW_SECONDS:
            connection.execute("UPDATE email_outbox SET status='review' WHERE id=?", (message_id,))
            return "review"
        connection.execute(
            "UPDATE email_outbox SET status='sending',attempt_token=?,lease_until=?,"
            "first_attempt_at=COALESCE(first_attempt_at,?),attempts=attempts+1 WHERE id=?",
            (token, now + LEASE_SECONDS, now, message_id),
        )
    try:
        provider_id = send_email(
            recipient=row["recipient"], subject=row["subject"], text=row["body"],
            idempotency_key=f"scope/{message_id}",
            config=ResendConfig(config.api_key, row["sender"]),
        )
        status = "accepted"
    except (EmailDeliveryError, ValueError):
        provider_id, status = None, "uncertain"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE email_outbox SET status=?,provider_id=?,lease_until=NULL "
            "WHERE id=? AND attempt_token=?",
            (status, provider_id, message_id, token),
        )
        return connection.execute("SELECT status FROM email_outbox WHERE id=?", (message_id,)).fetchone()[0]
