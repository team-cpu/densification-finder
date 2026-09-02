"""The acquisition board — saved leads grouped by contact stage.

Kept out of `app.py` because the decisions behind the board are worth testing
on their own: which leads are due, which column a lead belongs in, and in what
order the cards sit. Those are the functions below, and none of them touches
Streamlit.

The board replaces a saved list grouped by municipality. A lead's municipality
is still on its card; what the user works through day to day is the stage.
"""
from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

import detail
import formatting as F
import navigation
import workflow as WF

#: How many rows the due-follow-up preview shows. The design caps it: the
#: section is a glance at what needs chasing, not a second copy of the board.
DUE_PREVIEW = 4

#: Which lead's contact dialog is open, as `bfs:parcel`, or absent for none.
#:
#: A dialog cannot be driven from `if st.button(...): open_dialog()` — the
#: button that reads `True` when the user clicks "Eigentümer" reads `False`
#: again on the rerun that `Speichern` itself triggers inside the dialog, so
#: the dialog body (and the save it contains) never runs a second time.
#: Session state survives that rerun, so the dialog keeps reopening itself on
#: every rerun until something explicitly pops this key.
CONTACT_OPEN = "acquisition_contact_open"

#: Scoped to the board's own keyed container (`st-key-acq_board`) so it does
#: not touch the unrelated `st.columns` layouts elsewhere in `app.py`. Five
#: fixed-width columns held their shape at any viewport, which shredded
#: addresses and stage labels into unreadable fragments around 683px.
#:
#: The wrap is gated behind a viewport query rather than applied always. A
#: floor width is measured against Streamlit's content area, which is narrower
#: than the window, so an ungated `min-width` broke the desktop case it was
#: supposed to leave alone: at a 1440px window five 260px columns plus their
#: gaps no longer fit the content area and the fifth stage wrapped to a
#: full-width row of its own. Above the breakpoint the board keeps Streamlit's
#: own five-column behaviour and sets no floor at all.
#:
#: 1150px is measured, not guessed. Five columns stay readable down to about
#: 1150px (185px each); at 1120px the stage labels begin to truncate. The
#: breakpoint sits at the top of that band so the board wraps just before the
#: labels break rather than just after.
_BOARD_CSS = """
<style>
.st-key-acq_board { margin-top: 18px; }
.st-key-acq_board > [data-testid="stVerticalBlock"] > [data-testid="stHorizontalBlock"] {
  align-items: flex-start;
}
[class*="st-key-acq_stage_"] {
  padding: 9px;
  border: 1px solid #eaeaee;
  border-radius: 9px;
  background: #f7f7f9;
}
.acq-stage-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
  color: #4a4a52;
  font-size: 11.5px;
  font-weight: 600;
}
.acq-stage-count {
  min-width: 20px;
  padding: 1px 6px;
  border-radius: 999px;
  background: #e9e9ed;
  color: #77777f;
  font-family: "IBM Plex Mono", monospace;
  font-size: 10px;
  text-align: center;
}
[class*="st-key-acq_card_"] {
  margin-bottom: 8px;
  padding: 10px !important;
  border-color: #e4e4e9 !important;
  background: #fff;
  box-shadow: 0 1px 2px rgba(23,23,27,.03);
}
[class*="st-key-acq_card_"] [data-testid="stCaptionContainer"] {
  font-size: 11px;
}
@media (max-width: 1150px) {
  .st-key-acq_board [data-testid="stHorizontalBlock"] { flex-wrap: wrap; }
  .st-key-acq_board [data-testid="stColumn"] { min-width: 260px; }
  .st-key-acq_board [class*="st-key-acq_card_"] [data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap;
  }
  .st-key-acq_board [class*="st-key-acq_card_"] [data-testid="stColumn"] {
    min-width: 0;
  }
}
</style>
"""


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
    return F.swiss(value)


def _or_dash(value) -> str:
    return str(value) if pd.notna(value) and str(value).strip() else "—"


def contact_list(shortlist: pd.DataFrame) -> pd.DataFrame:
    """The saved leads as a flat table, for a mail merge or a phone list.

    Owner details are typed in by hand from the AGIS extract, so this exports
    what the user recorded and invents nothing.
    """
    if shortlist.empty:
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "Adresse": shortlist["address"].map(_or_dash),
            "Gemeinde": shortlist["municipality"],
            "Parzelle": shortlist["parcel"],
            "Potenzial m²": shortlist["delta"].round(0),
            "Eigentümerschaft": shortlist["owner_name"],
            "Kontaktperson": shortlist["contact_person"],
            "Telefon": shortlist["phone"],
            "E-Mail": shortlist["email"],
            "Stufe": shortlist["contact_status"].map(
                lambda code: WF.CONTACT_STATUS_LABELS.get(
                    code, WF.CONTACT_STATUS_LABELS[WF.DEFAULT_CONTACT_STATUS]
                )
            ),
            "Letzter Kontakt": shortlist["last_contact"],
            "Wiedervorlage": shortlist["due_date"],
            "Nächster Schritt": shortlist["next_step"],
            "Notiz": shortlist["note"],
        }
    )


