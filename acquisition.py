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

#: How many rows the due-follow-up preview shows. The design caps it: the
#: section is a glance at what needs chasing, not a second copy of the board.
DUE_PREVIEW = 4


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
    """Leads whose follow-up date is today or earlier, earliest first,
    excluding leads marked `declined`.

    `today` is an ISO string parameter rather than a call to `date.today()`, so
    the boundary is testable. The rule is ported from the design prototype's
    own `overdue` flag (`dd <= TODAY`): a lead due today is due today, not
    tomorrow — a `<` boundary here meant a follow-up sat unmentioned until the
    day after it actually needed chasing. A `declined` lead is never due
    regardless of its date: the status already means the owner said no, and a
    lead like "no interest, revisit 2027" must not nag every day until then. A
    lead with no date is not chased at all: an empty `due_date` means nobody
    has decided when to come back, which is a different state from being
    late.

    ISO dates compare as strings exactly as they compare as dates, so no
    parsing is needed to sort them.
    """
    if shortlist.empty:
        return shortlist
    due = shortlist["due_date"].fillna("").astype(str)
    not_declined = shortlist["contact_status"] != "declined"
    return shortlist[(due != "") & (due <= today) & not_declined].sort_values(
        "due_date", kind="stable"
    )


def due_items(
    shortlist: pd.DataFrame, today: str, limit: int = DUE_PREVIEW
) -> pd.DataFrame:
    """The follow-up preview: overdue leads first, then dated leads not yet
    due, each group earliest first, capped at `limit` rows.

    Ported from the design prototype's `dueItems`: overdue leads concatenated
    with not-yet-due dated leads (declined and undated leads excluded from
    both), then sliced to four. Showing only the overdue rows told the user
    what is already late but nothing about what is coming, so the desk looked
    clear the moment the last overdue lead was cleared even with a wall of
    follow-ups due next week.
    """
    if shortlist.empty:
        return shortlist
    late = overdue(shortlist, today)
    due = shortlist["due_date"].fillna("").astype(str)
    not_declined = shortlist["contact_status"] != "declined"
    upcoming = shortlist[(due != "") & (due > today) & not_declined].sort_values(
        "due_date", kind="stable"
    )
    return pd.concat([late, upcoming]).head(limit)


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


def _swiss(value: float) -> str:
    """1'740, not 1,740 — the separator the cadastre and the canton use."""
    return f"{value:,.0f}".replace(",", "’")


def _or_dash(value) -> str:
    return str(value) if pd.notna(value) and str(value).strip() else "—"


def render(parcels, decisions, db, today, price_of):
    """The acquisition board, and the recoverable list of hidden parcels.

    `price_of` is passed in rather than imported: resolving a land-price
    reference needs the loaded `land_prices.csv`, which belongs to `app.py`,
    and two lookups that disagreed about the most specific matching row would
    put one number in the table and another on the card.
    """
    st.divider()
    st.subheader("Akquisition — Eigentümerdialog")
    st.caption(
        "Kontaktstand je Parzelle und Eigentümerschaft. Das Wiedervorlagedatum "
        "steuert die Fälligkeit, die Stufe wird auf der Karte geändert. "
        "Eigentümerangaben werden weiterhin von Hand im AGIS nachgeschlagen."
    )

    shortlist = leads(parcels, decisions, "saved")
    if shortlist.empty:
        st.info("Noch keine Parzellen gespeichert.")
    else:
        _render_overdue(shortlist, today)
        _render_board(shortlist, db, price_of)

    _render_hidden(parcels, decisions, db)


def _render_overdue(shortlist, today):
    """Hidden entirely when nothing is due, rather than shown as an empty frame
    — an empty table reads as a broken query, not as a clear desk.

    The rows are `due_items` (overdue leads, then a look-ahead at what is
    coming, capped), but the badge counts `overdue` alone — the badge answers
    "how many need chasing right now", and counting the look-ahead rows too
    would make it lie the moment the preview shows anything upcoming.
    """
    rows = due_items(shortlist, today)
    if rows.empty:
        return
    overdue_count = len(overdue(shortlist, today))
    st.markdown(f"**Fällige Wiedervorlagen** · {overdue_count} offen")
    frame = pd.DataFrame(
        {
            "Wiedervorlage": rows["due_date"],
            "Adresse": rows["address"].map(_or_dash),
            "Gemeinde": rows["municipality"],
            "Parzelle": rows["parcel"],
            "Eigentümerschaft": rows["owner_name"].map(_or_dash),
            "Stufe": rows["contact_status"].map(
                lambda code: WF.CONTACT_STATUS_LABELS.get(
                    code, WF.CONTACT_STATUS_LABELS[WF.DEFAULT_CONTACT_STATUS]
                )
            ),
            "Nächster Schritt": rows["next_step"].map(_or_dash),
        }
    # `due_items` puts overdue rows first but keeps `shortlist`'s original
    # (non-contiguous) index; the tint below picks rows by position, so it
    # needs a plain 0..n-1 index or it would tint the wrong cells whenever the
    # original index skipped or reordered.
    ).reset_index(drop=True)
    # `due_items` orders overdue leads first, so the first `overdue_count`
    # positions in `frame` are exactly the overdue ones — a subset built from
    # that row range, rather than an `axis=1` scan of every row, says so
    # directly instead of re-deriving it from `Wiedervorlage <= today`.
    overdue_rows = frame.index[:overdue_count]
    styled = frame.style.map(
        lambda _: "background-color:#fdf5e7;color:#8a5a12",
        subset=(overdue_rows, "Wiedervorlage"),
    )
    st.dataframe(styled, hide_index=True, width="stretch")


