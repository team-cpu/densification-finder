import os
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

import email_outbox
import ingest
import organisation
import paths
import scheduler
import scope_auth as auth

ZURICH = scheduler.zone()


@pytest.fixture
def db(tmp_path, monkeypatch):
    database = str(tmp_path / "results.sqlite")
    shutil.copy2(paths.SEED_DB, database)
    with sqlite3.connect(database) as con:
        ingest.schema(con)
    monkeypatch.setenv("SCOPE_AUTH_MODE", "personal")
    monkeypatch.setenv("SCOPE_OWNER_EMAIL", "owner@example.com")
    monkeypatch.setenv("SCOPE_PUBLIC_URL", "https://scope.example.com")
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "scope@example.com")
    monkeypatch.setattr(auth, "current", lambda db=None: {"id": 1, "role": "Inhaber"})
    auth.bootstrap_owner(database)
    with sqlite3.connect(database) as con:
        con.execute("UPDATE organisation_members SET status='active'")
        con.execute("INSERT INTO organisation_members (name, email, role, status) VALUES "
                    "('Anna', 'anna@example.com', 'Bearbeiter', 'active'), "
                    "('Pending', 'pending@example.com', 'Leseweise', 'pending')")
        con.execute("UPDATE scope_access SET auth_id='owner-verified' WHERE member_id=1")
        con.execute("INSERT INTO scope_access(member_id,auth_id,invited_until) SELECT id,'anna-verified',0 FROM organisation_members WHERE email='anna@example.com'")
    return database


def parcel(db, n=0):
    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT bfs, parcel, municipality, address FROM parcel_results "
                           "WHERE address <> '' ORDER BY bfs, parcel LIMIT ?", (n + 1,)).fetchall()
    return rows[n]


def save_lead(db, row, **fields):
    bfs, parcel_no = row[0], row[1]
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR REPLACE INTO parcel_workflow (bfs, parcel, saved, hidden, owner_name, "
                    "contact_status, due_date, last_contact, next_step, note, contact_person, "
                    "owner_address, phone, email, updated_at) VALUES (?,?,1,0,?,?,?,?,?,?,?,?,?,?,?)",
                    (bfs, parcel_no, fields.get("owner_name", ""), fields.get("contact_status", "not_contacted"),
                     fields.get("due_date", ""), fields.get("last_contact", ""), fields.get("next_step", ""),
                     fields.get("note", ""), fields.get("contact_person", ""), "", "", "",
                     fields.get("updated_at", "2026-09-10 08:00:00")))


def settings(db, **flags):
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR IGNORE INTO organisation_profile (id, name) VALUES (1, '')")
        for key, value in flags.items():
            con.execute(f"UPDATE organisation_profile SET {key}=? WHERE id=1", (int(value),))


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=ZURICH)


def test_plan_waits_for_seven_oclock_and_marks_mondays():
    assert scheduler.plan(at("2026-09-11 06:59")) == []              # Friday, too early
    assert scheduler.plan(at("2026-09-11 07:00")) == [("reminder", "2026-09-11")]
    assert scheduler.plan(at("2026-09-14 07:30")) == [("reminder", "2026-09-14"), ("digest", "2026-W38")]
    assert scheduler.plan(at("2026-09-14 06:00")) == []


def test_reminder_lists_due_leads_oldest_first_and_skips_declined(db):
    first, second, third = parcel(db, 0), parcel(db, 1), parcel(db, 2)
    save_lead(db, first, due_date="2026-09-10", contact_status="contacted", owner_name="Müller AG")
    save_lead(db, second, due_date="2026-09-01", contact_status="in_discussion", contact_person="H. Meier")
    save_lead(db, third, due_date="2026-09-01", contact_status="declined")
    save_lead(db, parcel(db, 3), due_date="2026-09-12")  # tomorrow: not due
    text = scheduler.reminder_text(scheduler.due_leads(db, "2026-09-11"), "https://scope.example.com")
    assert text.startswith("2 fällige Wiedervorlagen")
    assert text.index(second[3]) < text.index(first[3])
    assert "Im Gespräch" in text and "H. Meier" in text and "Müller AG" in text
    assert "01.09.2026" in text and "10.09.2026" in text
    assert third[3] not in text
    assert "https://scope.example.com" in text
    assert scheduler.reminder_text([], "https://scope.example.com") == ""
    # Seed addresses already carry "PLZ Ort"; the municipality is not repeated.
    assert f"{second[3]}, {second[2]}" not in text if second[2] in second[3] else f"{second[3]}, {second[2]}" in text
    assert scheduler._where({"address": "Weg 1, 5000 Aarau", "municipality": "Aarau", "parcel": "9"}) == "Weg 1, 5000 Aarau"
    assert scheduler._where({"address": "", "municipality": "Aarau", "parcel": "9"}) == "Parzelle 9, Aarau"


def test_digest_counts_board_overdue_upcoming_and_recent(db):
    save_lead(db, parcel(db, 0), due_date="2026-09-10", contact_status="contacted", updated_at="2026-09-01 09:00:00")
    save_lead(db, parcel(db, 1), due_date="2026-09-16", contact_status="in_discussion", updated_at="2026-09-13 09:00:00")
    save_lead(db, parcel(db, 2), contact_status="declined", updated_at="2026-08-01 09:00:00")
    text = scheduler.digest_text(scheduler.board_summary(db, "2026-09-14"), "https://scope.example.com")
    assert "3 Leads" in text
    assert "Brief versandt: 1" in text and "Im Gespräch: 1" in text and "Abgelehnt: 1" in text
    assert "1 überfällig" in text
    assert "16.09.2026" in text
    assert "letzten 7 Tagen: 1" in text