def render(parcels, decisions, db, today, price_of):
    """The acquisition board, and the recoverable list of hidden parcels.

    `price_of` is passed in rather than imported: resolving a land-price
    reference needs the loaded `land_prices.csv`, which belongs to `app.py`,
    and two lookups that disagreed about the most specific matching row would
    put one number in the table and another on the card.
    """
    st.html(_ACQUISITION_INTRO)

    shortlist = leads(parcels, decisions, "saved")
    if shortlist.empty:
        st.info("Noch keine Parzellen gespeichert.")
    else:
        _render_overdue(shortlist, today)
        _render_board(shortlist, db, price_of)
        st.download_button(
            "Kontaktliste exportieren",
            contact_list(shortlist).to_csv(index=False).encode("utf-8"),
            file_name="akquisition-kontakte.csv",
            mime="text/csv",
            key="acq_contacts_csv",
        )

    _render_hidden(parcels, decisions, db)


#: Five compact cells from the design: date; parcel identity; owner plus next
#: step; stage; actions. Related fields stay together instead of each taking a
#: separate Streamlit column and squeezing both action labels into two lines.
_DUE_ROW_WIDTHS = [1.05, 2.3, 2.6, 1.25, 1.8]

#: Matches the overdue tint the dataframe `Styler` used to apply to the
#: `Wiedervorlage` cell — carried over verbatim so the list still tells late
#: from merely scheduled apart now that a plain `st.write` can't be styled.
_OVERDUE_TINT = "background-color:#fdf5e7;color:#8a5a12;padding:1px 6px;border-radius:3px"

_ACQUISITION_INTRO = """
<style>
.acquisition-page-intro { margin: 10px 0 20px; }
.acquisition-page-kicker {
  margin-bottom: 7px;
  color: #9a9aa6;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: .1em;
  text-transform: uppercase;
}
.acquisition-page-intro h1 {
  margin: 0;
  font-size: 21px;
  font-weight: 600;
  letter-spacing: -.015em;
}
.acquisition-page-intro p {
  max-width: 70ch;
  margin: 7px 0 0;
  color: #77777f;
  font-size: 12.5px;
  text-wrap: pretty;
}
@media (max-width: 760px) {
  .acquisition-page-intro { margin: 8px 0 16px; }
  .acquisition-page-intro h1 { font-size: 19px; }
}
</style>
<div class="acquisition-page-intro">
  <div class="acquisition-page-kicker">Akquisition</div>
  <h1>Eigentümer-Dialog</h1>
  <p>Kontaktstand je Parzelle und Eigentümerschaft. Das Wiedervorlagedatum
     steuert die Fälligkeit, die Stufe wird auf der Karte geändert.
     Eigentümerangaben werden weiterhin von Hand im AGIS nachgeschlagen.</p>
</div>
"""

_DUE_CSS = """
<style>
.acq-mobile-label { display: none; }
.st-key-acq_due {
  padding: 0 14px 4px;
  border: 1px solid #eaeaee;
  border-radius: 9px;
  background: #fff;
  overflow: hidden;
}
.st-key-acq_due_title {
  margin: 0 -14px;
  padding: 9px 14px;
  border-bottom: 1px solid #f0f0f3;
  background: #fbfbfc;
}
.st-key-acq_due_title [data-testid="stHorizontalBlock"] { align-items: center; }
.st-key-acq_due_title [data-testid="stCaptionContainer"] { text-align: right; }
[class*="st-key-acq_due_row_"] {
  padding: 8px 0;
  border-bottom: 1px solid #f0f0f3;
}
[class*="st-key-acq_due_row_"]:last-child { border-bottom: 0; }
.acq-due-primary { font-size: 12px; font-weight: 600; color: #2f2f37; }
.acq-due-secondary { margin-top: 2px; font-size: 10.5px; color: #8a8a94; }

@media (max-width: 760px) {
  [class*="st-key-acq_due_row_"] {
    padding: 12px;
    margin-bottom: 10px;
    border: 1px solid #eaeaee;
    border-radius: 9px;
    background: #fff;
  }

  [class*="st-key-acq_due_row_"] [data-testid="stHorizontalBlock"] {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 9px 12px;
  }

  [class*="st-key-acq_due_row_"] [data-testid="stColumn"] {
    width: auto !important;
    min-width: 0;
  }

  [class*="st-key-acq_due_row_"] [data-testid="stColumn"]:nth-child(2),
  [class*="st-key-acq_due_row_"] [data-testid="stColumn"]:nth-child(3),
  [class*="st-key-acq_due_row_"] [data-testid="stColumn"]:nth-child(5) {
    grid-column: 1 / -1;
  }

  .acq-mobile-label {
    display: block;
    margin-bottom: 2px;
    color: #9a9aa6;
    font-size: 9.5px;
    font-weight: 600;
    letter-spacing: .07em;
    text-transform: uppercase;
  }

  [class*="st-key-acq_due_row_"] [data-testid="stButton"] button {
    white-space: nowrap;
  }
}
</style>
"""


