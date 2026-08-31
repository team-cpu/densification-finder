"""Persistent shortlist and contact workflow for parcel leads.

The calculated parcel table is replaced municipality by municipality whenever
the cascade is recomputed.  User decisions must therefore live in their own
table, keyed by the stable cadastral parcel key, so a recompute cannot erase
them.
"""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from datetime import date

import pandas as pd

import paths


#: Dictionary order is the acquisition board's column order, left to right —
#: the sequence a lead actually moves through. The codes are stable; only the
#: labels are display. `contacted` is shown as "Brief versandt" because that is
#: the step it has always meant in practice, and renaming the code would mean
#: rewriting stored values for a caption.
CONTACT_STATUS_LABELS = {
    "not_contacted": "Nicht kontaktiert",
    "contacted": "Brief versandt",
    "in_discussion": "Im Gespräch",
    "meeting_scheduled": "Termin vereinbart",
    "declined": "Abgelehnt",
}
DEFAULT_CONTACT_STATUS = "not_contacted"

#: How long each free-text acquisition field may be. The limits are generous
#: enough that nobody meets them in normal use and small enough that a paste
#: accident cannot put a document into a database column.
TEXT_LIMITS = {
    "owner_name": 200,
    "contact_person": 200,
    "phone": 50,
    "email": 200,
    "next_step": 300,
    "note": 1000,
}

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _text(field: str, value: str) -> str:
    """Trim a free-text field and hold it to its documented length."""
    value = str(value).strip()
    limit = TEXT_LIMITS[field]
    if len(value) > limit:
        raise ValueError(f"{field} must be {limit} characters or fewer")
    return value


def _date(field: str, value: str) -> str:
    """An ISO date, or the empty string for "no date decided yet".

    Checked twice: the shape, so that a Swiss `02.09.2026` is refused rather
    than silently sorted as text, and then the calendar, so that `2026-02-30`
    is refused as well.
    """
    value = str(value).strip()
    if not value:
        return ""
    if not _ISO_DATE.match(value):
        raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD) or empty")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field} is not a date on the calendar") from None
    return value


def _keys(keys: Iterable[tuple[int, str]]) -> list[tuple[int, str]]:
    """Normalise and deduplicate database keys before a batch update."""
    return sorted({(int(bfs), str(parcel)) for bfs, parcel in keys})


def load(db: str | None = None) -> pd.DataFrame:
    """Return every stored parcel decision.

    ``ingest.schema`` is run during application bootstrap, so the table exists
    before this function is called.  Keeping the read uncached is intentional:
    actions in the same Streamlit session must be visible immediately.
    """
    with sqlite3.connect(db or paths.DB) as connection:
        return pd.read_sql_query(
            "SELECT bfs, parcel, saved, hidden, owner_name, contact_status, "
            "due_date, last_contact, next_step, note, contact_person, "
            "phone, email, updated_at "
            "FROM parcel_workflow",
            connection,
        )


def update(
    keys: Iterable[tuple[int, str]],
    *,
    saved: bool | None = None,
    hidden: bool | None = None,
    owner_name: str | None = None,
    contact_status: str | None = None,
    due_date: str | None = None,
    last_contact: str | None = None,
    next_step: str | None = None,
    note: str | None = None,
    contact_person: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    db: str | None = None,
) -> int:
    """Persist one or more parcel decisions atomically.

    Fields omitted by the caller keep their current value.  A row is created
    with conservative defaults before it is updated, which makes the same
    operation work for both new and existing leads.
    """
    parcel_keys = _keys(keys)
    if not parcel_keys:
        return 0
    if contact_status is not None and contact_status not in CONTACT_STATUS_LABELS:
        raise ValueError(f"Unknown contact status: {contact_status}")

    # Validated before a single write, so a bad date in one field cannot leave
    # the other fields of the same save applied.
    text = {
        "owner_name": owner_name,
        "next_step": next_step,
        "note": note,
        "contact_person": contact_person,
        "phone": phone,
        "email": email,
    }
    dates = {"due_date": due_date, "last_contact": last_contact}
    clean = {
        field: _text(field, value)
        for field, value in text.items()
        if value is not None
    }
    clean.update(
        {
            field: _date(field, value)
            for field, value in dates.items()
            if value is not None
        }
    )

    assignments: list[str] = []
    values: list[object] = []
    if saved is not None:
        assignments.append("saved = ?")
        values.append(int(saved))
    if hidden is not None:
        assignments.append("hidden = ?")
        values.append(int(hidden))
    if contact_status is not None:
        assignments.append("contact_status = ?")
        values.append(contact_status)
    for field, value in clean.items():
        assignments.append(f"{field} = ?")
        values.append(value)
    if not assignments:
        return 0

    assignments.append("updated_at = CURRENT_TIMESTAMP")
    with sqlite3.connect(db or paths.DB) as connection:
        connection.executemany(
            "INSERT OR IGNORE INTO parcel_workflow (bfs, parcel) VALUES (?, ?)",
            parcel_keys,
        )
        connection.executemany(
            f"UPDATE parcel_workflow SET {', '.join(assignments)} "
            "WHERE bfs = ? AND parcel = ?",
            [tuple(values) + key for key in parcel_keys],
        )
    return len(parcel_keys)


def set_saved(
    keys: Iterable[tuple[int, str]], value: bool, db: str | None = None
) -> int:
    return update(keys, saved=value, db=db)


def set_hidden(
    keys: Iterable[tuple[int, str]], value: bool, db: str | None = None
) -> int:
    return update(keys, hidden=value, db=db)


def set_contact_status(
    keys: Iterable[tuple[int, str]], status: str, db: str | None = None
) -> int:
    return update(keys, contact_status=status, db=db)


def set_owner_name(
    keys: Iterable[tuple[int, str]], owner_name: str, db: str | None = None
) -> int:
    return update(keys, owner_name=owner_name, db=db)
