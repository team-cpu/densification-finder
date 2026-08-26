"""Persistent shortlist and contact workflow for parcel leads.

The calculated parcel table is replaced municipality by municipality whenever
the cascade is recomputed.  User decisions must therefore live in their own
table, keyed by the stable cadastral parcel key, so a recompute cannot erase
them.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable

import pandas as pd

import paths


CONTACT_STATUS_LABELS = {
    "not_contacted": "Noch nicht kontaktiert",
    "contacted": "Kontaktiert",
    "declined": "Abgelehnt",
    "meeting_scheduled": "Termin vereinbart",
}
DEFAULT_CONTACT_STATUS = "not_contacted"


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
            "SELECT bfs, parcel, saved, hidden, owner_name, contact_status, updated_at "
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
    if owner_name is not None:
        owner_name = owner_name.strip()
        if len(owner_name) > 200:
            raise ValueError("Owner name must be 200 characters or fewer")

    assignments: list[str] = []
    values: list[object] = []
    if saved is not None:
        assignments.append("saved = ?")
        values.append(int(saved))
    if hidden is not None:
        assignments.append("hidden = ?")
        values.append(int(hidden))
    if owner_name is not None:
        assignments.append("owner_name = ?")
        values.append(owner_name)
    if contact_status is not None:
        assignments.append("contact_status = ?")
        values.append(contact_status)
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
