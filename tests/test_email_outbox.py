import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

import email_outbox as outbox
from email_delivery import EmailDeliveryError, ResendConfig


@pytest.fixture
def queued(tmp_path):
    db = str(tmp_path / "mail.sqlite")
    with sqlite3.connect(db) as con:
        outbox.schema(con)
        outbox.schema(con)
        message = outbox.enqueue(con, event_key="invite/1", recipient="qa@example.com",
                                 sender="scope@example.com", subject="Invite", body="Hello")
    return db, message


CONFIG = ResendConfig("test-secret", "changed@example.com")


def test_acceptance_persisted_and_never_resent(queued, monkeypatch):
    db, message = queued
    calls = []
    monkeypatch.setattr(outbox, "send_email", lambda **kw: calls.append(kw) or "provider-1")
    assert outbox.deliver(db, message, config=CONFIG) == "accepted"
    assert outbox.deliver(db, message, config=CONFIG) == "accepted"
    assert len(calls) == 1
    assert calls[0]["config"].sender == "scope@example.com"
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT provider_id FROM email_outbox").fetchone()[0] == "provider-1"


def test_uncertain_retry_reuses_exact_payload_and_key(queued, monkeypatch):
    db, message = queued
    calls = []
    def send(**kw):
        calls.append(kw)
        if len(calls) == 1:
            raise EmailDeliveryError("test failure")
        return "provider-1"
    monkeypatch.setattr(outbox, "send_email", send)
    assert outbox.deliver(db, message, config=CONFIG) == "uncertain"
    assert outbox.deliver(db, message, config=CONFIG) == "accepted"
    assert calls[0] == calls[1]


def test_stale_attempt_requires_review_without_network(queued, monkeypatch):
    db, message = queued
    with sqlite3.connect(db) as con:
        con.execute("UPDATE email_outbox SET status='sending',first_attempt_at=1,lease_until=2")
    monkeypatch.setattr(outbox, "send_email", lambda **kw: pytest.fail("must not send"))
    assert outbox.deliver(db, message, config=CONFIG) == "review"


def test_crashed_worker_can_resume_with_same_key_inside_window(queued, monkeypatch):
    db, message = queued
    now = outbox.time.time()
    with sqlite3.connect(db) as con:
        con.execute("UPDATE email_outbox SET status='sending',first_attempt_at=?,lease_until=?",
                    (now - 120, now - 60))
    calls = []
    monkeypatch.setattr(outbox, "send_email", lambda **kw: calls.append(kw) or "provider-1")
    assert outbox.deliver(db, message, config=CONFIG) == "accepted"
    assert calls[0]["idempotency_key"] == f"scope/{message}"


def test_two_workers_send_only_once(queued, monkeypatch):
    db, message = queued
    started, release = Event(), Event()
    def send(**kw):
        started.set()
        assert release.wait(5)
        return "provider-1"
    monkeypatch.setattr(outbox, "send_email", send)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(outbox.deliver, db, message, config=CONFIG)
        try:
            assert started.wait(5)
            assert outbox.deliver(db, message, config=CONFIG) == "sending"
        finally:
            release.set()
        assert first.result() == "accepted"


def test_event_identity_and_transaction_rollback(queued):
    db, message = queued
    kwargs = dict(event_key="invite/1", recipient="qa@example.com", sender="scope@example.com",
                  subject="Invite", body="Hello")
    with sqlite3.connect(db) as con:
        assert outbox.enqueue(con, **kwargs) == message
        with pytest.raises(ValueError):
            outbox.enqueue(con, **(kwargs | {"body": "Changed"}))
    with pytest.raises(RuntimeError):
        with sqlite3.connect(db) as con:
            outbox.enqueue(con, **(kwargs | {"event_key": "invite/2"}))
            raise RuntimeError("domain transaction failed")
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM email_outbox").fetchone()[0] == 1


def test_html_is_persisted_and_retried_unchanged(queued, monkeypatch):
    db, _ = queued
    with sqlite3.connect(db) as con:
        message = outbox.enqueue(con, event_key='html/1', recipient='qa@example.com',
                                 sender='scope@example.com', subject='Scope', body='Plain', body_html='<p>HTML</p>')
    calls = []
    def send(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise EmailDeliveryError('timeout')
        return 'provider'
    monkeypatch.setattr(outbox, 'send_email', send)
    assert outbox.deliver(db, message, config=CONFIG) == 'uncertain'
    assert outbox.deliver(db, message, config=CONFIG) == 'accepted'
    assert calls[0] == calls[1]
    assert calls[0]['html'] == '<p>HTML</p>'
