"""The acquisition board — saved leads grouped by contact stage.

Kept out of `app.py` because the decisions behind the board are worth testing
on their own: which leads are due, which column a lead belongs in, and in what
order the cards sit. Those are the functions below, and none of them touches
Streamlit.

The board replaces a saved list grouped by municipality. A lead's municipality
is still on its card; what the user works through day to day is the stage.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import detail
import workflow as WF


def leads(parcels: pd.DataFrame, decisions: pd.DataFrame, field: str) -> pd.DataFrame:
    """Saved or hidden decisions joined back to the parcel facts they name.

    An inner join deliberately: a decision whose parcel is not in the current
    result set — a municipality that has not been recomputed yet — has nothing
    to put on a card, and a blank card would suggest the lead had been lost
    rather than merely not loaded.
    """
    if decisions.empty:
        return parcels.iloc[0:0].copy()
    chosen = decisions[decisions[field].fillna(0).astype(bool)]
    if chosen.empty:
        return parcels.iloc[0:0].copy()
    return chosen.merge(
        parcels, on=["bfs", "parcel"], how="inner", validate="one_to_one"
    )


def overdue(shortlist: pd.DataFrame, today: str) -> pd.DataFrame:
    """Leads whose follow-up date has passed, earliest first.

    `today` is an ISO string parameter rather than a call to `date.today()`, so
    the boundary is testable. A lead due today is not yet overdue. A lead with
    no date is not chased at all: an empty `due_date` means nobody has decided
    when to come back, which is a different state from being late.

    ISO dates compare as strings exactly as they compare as dates, so no
    parsing is needed to sort them.
    """
    if shortlist.empty:
        return shortlist
    due = shortlist["due_date"].fillna("").astype(str)
    return shortlist[(due != "") & (due < today)].sort_values(
        "due_date", kind="stable"
    )


def _board_order(frame: pd.DataFrame) -> pd.DataFrame:
    """Soonest follow-up first within a column; undated leads last."""
    if frame.empty:
        return frame
    due = frame["due_date"].fillna("").astype(str)
    return (
        frame.assign(_undated=(due == "").astype(int), _due=due)
        .sort_values(["_undated", "_due", "parcel"], kind="stable")
        .drop(columns=["_undated", "_due"])
    )


def by_stage(shortlist: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """One frame per contact stage, in board order, including empty stages.

    Every stage is present even when nothing is in it: the board draws five
    columns on an empty database, and a column that disappeared when its last
    lead moved on would make the board change shape under the user.

    A status this release does not know — written by a newer one against the
    same volume — falls back to the first stage rather than dropping the lead
    off the board entirely.
    """
    if shortlist.empty:
        return {stage: shortlist for stage in WF.CONTACT_STATUS_LABELS}
    # `list(...)` rather than the dict: `Series.isin` does match a mapping on
    # its keys, but saying so out loud costs nothing and does not depend on it.
    known = shortlist["contact_status"].where(
        shortlist["contact_status"].isin(list(WF.CONTACT_STATUS_LABELS)),
        WF.DEFAULT_CONTACT_STATUS,
    )
    return {
        stage: _board_order(shortlist[known == stage])
        for stage in WF.CONTACT_STATUS_LABELS
    }