def _render_overdue(shortlist, today):
    """Hidden entirely when nothing is due, rather than shown as an empty frame
    — an empty table reads as a broken query, not as a clear desk.

    The rows are `due_items` (overdue leads, then a look-ahead at what is
    coming, capped), but the badge counts `overdue` alone — the badge answers
    "how many need chasing right now", and counting the look-ahead rows too
    would make it lie the moment the preview shows anything upcoming.

    Drawn as widgets, one row per lead, rather than a read-only `st.dataframe`
    — the whole point of this section is acting on a lead first thing in the
    morning without first hunting for its card among five stage columns.
    """
    rows = due_items(shortlist, today)
    if rows.empty:
        return
    overdue_count = len(overdue(shortlist, today))
    with st.container(key="acq_due"):
        st.html(_DUE_CSS)
        with st.container(key="acq_due_title"):
            due_heading, due_today = st.columns([4, 1])
            due_heading.markdown(
                f"**Fällige Wiedervorlagen** · {overdue_count} offen"
            )
            due_today.caption(f"Heute · {escape(str(today))}")

        # `due_items` orders overdue leads first, so the first `overdue_count`
        # positions are exactly the overdue ones — a position check against that
        # count, rather than re-deriving "is this row late" from its own due
        # date, says so directly instead of duplicating `overdue`'s own rule.
        for position, (_, row) in enumerate(rows.iterrows()):
            _render_due_row(row, position < overdue_count)


def _render_due_row(row, is_overdue):
    key = int(row["bfs"]), str(row["parcel"])
    slug = f"{key[0]}_{key[1]}"
    with st.container(key=f"acq_due_row_{slug}"):
        columns = st.columns(_DUE_ROW_WIDTHS, vertical_alignment="center")

        for column, label in zip(
            columns,
            ("Wiedervorlage", "Parzelle", "Kontakt", "Stufe", "Aktionen"),
        ):
            if label:
                column.html(f'<span class="acq-mobile-label">{label}</span>')

        if is_overdue:
            # Escaped even though `workflow._date` only ever stores a strict
            # YYYY-MM-DD: this is the one field on the row interpolated into markup
            # rather than written through `st.write`, and the database is a file on
            # a volume that can be edited by hand. Relying on a validator three
            # modules away to keep this safe would make a change over there a hole
            # over here, silently.
            columns[0].markdown(
                f'<span style="{_OVERDUE_TINT}">{escape(str(row["due_date"]))}</span>',
                unsafe_allow_html=True,
            )
        else:
            columns[0].write(row["due_date"])
        columns[1].markdown(
            '<div class="acq-due-primary">'
            + escape(_or_dash(row["address"]))
            + '</div><div class="acq-due-secondary">'
            + escape(f"{row['municipality']} · Parzelle {row['parcel']}")
            + "</div>",
            unsafe_allow_html=True,
        )
        columns[2].markdown(
            '<div class="acq-due-primary">'
            + escape(_or_dash(row["owner_name"]))
            + '</div><div class="acq-due-secondary">'
            + escape(_or_dash(row["next_step"]))
            + "</div>",
            unsafe_allow_html=True,
        )
        stage = (
            row["contact_status"]
            if row["contact_status"] in WF.CONTACT_STATUS_LABELS
            else WF.DEFAULT_CONTACT_STATUS
        )
        columns[3].write(WF.CONTACT_STATUS_LABELS[stage])
        action_owner, action_analyse = columns[4].columns(2)
        with action_owner:
            _eigentuemer_button(key, f"due_contact_{slug}")
        with action_analyse:
            _analyse_button(row, f"due_open_{slug}")


def _render_board(shortlist, db, price_of):
    stages = by_stage(shortlist)
    with st.container(key="acq_board"):
        # Once per render, not once per card — a `<style>` tag is idempotent
        # on the page, so 25 identical copies would cost 25x the HTML for the
        # same effect a single one already has.
        st.html(_BOARD_CSS)
        columns = st.columns(len(WF.CONTACT_STATUS_LABELS))
        for column, (stage, label) in zip(columns, WF.CONTACT_STATUS_LABELS.items()):
            frame = stages[stage]
            with column:
                with st.container(key=f"acq_stage_{stage}"):
                    st.markdown(
                        '<div class="acq-stage-heading"><span>'
                        + escape(label)
                        + '</span><span class="acq-stage-count">'
                        + str(len(frame))
                        + "</span></div>",
                        unsafe_allow_html=True,
                    )
                    for _, row in frame.iterrows():
                        _render_card(row, db, price_of)

    _render_open_contact_dialog(shortlist, db)


