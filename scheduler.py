"""Due-date reminders and the weekly digest.

`plan` decides which messages a moment in time calls for, `due_leads` /
`board_summary` read the board, `reminder_text` / `digest_text` render the
German plain-text mails, and `run_once` ties them to the durable outbox. The
container runs `python -m scheduler` beside Streamlit (see the Dockerfile),
so sending never depends on somebody having the app open.

Every message carries a stable event key (`reminder/<date>/<member>`,
`digest/<ISO week>/<member>`): a repeated run on the same day finds the key
and only retries deliveries the outbox still considers open. Message text is
rendered once, at enqueue time; a retry sends the stored text.

Spec: docs/superpowers/specs/2026-09-11-reminders-digest-design.md
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone

import email_outbox
from email_templates import notification_html
import organisation
import paths
import scope_auth
import workflow
from email_delivery import EmailDeliveryError, ResendConfig

SEND_HOUR = 7
INTERVAL_SECONDS = 600
UPCOMING_DAYS = 7


def zone():
    """Europe/Zurich; a fixed UTC+1 if the image ships without tz data."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Zurich")
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError or no zoneinfo at all
        return timezone(timedelta(hours=1), "CET")


def plan(now: datetime) -> list[tuple[str, str]]:
    """Which messages `now` calls for: a daily reminder from 07:00, the digest on Mondays."""
    if now.hour < SEND_HOUR:
        return []
    jobs = [("reminder", now.date().isoformat())]
    if now.weekday() == 0:
        year, week, _ = now.isocalendar()
        jobs.append(("digest", f"{year}-W{week:02d}"))
    return jobs


# --- Reading the board --------------------------------------------------------

_LEAD_SQL = (
    "SELECT w.bfs, w.parcel, COALESCE(p.municipality, '') AS municipality, "
    "COALESCE(p.address, '') AS address, w.contact_status, w.due_date, "
    "w.owner_name, w.contact_person, w.updated_at "
    "FROM parcel_workflow w LEFT JOIN parcel_results p ON p.bfs = w.bfs AND p.parcel = w.parcel "
    "WHERE w.saved = 1 AND w.hidden = 0"
)


def _leads(db: str) -> list[dict]:
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(_LEAD_SQL)]


def due_leads(db: str, today: str) -> list[dict]:
    """Same rule as the board's *Fällige Wiedervorlagen*: dated, due today or
    earlier, not declined; oldest first."""
    rows = [lead for lead in _leads(db)
            if lead["due_date"] and lead["due_date"] <= today and lead["contact_status"] != "declined"]
    return sorted(rows, key=lambda lead: (lead["due_date"], lead["municipality"], lead["parcel"]))


def board_summary(db: str, today: str) -> dict:
    leads = _leads(db)
    horizon = (date.fromisoformat(today) + timedelta(days=UPCOMING_DAYS)).isoformat()
    since = (date.fromisoformat(today) - timedelta(days=UPCOMING_DAYS)).isoformat()
    by_stage = {label: 0 for label in workflow.CONTACT_STATUS_LABELS.values()}
    for lead in leads:
        by_stage[workflow.CONTACT_STATUS_LABELS.get(lead["contact_status"], lead["contact_status"])] += 1
    open_dated = [lead for lead in leads if lead["due_date"] and lead["contact_status"] != "declined"]
    return {
        "today": today,
        "week": "{}-W{:02d}".format(*date.fromisoformat(today).isocalendar()[:2]),
        "total": len(leads),
        "by_stage": by_stage,
        "overdue": sum(lead["due_date"] <= today for lead in open_dated),
        "upcoming": sorted((lead for lead in open_dated if today < lead["due_date"] <= horizon),
                           key=lambda lead: lead["due_date"]),
        "recent": sum(str(lead["updated_at"] or "")[:10] >= since for lead in leads),
    }


# --- Rendering ------------------------------------------------------------------

def _german_date(iso: str) -> str:
    try:
        return date.fromisoformat(iso).strftime("%d.%m.%Y")
    except ValueError:
        return iso


def _where(lead: dict) -> str:
    """Address with the municipality, unless the address already names it."""
    address = lead["address"] or f"Parzelle {lead['parcel']}"
    town = lead["municipality"]
    if town and town not in address:
        return f"{address}, {town}"
    return address


def _lead_line(lead: dict) -> str:
    parts = [_where(lead)]
    if lead["address"]:
        parts.append(f"Parzelle {lead['parcel']}")
    parts.append(workflow.CONTACT_STATUS_LABELS.get(lead["contact_status"], lead["contact_status"]))
    parts.append(f"fällig {_german_date(lead['due_date'])}")
    contact = lead["contact_person"] or lead["owner_name"]
    if contact:
        parts.append(contact)
    return "- " + " · ".join(parts)


def _footer(url: str, switch: str) -> str:
    return (f"Board öffnen: {url}\n\n"
            f"Abstellen: Einstellungen → {switch}.")


