"""Saved screening filter presets.

A screening run is a research position, not a one-off query — "Wohnzone,
800 m² potential, Bezirk Horgen" is worth returning to, and retyping a dozen
controls to get back to it is the friction this module removes. Kept apart
from `parcel_results`, which the cascade replaces wholesale on every recompute,
for the same reason `workflow.py` is: nothing here is calculated, so nothing
here should be rebuilt.
"""
from __future__ import annotations

import json
import sqlite3

import pandas as pd

import paths

#: Long enough that nobody meets it describing a real search, short enough
#: that a paste accident cannot put a document into the picker.
NAME_LIMIT = 100


def save(name: str, filters: dict, db: str | None = None) -> None:
    """Store a named set of filter values, replacing any search of the same
    name.

    Replacing rather than accumulating is the point of keying on `name`: a
    dozen near-duplicate "Wohnzone" searches would defeat the picker as
    surely as never saving at all.
    """
    clean = str(name).strip()
    if not clean:
        raise ValueError("name must not be empty")
    if len(clean) > NAME_LIMIT:
        raise ValueError(f"name must be {NAME_LIMIT} characters or fewer")
    try:
        payload = json.dumps(filters, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"filters is not JSON-serialisable: {exc}") from None

    with sqlite3.connect(db or paths.DB) as connection:
        # INSERT OR REPLACE, not UPDATE-or-INSERT: `name` is the primary key,
        # and replacing the whole row (rather than merging into it) is what
        # makes saving under an existing name mean "this search now looks
        # like this", including a fresh `created_at`.
        connection.execute(
            "INSERT OR REPLACE INTO saved_searches (name, filters) VALUES (?, ?)",
            (clean, payload),
        )


def load(db: str | None = None) -> pd.DataFrame:
    """Every saved search, most recent first, with `filters` parsed back into
    a dict.

    Not cached by Streamlit: a save or delete in the same session must be
    visible on the very next read, the same reasoning `workflow.load` gives.
    """
    with sqlite3.connect(db or paths.DB) as connection:
        raw = pd.read_sql_query(
            "SELECT name, filters, created_at FROM saved_searches "
            "ORDER BY created_at DESC",
            connection,
        )
    # A row whose JSON is corrupt (a hand-edited database, or a future release
    # that changes the payload shape) is dropped rather than surfaced with
    # `filters=None`: every caller of `load()` would otherwise need to guard
    # against an un-appliable search, and the one place that matters — the
    # Screening picker — would rather show one fewer entry than crash for
    # every other saved search along with it.
    rows = []
    for row in raw.itertuples(index=False):
        try:
            filters = json.loads(row.filters)
        except (TypeError, ValueError):
            continue
        rows.append({"name": row.name, "filters": filters, "created_at": row.created_at})
    return pd.DataFrame(rows, columns=["name", "filters", "created_at"])


def delete(name: str, db: str | None = None) -> int:
    """Remove one saved search. Returns the number of rows removed (0 or 1)."""
    with sqlite3.connect(db or paths.DB) as connection:
        cursor = connection.execute(
            "DELETE FROM saved_searches WHERE name = ?", (str(name).strip(),)
        )
        return cursor.rowcount