def _render_board(shortlist, db, price_of):
    stages = by_stage(shortlist)
    columns = st.columns(len(WF.CONTACT_STATUS_LABELS))
    for column, (stage, label) in zip(columns, WF.CONTACT_STATUS_LABELS.items()):
        frame = stages[stage]
        with column:
            st.markdown(f"**{label}** · {len(frame)}")
            for _, row in frame.iterrows():
                _render_card(row, db, price_of)


def _render_card(row, db, price_of):
    key = int(row["bfs"]), str(row["parcel"])
    slug = f"{key[0]}_{key[1]}"
    reference = price_of(row)
    land_value = (
        row["area"] * reference.price_chf_m2
        if reference is not None and pd.notna(row["area"])
        else None
    )
    stored = (
        row["contact_status"]
        if row["contact_status"] in WF.CONTACT_STATUS_LABELS
        else WF.DEFAULT_CONTACT_STATUS
    )

    with st.container(border=True):
        st.markdown(f"**{_or_dash(row['address'])}**")
        st.caption(f"{row['municipality']} · {row['parcel']}")
        st.text(f"Potenzial  {_swiss(row['delta'])} m²")
        st.text(
            "Landwert   —"
            if land_value is None
            else f"Landwert   CHF {_swiss(land_value)}"
        )
        st.caption(str(row["owner_name"]).strip() or "Eigentümer nicht erfasst")
        st.caption(f"{_or_dash(row['last_contact'])} → {_or_dash(row['due_date'])}")
        if str(row["next_step"]).strip():
            st.caption(row["next_step"])

        stage = st.selectbox(
            "Stufe",
            list(WF.CONTACT_STATUS_LABELS),
            index=list(WF.CONTACT_STATUS_LABELS).index(stored),
            format_func=lambda code: WF.CONTACT_STATUS_LABELS[code],
            key=f"stage_{slug}",
            label_visibility="collapsed",
        )
        if stage != stored:
            WF.update([key], contact_status=stage, db=db)
            st.rerun()

        if st.button("Analyse", key=f"open_{slug}", width="stretch"):
            detail.open_parcel(detail.parcel_id(row))
            st.rerun()

        with st.expander("Kontaktdetails"):
            _render_contact_form(row, key, slug, db)


def _render_contact_form(row, key, slug, db):
    """Dates are text rather than `st.date_input` because "no date yet" is a
    real and common state here, and a date picker has to invent a day to show.
    The format is stated in the placeholder and enforced by `workflow.update`,
    which reports what it refused."""
    with st.form(f"contact_{slug}"):
        owner_name = st.text_input(
            "Eigentümerschaft", value=str(row["owner_name"]), max_chars=200
        )
        contact_person = st.text_input(
            "Kontaktperson",
            value=str(row["contact_person"]),
            max_chars=200,
            placeholder="Name, Funktion",
        )
        phone = st.text_input(
            "Telefon", value=str(row["phone"]), max_chars=50, placeholder="+41 …"
        )
        email = st.text_input(
            "E-Mail",
            value=str(row["email"]),
            max_chars=200,
            placeholder="name@domain.ch",
        )
        last_contact = st.text_input(
            "Letzter Kontakt",
            value=str(row["last_contact"]),
            max_chars=10,
            placeholder="JJJJ-MM-TT",
        )
        due_date = st.text_input(
            "Wiedervorlage",
            value=str(row["due_date"]),
            max_chars=10,
            placeholder="JJJJ-MM-TT",
        )
        next_step = st.text_input(
            "Nächster Schritt", value=str(row["next_step"]), max_chars=300
        )
        note = st.text_area("Notiz", value=str(row["note"]), max_chars=1000)
        store = st.form_submit_button("Speichern")
        remove = st.form_submit_button("Von Merkliste entfernen")

    if remove:
        WF.set_saved([key], False, db)
        st.toast("Parzelle von der Merkliste entfernt.")
        st.rerun()
    if not store:
        return
    try:
        WF.update(
            [key],
            owner_name=owner_name,
            contact_person=contact_person,
            phone=phone,
            email=email,
            last_contact=last_contact,
            due_date=due_date,
            next_step=next_step,
            note=note,
            db=db,
        )
    except ValueError as error:
        st.error(str(error))
        return
    st.toast("Kontaktdaten gespeichert.")
    st.rerun()


def _render_hidden(parcels, decisions, db):
    """Carried across from the old panel unchanged. Hiding a parcel is the one
    destructive-looking action in the list, and it stays recoverable."""
    hidden = leads(parcels, decisions, "hidden")
    if hidden.empty:
        return
    ordered = hidden.sort_values(["municipality", "parcel"], kind="stable")
    options = [(int(row["bfs"]), str(row["parcel"])) for _, row in ordered.iterrows()]
    labels = {
        (int(row["bfs"]), str(row["parcel"])): (
            f"{row['municipality']} · Parzelle {row['parcel']} · "
            f"{_or_dash(row['address'])}"
        )
        for _, row in ordered.iterrows()
    }
    with st.expander(f"Ausgeblendete Parzellen · {len(hidden)}"):
        restore = st.multiselect(
            "Wieder in der Ergebnisliste anzeigen",
            options,
            format_func=lambda key: labels[key],
            key="restore_hidden_selection",
        )
        if st.button(
            "Auswahl wieder anzeigen",
            key="restore_hidden_button",
            disabled=not restore,
        ):
            WF.set_hidden(restore, False, db)
            st.toast(f"{len(restore)} Parzelle(n) wieder eingeblendet.")
            st.rerun()