def test_run_once_sends_each_member_once_and_is_idempotent(db, monkeypatch):
    settings(db, due_reminders=True, weekly_digest=True)
    save_lead(db, parcel(db, 0), due_date="2026-09-10")
    send = Mock(return_value="provider-id")
    monkeypatch.setattr(email_outbox, "send_email", send)

    done = scheduler.run_once(db, now=at("2026-09-14 07:05"))
    assert done == {"reminder/2026-09-14": 2, "digest/2026-W38": 2}
    assert send.call_count == 4
    recipients = sorted(call.kwargs["recipient"] for call in send.call_args_list)
    assert recipients == ["anna@example.com", "anna@example.com", "owner@example.com", "owner@example.com"]
    assert "pending@example.com" not in recipients
    subjects = {call.kwargs["subject"] for call in send.call_args_list}
    assert subjects == {"Scope: 1 fällige Wiedervorlage", "Scope: Wochenübersicht KW 38"}
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM email_outbox WHERE status='accepted'").fetchone()[0] == 4

    assert scheduler.run_once(db, now=at("2026-09-14 09:00")) == {}
    assert send.call_count == 4
    # Next day: a new reminder only; the digest waits for next Monday.
    assert scheduler.run_once(db, now=at("2026-09-15 07:00")) == {"reminder/2026-09-15": 2}


def test_run_once_respects_switches_mode_and_empty_days(db, monkeypatch):
    send = Mock(return_value="provider-id")
    monkeypatch.setattr(email_outbox, "send_email", send)
    settings(db, due_reminders=True, weekly_digest=True)
    assert scheduler.run_once(db, now=at("2026-09-11 08:00")) == {}   # nothing due, Friday
    save_lead(db, parcel(db, 0), due_date="2026-09-10")
    settings(db, due_reminders=False, weekly_digest=False)
    assert scheduler.run_once(db, now=at("2026-09-14 08:00")) == {}
    settings(db, due_reminders=True)
    monkeypatch.setenv("SCOPE_AUTH_MODE", "shared")
    assert scheduler.run_once(db, now=at("2026-09-14 08:00")) == {}
    monkeypatch.setenv("SCOPE_AUTH_MODE", "personal")
    monkeypatch.delenv("RESEND_API_KEY")
    assert scheduler.run_once(db, now=at("2026-09-14 08:00")) == {}
    send.assert_not_called()


def test_uncertain_delivery_is_not_rerendered(db, monkeypatch):
    settings(db, due_reminders=True, weekly_digest=False)
    save_lead(db, parcel(db, 0), due_date="2026-09-10")
    send = Mock(side_effect=email_outbox.EmailDeliveryError("down"))
    monkeypatch.setattr(email_outbox, "send_email", send)
    assert scheduler.run_once(db, now=at("2026-09-14 07:05")) == {"reminder/2026-09-14": 0}
    with sqlite3.connect(db) as con:
        bodies = con.execute("SELECT body FROM email_outbox WHERE status='uncertain'").fetchall()
    assert len(bodies) == 2
    save_lead(db, parcel(db, 1), due_date="2026-09-10")  # the day changes, the stored mail must not
    send.side_effect = None
    send.return_value = "provider-id"
    monkeypatch.setattr(email_outbox, "LEASE_SECONDS", 0)
    assert scheduler.run_once(db, now=at("2026-09-14 07:20")) == {"reminder/2026-09-14": 2}
    assert all(call.kwargs["text"] == bodies[0][0] for call in send.call_args_list[:1])


def test_reminder_and_digest_toggles_are_live_in_personal_mode(db, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(paths, "DB", db)
    app = AppTest.from_string(
        "import organisation, streamlit as st\n"
        f"st.session_state[{organisation.DIALOG_OPEN!r}] = True\n"
        f"st.session_state[{organisation.DIALOG_VIEW!r}] = 'settings'\n"
        "organisation.open_if_requested('2026-09-11')\n"
    ).run()
    assert not app.exception
    for field in ("weekly_digest", "due_reminders"):
        assert not app.toggle(key=f"org_profile_{field}").disabled
    assert app.toggle(key="org_profile_due_reminders").value is False  # opt-in
    app.toggle(key="org_profile_due_reminders").set_value(True).run()
    assert not app.exception
    assert organisation.load_profile(db)["due_reminders"] is True
    assert not app.toggle(key="org_profile_shared_calculations").disabled


def test_recipients_exclude_legacy_unbound_and_revoked(db):
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO organisation_members(name,email,role,status) VALUES('Legacy','legacy@example.com','Inhaber','active')")
        con.execute("UPDATE scope_access SET auth_id=NULL WHERE member_id=1")
    assert [email for _, email in scheduler._recipients(db)] == ['anna@example.com']
    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM scope_access")
    assert scheduler._recipients(db) == []


def test_revoked_after_planning_is_not_sent(db, monkeypatch):
    settings(db, due_reminders=True)
    save_lead(db, parcel(db), due_date="2026-09-10")
    original = scheduler.reminder_text

    def revoke(leads, url):
        with sqlite3.connect(db) as con:
            con.execute("DELETE FROM scope_access")
        return original(leads, url)

    monkeypatch.setattr(scheduler, "reminder_text", revoke)
    send = Mock()
    monkeypatch.setattr(email_outbox, "send_email", send)
    assert scheduler.run_once(db, now=at("2026-09-14 07:05")) == {"reminder/2026-09-14": 0}
    send.assert_not_called()