def _render_open_contact_dialog(shortlist, db):
    """Runs once after the board, not from inside a card, because the open
    lead can be in any column (or, once removed, in none of them) — finding
    it by the session key instead of by card position is what lets a card's
    own rerun close the dialog it opened."""
    open_pid = st.session_state.get(CONTACT_OPEN)
    if open_pid is None:
        return
    row = _lead_by_pid(shortlist, open_pid)
    if row is None:
        # Removed ("Von Merkliste entfernen") or moved out of the shortlist
        # since the dialog was opened — nothing left to edit, and re-raising
        # here would trade a closed dialog for a crashed board.
        st.session_state.pop(CONTACT_OPEN, None)
        return
    key = (int(row["bfs"]), str(row["parcel"]))
    _contact_dialog(row, key, db)


def _lead_by_pid(shortlist, pid):
    """The shortlist row named by a `bfs:parcel` pid, or `None`.

    Matches on the same two-part key `_render_card` builds, not on the
    shortlist's pandas index — that index is not part of the pid and is free
    to change (a stage move or a hidden lead reorders `by_stage`'s frames).
    """
    bfs_part, _, parcel_part = str(pid).partition(":")
    try:
        bfs = int(bfs_part)
    except ValueError:
        return None
    match = shortlist[
        (shortlist["bfs"] == bfs) & (shortlist["parcel"].astype(str) == parcel_part)
    ]
    return None if match.empty else match.iloc[0]


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

    with st.container(key=f"acq_card_{slug}", border=True):
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

        analyse_action, owner_action = st.columns(2)
        with analyse_action:
            _analyse_button(row, f"open_{slug}")
        with owner_action:
            _eigentuemer_button(key, f"contact_{slug}")


def _analyse_button(row, widget_key):
    """Shared by the card and the follow-up list so a lead opens the same
    parcel no matter which surface it was opened from — the caller picks the
    widget key because a due lead is drawn twice (once here, once on its own
    card) and the two copies would collide on a shared `open_{slug}`."""
    if st.button("Analyse", key=widget_key, width="stretch"):
        detail.open_parcel(detail.parcel_id(row))
        navigation.go_to("Analyse")
        st.rerun()


def _eigentuemer_button(key, widget_key):
    """Opens `_contact_dialog` for `key`, shared by the card and the
    follow-up list — a lead's contact data lives at one `bfs`/`parcel` pair,
    so both surfaces route through the same `CONTACT_OPEN` write rather than
    each carrying its own copy of how a dialog gets opened."""
    if st.button("Eigentümer", key=widget_key, width="stretch"):
        st.session_state[CONTACT_OPEN] = f"{key[0]}:{key[1]}"
        st.rerun()


@st.dialog("Kontaktdetails")
def _contact_dialog(row, key, db):
    """The contact form for one lead, built once per open dialog rather than
    once per card. A per-card `st.expander` built the same 9 widgets whether
    or not it was open — at 100 leads that was ~900 invisible widgets the
    browser still carried. A single dialog for the session's one open lead
    turns that into 9 widgets that exist only while someone is actually
    looking at them.

    Dates are text rather than `st.date_input` because "no date yet" is a
    real and common state here, and a date picker has to invent a day to show.
    The format is stated in the placeholder and enforced by `workflow.update`,
    which reports what it refused.

    No `st.form`: a dialog already gates everything behind its own explicit
    buttons, and a form here would only add a second, redundant submit
    boundary and more button-key bookkeeping for no behaviour gained.
    """
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

    if st.button("Speichern", key="acq_contact_save"):
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
            # Left open on the error rather than popped/rerun: the whole point
            # of `WF.update`'s all-or-nothing validation is that a bad date
            # cannot lose a good field along with it, and closing here anyway
            # would silently drop everything the user just typed.
            st.error(str(error))
            return
        st.toast("Kontaktdaten gespeichert.")
        st.session_state.pop(CONTACT_OPEN, None)
        st.rerun()

    if st.button("Von Merkliste entfernen", key="acq_contact_remove"):
        WF.set_saved([key], False, db)
        st.toast("Parzelle von der Merkliste entfernt.")
        st.session_state.pop(CONTACT_OPEN, None)
        st.rerun()

    if st.button("Abbrechen", key="acq_contact_cancel"):
        st.session_state.pop(CONTACT_OPEN, None)
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