def reminder_subject(count: int) -> str:
    return f"Scope: {count} fällige Wiedervorlage" + ("" if count == 1 else "n")


def reminder_text(leads: list[dict], url: str) -> str:
    if not leads:
        return ""
    count = len(leads)
    head = f"{count} fällige Wiedervorlage" + ("" if count == 1 else "n")
    return (head + "\n\n" + "\n".join(_lead_line(lead) for lead in leads) + "\n\n"
            + _footer(url, "Erinnerung bei fälligen Kontakten") + "\n")


def digest_subject(summary: dict) -> str:
    return f"Scope: Wochenübersicht KW {int(summary['week'].split('W')[1])}"


def digest_text(summary: dict, url: str) -> str:
    lines = [f"Wochenübersicht KW {int(summary['week'].split('W')[1])} (Stand {_german_date(summary['today'])})", ""]
    lines.append(f"Board: {summary['total']} {'Lead' if summary['total'] == 1 else 'Leads'}")
    lines.extend(f"- {label}: {count}" for label, count in summary["by_stage"].items())
    lines.append("")
    lines.append(f"Wiedervorlagen: {summary['overdue']} überfällig, "
                 f"{len(summary['upcoming'])} in den nächsten {UPCOMING_DAYS} Tagen")
    for lead in summary["upcoming"]:
        stage = workflow.CONTACT_STATUS_LABELS.get(lead["contact_status"], lead["contact_status"])
        lines.append(f"- {_german_date(lead['due_date'])} · {_where(lead)} · {stage}")
    lines.append("")
    lines.append(f"Bearbeitet in den letzten {UPCOMING_DAYS} Tagen: {summary['recent']}")
    lines.append("")
    lines.append(_footer(url, "Wöchentliche Zusammenfassung"))
    return "\n".join(lines) + "\n"


# --- Running ----------------------------------------------------------------------

def _recipients(db: str) -> list[tuple[int, str]]:
    with sqlite3.connect(db) as con:
        return [(int(member_id), email) for member_id, email in con.execute(
            "SELECT m.id, m.email FROM organisation_members m JOIN scope_access a ON a.member_id=m.id "
            "WHERE m.status = 'active' AND m.email <> '' AND a.auth_id IS NOT NULL AND a.auth_id <> '' ORDER BY m.id")]


def run_once(db: str, now: datetime | None = None) -> dict[str, int]:
    """Enqueue and deliver what `now` calls for; returns accepted counts per job key.

    Silent — an empty dict — when personal accounts, Resend or the public URL
    are not configured, when nothing is due, or when today's messages were
    already accepted.
    """
    now = now or datetime.now(zone())
    if not scope_auth.enabled():
        return {}
    try:
        config = ResendConfig.from_environment()
    except EmailDeliveryError:
        return {}
    url = os.environ.get("SCOPE_PUBLIC_URL", "").rstrip("/")
    if not url:
        return {}
    profile = organisation.load_profile(db)
    recipients = _recipients(db)
    today = now.date().isoformat()
    done: dict[str, int] = {}
    for kind, period in plan(now):
        if kind == "reminder":
            if not profile["due_reminders"]:
                continue
            leads = due_leads(db, today)
            subject, body = reminder_subject(len(leads)), reminder_text(leads, url)
        else:
            if not profile["weekly_digest"]:
                continue
            summary = board_summary(db, today)
            subject, body = digest_subject(summary), digest_text(summary, url)
        prefix = f"{kind}/{period}"
        messages: list[str] = []
        with sqlite3.connect(db) as con:
            con.execute("BEGIN IMMEDIATE")
            for member_id, email in recipients:
                key = f"{prefix}/{member_id}"
                found = email_outbox.find(con, key)
                if found is None:
                    if not body:  # nothing due: no e-mail, no record
                        continue
                    messages.append(email_outbox.enqueue(
                        con, event_key=key, recipient=email, sender=config.sender,
                        subject=subject, body=body, body_html=notification_html(subject, body, url)))
                elif found[1] != "accepted":
                    messages.append(found[0])
        if not messages:
            continue
        done[prefix] = 0
        for message_id in messages:
            # Recheck after enqueue: a grant may have been revoked during planning.
            with sqlite3.connect(db) as con:
                recipient = con.execute("SELECT recipient FROM email_outbox WHERE id=?", (message_id,)).fetchone()
            if recipient and recipient[0] in {email for _, email in _recipients(db)}:
                done[prefix] += email_outbox.deliver(db, message_id, config=config) == "accepted"
    return done


def serve(interval: int = INTERVAL_SECONDS) -> None:
    """Loop forever; a failing pass is logged and retried at the next interval."""
    while True:
        try:
            done = run_once(paths.DB)
            if done:
                print(f"scheduler: {json.dumps(done)}", flush=True)
        except Exception as exc:  # noqa: BLE001 - keep the sidecar alive
            print(f"scheduler: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    if "--once" in sys.argv[1:]:
        print(json.dumps(run_once(paths.DB)))
    else:
        serve()
