"""Which of the four pages the application is showing.

Two keys, not one. Streamlit refuses a write to a widget-keyed session value
once that widget has been instantiated during the current run:

    StreamlitAPIException: `st.session_state.acq_page` cannot be modified after
    the widget with key `acq_page` is instantiated.

A button that navigates — "Analyse" on an acquisition card, "Zur Akquisition"
on the shortlist — is rendered after the navigation control, so it cannot set
the page directly. It parks a request instead, and `reconcile` applies it at
the top of the next run, before the control exists.
"""
from __future__ import annotations

import streamlit as st

#: Left to right, the order a lead actually moves through: find it, keep it,
#: work out what it is worth, then approach the owner.
PAGES = ("Screening", "Merkliste", "Analyse", "Akquisition")

DEFAULT_PAGE = PAGES[0]

#: The navigation widget's own key.
PAGE = "acq_page"

#: A parked request to move, honoured by `reconcile` on the next run.
PENDING = "acq_page_go"


def reconcile(state) -> str:
    """Apply any parked navigation request. Call before rendering the control.

    Returns the page that should now be selected.
    """
    requested = state.pop(PENDING, None)
    if requested:
        state[PAGE] = requested
    elif state.get(PAGE) not in PAGES:
        state[PAGE] = DEFAULT_PAGE
    return state[PAGE]


def go_to(page: str, state=None) -> None:
    """Ask for a page. Takes effect on the next run; the caller reruns.

    The page name is checked here rather than where it is read, so a typo
    fails at the call site instead of quietly painting an empty page.
    """
    if page not in PAGES:
        raise ValueError(f"Unknown page: {page}")
    (st.session_state if state is None else state)[PENDING] = page


def render() -> str:
    """Draw the control and return the selected page."""
    page = reconcile(st.session_state)
    st.segmented_control(
        "Navigation",
        PAGES,
        key=PAGE,
        label_visibility="collapsed",
    )
    return st.session_state.get(PAGE, page)
