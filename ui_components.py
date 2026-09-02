"""Local, dependency-free Streamlit components for the two design-native lists.

Both frontends speak Streamlit's small component protocol directly. Keeping the
HTML and JavaScript in this repository avoids a Node build in the Docker image
and avoids putting acquisition data through an unmaintained third-party wheel.
The components only return intent (move/open/edit); Python still validates the
parcel and performs every database or navigation change.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components


_ROOT = Path(__file__).with_name("components")
_MERKLISTE = components.declare_component(
    "scope_merkliste", path=str(_ROOT / "merkliste")
)
_SCREENING_TABLE = components.declare_component(
    "scope_screening_table", path=str(_ROOT / "screening_table")
)
_ACQUISITION_BOARD = components.declare_component(
    "scope_acquisition_board", path=str(_ROOT / "acquisition_board")
)


def merkliste_table(rows: list[dict], *, key: str):
    """Render the prototype's shortlist table and return its latest action."""
    return _MERKLISTE(rows=rows, key=key, default=None)


def screening_table(rows: list[dict], *, dismissed: list[dict] | None = None, key: str):
    """Render the prototype's screening table and return its latest row action."""
    return _SCREENING_TABLE(
        rows=rows,
        dismissed=dismissed or [],
        key=key,
        default=None,
    )


def acquisition_board(stages: list[dict], *, key: str):
    """Render the five-stage drag-and-drop board and return its latest action."""
    return _ACQUISITION_BOARD(stages=stages, key=key, default=None)


def consume_event(event, scope: str, state=None):
    """Return a component event once, even though its value survives reruns.

    Streamlit retains a component's last value under its widget key. A move
    therefore remains the return value on the rerun caused by the database
    update. The frontend attaches an event id; remembering it here makes that
    retained value harmless while allowing the next distinct event through.
    """
    if not isinstance(event, dict) or not isinstance(event.get("eventId"), str):
        return None
    target = st.session_state if state is None else state
    marker = f"_component_event_{scope}"
    if target.get(marker) == event["eventId"]:
        return None
    target[marker] = event["eventId"]
    return event
