"""The acquisition board — saved leads grouped by contact stage.

Kept out of `app.py` because the decisions behind the board are worth testing
on their own: which leads are due, which column a lead belongs in, and in what
order the cards sit. Those rules remain plain dataframe functions; rendering
and validated component-event handling sit alongside them below.

The board replaces a saved list grouped by municipality. A lead's municipality
is still on its card; what the user works through day to day is the stage.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape

import pandas as pd
import streamlit as st

import detail
import formatting as F
import navigation
import ui_components as UI
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


_CONTACT_STAGE_TINTS = {
    "not_contacted": ("#f4f4f6", "#6a6a74"),
    "contacted": ("#eef2fb", "#33538f"),
    "in_discussion": ("#e8f0ef", "#143a37"),
    "meeting_scheduled": ("#eef6f0", "#2f6b45"),
    "declined": ("#f6f2ee", "#7a5a3a"),
}


_CONTACT_DIALOG_CSS = """
<style>
div[data-testid="stDialog"]:has(.scope-contact-modal) {
  position: fixed !important;
  inset: 0 !important;
  width: 100vw !important;
  max-width: none !important;
  height: 100vh !important;
  display: flex !important;
  flex-direction: row !important;
  align-items: flex-start !important;
  justify-content: center !important;
  box-sizing: border-box !important;
  margin: 0 !important;
  padding: 64px 24px !important;
  overflow-y: auto !important;
  background: rgba(23, 23, 27, .28) !important;
}

/* React Aria treats the inner modal wrapper as "inside" for dismissal.
   Keep its box on the dialog, so the surrounding overlay receives clicks. */
div[data-testid="stDialog"]:has(.scope-contact-modal) > div {
  display: contents !important;
}

/* Streamlit's own input chatter — "Press Enter to apply · 26/200" — is drawn
   over the field in this modal and is not part of the design. The length caps
   it announces are enforced in `workflow._text` either way. */
div[data-testid="stDialog"]:has(.scope-contact-modal)
  [data-testid="InputInstructions"] {
  display: none !important;
}

/* Notes and address grow with their text instead of scrolling inside a fixed
   box; the dialog is a flow layout, so the modal grows with them. */
div[data-testid="stDialog"]:has(.scope-contact-modal) textarea {
  field-sizing: content;
  max-height: none;
}

/* Grow with the text, then scroll the form body while Close/Fertig stay visible. */
div[data-testid="stDialog"]:has(.scope-contact-modal)
  [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .scope-contact-modal) {
  max-height:calc(100dvh - 130px);
  min-height:0;
  overflow:hidden;
}
div[data-testid="stDialog"]:has(.scope-contact-modal)
  [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .scope-contact-modal) > div {
  flex:0 0 auto !important;
}
div[data-testid="stDialog"]:has(.scope-contact-modal)
  [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .scope-contact-modal) > div:has(> .st-key-contact_modal_body) {
  flex:1 1 auto !important;
  min-height:0 !important;
  overflow-y:auto;
  overscroll-behavior:contain;
}
@supports not (field-sizing: content) {
  .st-key-contact_modal_body [data-testid="stTextArea"] textarea {
    resize:vertical !important;
    overflow:auto;
    min-height:90px !important;
  }
}

div[data-testid="stDialog"]:has(.scope-contact-modal) section[role="dialog"] {
  position: relative !important;
  width: 620px !important;
  min-width: 0 !important;
  max-width: calc(100vw - 48px) !important;
  max-height: none !important;
  padding: 0 !important;
  overflow: hidden !important;
  border: 1px solid #e4e4ea !important;
  border-radius: 11px !important;
  background: #fff !important;
  box-shadow: none !important;
}

div[data-testid="stDialog"]:has(.scope-contact-modal) section[role="dialog"]
  > h2 {
  position: absolute !important;
  width: 1px !important;
  height: 1px !important;
  padding: 0 !important;
  margin: -1px !important;
  overflow: hidden !important;
  clip: rect(0, 0, 0, 0) !important;
  white-space: nowrap !important;
}

div[data-testid="stDialog"]:has(.scope-contact-modal) section[role="dialog"]
  > button[aria-label="Close"] {
  top: 18px !important;
  right: 20px !important;
  z-index: 4 !important;
  width: 26px !important;
  min-width: 26px !important;
  height: 26px !important;
  min-height: 26px !important;
  padding: 0 !important;
  border: 1px solid #e2e2e8 !important;
  border-radius: 5px !important;
  background: #fff !important;
  color: #77777f !important;
}

div[data-testid="stDialog"]:has(.scope-contact-modal) section[role="dialog"]
  > div:last-child {
  padding: 0 !important;
}

div[data-testid="stDialog"]:has(.scope-contact-modal)
  section[role="dialog"] [data-testid="stVerticalBlock"]:not(.st-key-contact_modal_body) {
  gap: 0 !important;
}

.scope-contact-modal,
.scope-contact-modal * {
  box-sizing: border-box;
}

.scope-contact-header {
  position: relative;
  min-height: 91px;
  padding: 18px 150px 18px 20px;
  border-bottom: 1px solid #f0f0f3;
}

.scope-contact-kicker {
  color: #8a8a94;
  font-size: 10px;
  font-weight: 600;
  line-height: 12px;
  letter-spacing: .1em;
  text-transform: uppercase;
}

.scope-contact-kicker {
  margin-bottom: 6px;
}

.scope-contact-address {
  overflow-wrap:anywhere;
  color: #17171b;
  font-size: 16px;
  font-weight: 600;
  line-height: normal;
  letter-spacing: -.01em;
}

.scope-contact-place {
  margin-top: 4px;
  color: #9a9aa6;
  font-size: 11.5px;
  line-height: 14px;
}

.scope-contact-stage {
  position: absolute;
  top: 22px;
  right: 56px;
  display: inline-flex;
  align-items: center;
  padding: 4px 9px;
  border-radius: 20px;
  font-size: 10.5px;
  font-weight: 500;
  line-height: 1;
  white-space: nowrap;
}

div[data-testid="stDialog"]:has(.scope-contact-modal)
  .st-key-contact_modal_body {
  padding: 18px 20px !important;
  gap: 14px !important;
}

.st-key-contact_modal_body [data-testid="stHorizontalBlock"] {
  gap: 16px !important;
}

.st-key-contact_modal_body [data-testid="stColumn"] {
  min-width: 0 !important;
}

.st-key-contact_modal_body [data-testid="stTextInput"] label,
.st-key-contact_modal_body [data-testid="stTextArea"] label {
  min-height: 12px !important;
  height: 12px !important;
  margin: 0 0 6px !important;
  color: #8a8a94 !important;
  font-size: 10px !important;
  font-weight: 600 !important;
  line-height: 12px !important;
  letter-spacing: .07em !important;
  text-transform: uppercase !important;
}

.st-key-contact_modal_body [data-testid="stTextInput"] label span,
.st-key-contact_modal_body [data-testid="stTextInput"] label [data-testid="stMarkdownContainer"],
.st-key-contact_modal_body [data-testid="stTextInput"] label p,
.st-key-contact_modal_body [data-testid="stTextArea"] label span,
.st-key-contact_modal_body [data-testid="stTextArea"] label [data-testid="stMarkdownContainer"],
.st-key-contact_modal_body [data-testid="stTextArea"] label p {
  color: inherit !important;
  font-size: 10px !important;
  font-weight: 600 !important;
  line-height: 12px !important;
  letter-spacing: .07em !important;
}

/* The two text areas carry the same frame as the one-line fields; only their
   height is free, so a long note grows the box (and the modal) downwards.
   Streamlit's `height=` argument sizes the wrappers, which would otherwise cap
   the box no matter what the textarea itself does — hence `auto` on all three. */
.st-key-contact_modal_body [data-testid="stTextArea"],
.st-key-contact_modal_body [data-testid="stTextArea"]
  [data-testid="stTextAreaRootElement"] {
  height: auto !important;
}

.st-key-contact_modal_body [data-testid="stTextArea"] textarea {
  height: auto !important;
  field-sizing: content;
  min-height: 54px !important;
  padding: 8px 10px !important;
  border: 1px solid #e2e2e8 !important;
  border-radius: 6px !important;
  background: #fff !important;
  color: #17171b !important;
  font-size: 12.5px !important;
  line-height: 1.45 !important;
  resize: none !important;
}

.st-key-contact_modal_body [data-testid="stTextAreaRootElement"] {
  border:0 !important; box-shadow:none !important; background:transparent !important;
}
.st-key-contact_modal_body textarea:focus {
  outline:none; border-color:#1c4e4a !important;
  box-shadow:0 0 0 3px #e2eceb !important;
}
div[data-testid="stDialog"]:has(.scope-contact-modal) button[aria-label="Close"] svg {
  width:13px; height:13px;
}

.st-key-contact_modal_body [data-testid="stTextInput"]
  div[data-testid="stTextInputRootElement"] {
  min-height: 32px !important;
  height: 32px !important;
  border: 1px solid #e2e2e8 !important;
  border-radius: 6px !important;
  background: #fff !important;
  box-shadow: none !important;
}

.st-key-contact_modal_body [data-testid="stTextInput"]
  div[data-testid="stTextInputRootElement"]:focus-within {
  border-color: #1c4e4a !important;
  box-shadow: 0 0 0 3px #e2eceb !important;
}

.st-key-contact_modal_body [data-testid="stTextInput"] input {
  height: 30px !important;
  padding: 0 10px !important;
  color: #17171b !important;
  background: transparent !important;
  font-family: "Instrument Sans", "Source Sans", sans-serif !important;
  font-size: 12.5px !important;
}

.st-key-contact_modal_body [data-testid="stTextInput"] input::placeholder {
  color: #aaaab3 !important;
  opacity: 1 !important;
}

.st-key-contact_modal_body [class*="st-key-acq_contact_phone_"] input,
.st-key-contact_modal_body [class*="st-key-acq_contact_last_"] input,
.st-key-contact_modal_body [class*="st-key-acq_contact_due_"] input {
  font-family: "IBM Plex Mono", monospace !important;
  font-variant-numeric: tabular-nums;
}

.st-key-contact_modal_footer {
  min-height: 59px;
  padding: 14px 20px !important;
  border-top: 1px solid #f0f0f3;
}

.st-key-contact_modal_footer[data-testid="stHorizontalBlock"],
.st-key-contact_modal_footer [data-testid="stHorizontalBlock"] {
  align-items: center !important;
  justify-content: space-between !important;
  gap: 16px !important;
}

.scope-contact-footer-note {
  color: #b0b0b8;
  font-size: 11px;
  line-height: 13.5px;
  text-wrap: pretty;
}

.st-key-acq_contact_save button[kind="primary"] {
  min-height: 30px !important;
  height: 30px !important;
  padding: 0 14px !important;
  border: 1px solid #1c4e4a !important;
  border-radius: 6px !important;
  background: #1c4e4a !important;
  color: #fff !important;
  font-size: 12px !important;
  font-weight: 500 !important;
}

@media (max-width: 700px) {
  div[data-testid="stDialog"]:has(.scope-contact-modal) {
    padding: 20px 12px !important;
  }

  div[data-testid="stDialog"]:has(.scope-contact-modal) section[role="dialog"] {
    max-width: calc(100vw - 24px) !important;
  }
  div[data-testid="stDialog"]:has(.scope-contact-modal)
    [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .scope-contact-modal) {
    max-height:calc(100dvh - 42px);
  }

  .scope-contact-header {
    padding-right: 116px;
  }

  .st-key-contact_modal_body [data-testid="stHorizontalBlock"] {
    flex-wrap: wrap !important;
  }

  .st-key-contact_modal_body [data-testid="stColumn"] {
    min-width: 100% !important;
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


#: How long a follow-up sits in the future when the board moves a card. The
#: dialog no longer asks for a Wiedervorlage date — dragging a lead to its next
#: stage is the moment the follow-up is decided, so the board sets it. Two weeks
#: is the interval Philipp's own board used between a letter and a call.
FOLLOW_UP_DAYS = 14


def format_phone(value: str) -> str:
    """Swiss grouping — `078 777 88 00` — for what was typed as digits.

    Only the two shapes the cadastre and a Swiss address block actually carry:
    ten digits starting with 0, and the same number written +41. Anything else
    (a foreign number, an extension, a note beside the number) is left exactly
    as typed rather than being regrouped into something that looks official and
    is wrong.
    """
    raw = str(value).strip()
    if not raw:
        return ""
    plus = raw.startswith("+")
    digits = "".join(ch for ch in raw if ch.isdigit())
    if plus and digits.startswith("41") and len(digits) == 11:
        rest = digits[2:]
        return f"+41 {rest[:2]} {rest[2:5]} {rest[5:7]} {rest[7:]}"
    if not plus and digits == raw.replace(" ", "") and digits.startswith("0") and len(digits) == 10:
        return f"{digits[:3]} {digits[3:6]} {digits[6:8]} {digits[8:]}"
    return raw


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
            "Postadresse": shortlist["owner_address"].map(
                lambda text: " · ".join(
                    line.strip() for line in str(text).splitlines() if line.strip()
                )
            ),
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
    shortlist = leads(parcels, decisions, "saved")
    with st.container(key="acq_header"):
        header_copy, header_action = st.columns([5, 2], vertical_alignment="bottom")
        header_copy.html(_ACQUISITION_INTRO)
        if not shortlist.empty:
            header_action.download_button(
                "Kontaktliste exportieren",
                contact_list(shortlist).to_csv(index=False).encode("utf-8"),
                file_name="akquisition-kontakte.csv",
                mime="text/csv",
                key="acq_contacts_csv",
                type="primary",
                width="content",
            )

    _render_board(shortlist, db, price_of, today)

    _render_hidden(parcels, decisions, db)
    st.html(_ACQUISITION_FOOTER)


#: Five compact cells from the design: date; parcel identity; owner plus next
#: step; stage; actions. Related fields stay together instead of each taking a
#: separate Streamlit column and squeezing both action labels into two lines.
_DUE_CHIP_STYLE = (
    "display:inline-flex;align-items:center;font-size:11px;font-weight:500;"
    "padding:2px 7px;border-radius:20px;font-family:'IBM Plex Mono',monospace;"
    "font-variant-numeric:tabular-nums;"
)


_ACQUISITION_INTRO = """
<style>
.st-key-acq_header { margin: 10px 0 4px; }
.st-key-acq_header [data-testid="stHorizontalBlock"] {
  align-items: flex-end;
  gap: 24px;
}
.st-key-acq_header [data-testid="stColumn"]:last-child [data-testid="stDownloadButton"] {
  display: flex;
  justify-content: flex-end;
}
.st-key-acq_header [data-testid="stColumn"]:last-child > [data-testid="stVerticalBlock"] {
  align-items: flex-end;
}
.st-key-acq_header [data-testid="stDownloadButton"] button {
  width: auto;
  height: 30px;
  min-height: 30px;
  padding: 0 13px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}
.acquisition-page-intro { margin: 0; }
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
  line-height: 1.2;
  font-weight: 600;
  letter-spacing: -.015em;
}
.acquisition-page-intro p {
  max-width: 70ch;
  margin: 7px 0 0;
  color: #77777f;
  font-size: 12.5px;
  line-height: 1.2;
  text-wrap: pretty;
}
@media (max-width: 760px) {
  .acquisition-page-intro h1 { font-size: 19px; }
  .st-key-acq_header [data-testid="stHorizontalBlock"] { flex-wrap: wrap; }
  .st-key-acq_header [data-testid="stColumn"] { min-width: 100%; }
  .st-key-acq_header [data-testid="stColumn"]:last-child [data-testid="stDownloadButton"] {
    justify-content: flex-start;
  }
  .st-key-acq_header [data-testid="stColumn"]:last-child > [data-testid="stVerticalBlock"] {
    align-items: flex-start;
  }
}
</style>
<div class="acquisition-page-intro">
  <div class="acquisition-page-kicker">Akquisition</div>
  <h1>Eigentümer-Dialog</h1>
  <p>Kontaktstand je Parzelle und Eigentümerschaft. Karten per Drag &amp; Drop
     zwischen den Stufen verschieben; das Verschieben setzt Kontaktdatum und
     Wiedervorlage.</p>
</div>
"""

_ACQUISITION_FOOTER = """
<style>
.acquisition-page-footer {
  margin-top: 18px;
  color: #9a9aa6;
  font-size: 10.5px;
  line-height: 1.45;
}
</style>
<p class="acquisition-page-footer">Eigentümerangaben werden manuell im AGIS
nachgeschlagen; Bearbeitung nur für den internen Akquisitionsprozess. Kein
Bestandteil der amtlichen Parzellendaten.</p>
"""

def board_data(shortlist, price_of, today) -> list[dict]:
    """JSON-safe five-column board data for the local drag-and-drop component."""
    output = []
    grouped = by_stage(shortlist)
    # One home for "late", shared with nothing else since the Fällige-
    # Wiedervorlagen list was dropped: the card's own chip is the only place
    # the rule still shows, and re-deriving it inline would be a second copy.
    late = {
        (int(row.bfs), str(row.parcel))
        for row in overdue(shortlist, today).itertuples()
    }
    for stage, label in WF.CONTACT_STATUS_LABELS.items():
        cards = []
        for _, row in grouped[stage].iterrows():
            reference = price_of(row)
            land_value = (
                row["area"] * reference.price_chf_m2
                if reference is not None and pd.notna(row["area"])
                else None
            )
            contact_line = " · ".join(
                value
                for value in (
                    str(row["contact_person"]).strip(),
                    str(row["phone"]).strip(),
                )
                if value
            ) or "Kontakt nicht erfasst"
            due_iso = _or_dash(row["due_date"])
            cards.append(
                {
                    "bfs": int(row["bfs"]),
                    "parcel": str(row["parcel"]),
                    "address": _or_dash(row["address"]),
                    "municipality": str(row["municipality"]),
                    "potential": _swiss(float(row["delta"])),
                    "landValue": (
                        "—" if land_value is None else f"CHF {_swiss(land_value)}"
                    ),
                    "owner": str(row["owner_name"]).strip()
                    or "Eigentümer nicht erfasst",
                    "contactLine": contact_line,
                    "lastContact": _or_dash(
                        _contact_date_display(row["last_contact"])
                    ),
                    "due": _or_dash(_contact_date_display(row["due_date"])),
                    "overdue": (int(row["bfs"]), str(row["parcel"])) in late,
                    "next": str(row["next_step"]).strip(),
                    "statusCode": stage,
                    "status": label,
                }
            )
        output.append({"code": stage, "label": label, "cards": cards})
    return output


def handle_board_event(event, shortlist, db, state=None) -> bool:
    """Validate and apply a drag or Analyse action from the board component."""
    if not isinstance(event, dict):
        return False
    try:
        bfs, parcel = int(event.get("bfs")), str(event.get("parcel"))
    except (TypeError, ValueError):
        return False
    hit = shortlist[
        (shortlist["bfs"] == bfs) & (shortlist["parcel"].astype(str) == parcel)
    ]
    if hit.empty:
        return False
    key = (bfs, parcel)
    if event.get("type") == "move":
        stage = event.get("stage")
        if stage not in WF.CONTACT_STATUS_LABELS:
            return False
        # Moving a card *is* the contact being recorded, so the two dates the
        # dialog no longer asks for are written here. `declined` ends the
        # conversation: it gets the contact date but no follow-up, which is
        # also what keeps it out of "Fällige Wiedervorlagen".
        today = date.today()
        follow_up = (
            "" if stage == "declined"
            else (today + timedelta(days=FOLLOW_UP_DAYS)).isoformat()
        )
        WF.update(
            [key],
            contact_status=stage,
            last_contact=today.isoformat(),
            due_date=follow_up,
            db=db,
        )
        return True
    if event.get("type") == "analyse":
        target = st.session_state if state is None else state
        target[detail.SELECTED] = f"{bfs}:{parcel}"
        navigation.go_to("Analyse", target)
        return True
    if event.get("type") == "owner":
        # The board is now the only way into the owner dialog from this page —
        # the Fällige-Wiedervorlagen list that used to carry the button is gone.
        target = st.session_state if state is None else state
        target[CONTACT_OPEN] = f"{bfs}:{parcel}"
        return True
    return False


def _render_board(shortlist, db, price_of, today):
    event = UI.acquisition_board(
        board_data(shortlist, price_of, today), key="acq_design_board"
    )
    event = UI.consume_event(event, "acquisition_board")
    if event is not None and handle_board_event(event, shortlist, db):
        st.rerun()

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

    Matches on the same two-part key the board component emits, not on the
    shortlist's pandas index — that index is not part of the pid and is free
    to change when a stage move reorders `by_stage`'s frames.
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


def _contact_date_display(value) -> str:
    """Show stored ISO dates in the same Swiss format as the design."""
    text = "" if pd.isna(value) else str(value).strip()
    if not text:
        return ""
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return text


def _contact_date_storage(value) -> str:
    """Accept the displayed Swiss date format while storing canonical ISO."""
    text = str(value or "").strip()
    if not text or "." not in text:
        return text
    try:
        return datetime.strptime(text, "%d.%m.%Y").date().isoformat()
    except ValueError:
        # Let workflow.update produce its established validation message and
        # preserve its all-or-nothing write guarantee.
        return text


def _close_contact_dialog() -> None:
    st.session_state.pop(CONTACT_OPEN, None)
    st.session_state.pop("acq_contact_errors", None)


def _save_contact_field(key, db, field_name, widget_key, *, is_date=False):
    """Persist one owner-modal field as soon as its widget changes."""
    value = st.session_state.get(widget_key, "")
    if is_date:
        value = _contact_date_storage(value)
    elif field_name == "phone":
        # Written back into the widget as well: a number that is stored grouped
        # but still displayed as the digits someone typed reads as "not saved".
        value = format_phone(value)
        st.session_state[widget_key] = value
    errors = dict(st.session_state.get("acq_contact_errors", {}))
    try:
        WF.update([key], db=db, **{field_name: value})
    except ValueError as error:
        errors[field_name] = (
            "Bitte ein gültiges Datum im Format TT.MM.JJJJ eingeben."
            if is_date else str(error)
        )
    else:
        errors.pop(field_name, None)
    st.session_state["acq_contact_errors"] = errors


@st.dialog(
    "Eigentümerschaft",
    width="large",
    on_dismiss=_close_contact_dialog,
)
def _contact_dialog(row, key, db):
    """Render the one open lead in the design's compact two-column modal.

    Like the design export, each field is saved when it changes; ``Fertig``
    only closes the modal. Python still validates every changed value and an
    invalid date leaves both the field and the dialog visible with an error.
    """
    status_code = str(row.get("contact_status", WF.DEFAULT_CONTACT_STATUS))
    status_label = WF.CONTACT_STATUS_LABELS.get(
        status_code, WF.CONTACT_STATUS_LABELS[WF.DEFAULT_CONTACT_STATUS]
    )
    stage_background, stage_color = _CONTACT_STAGE_TINTS.get(
        status_code, _CONTACT_STAGE_TINTS[WF.DEFAULT_CONTACT_STATUS]
    )
    slug = f"{key[0]}_{key[1]}"
    st.html(
        _CONTACT_DIALOG_CSS
        + f"""
<div class="scope-contact-modal">
  <div class="scope-contact-header">
    <div class="scope-contact-kicker">Eigentümerschaft</div>
    <div class="scope-contact-address">{escape(str(row['address']))}</div>
    <div class="scope-contact-place">{escape(str(row['municipality']))} · {escape(str(row['parcel']))}</div>
    <span class="scope-contact-stage" style="background:{stage_background};color:{stage_color}">{escape(status_label)}</span>
  </div>
</div>
"""
    )

    with st.container(key="contact_modal_body"):
        first_row = st.columns(2)
        owner_key = f"acq_contact_owner_{slug}"
        first_row[0].text_input(
            "Eigentümerschaft",
            value=str(row["owner_name"]),
            max_chars=200,
            placeholder="z. B. Erbengemeinschaft Weber",
            key=owner_key,
            on_change=_save_contact_field,
            args=(key, db, "owner_name", owner_key),
        )
        person_key = f"acq_contact_person_{slug}"
        first_row[1].text_input(
            "Kontaktperson",
            value=str(row["contact_person"]),
            max_chars=200,
            placeholder="Name, Funktion",
            key=person_key,
            on_change=_save_contact_field,
            args=(key, db, "contact_person", person_key),
        )

        second_row = st.columns(2)
        phone_key = f"acq_contact_phone_{slug}"
        second_row[0].text_input(
            "Telefon",
            value=str(row["phone"]),
            max_chars=50,
            placeholder="+41 ...",
            key=phone_key,
            on_change=_save_contact_field,
            args=(key, db, "phone", phone_key),
        )
        email_key = f"acq_contact_email_{slug}"
        second_row[1].text_input(
            "E-Mail",
            value=str(row["email"]),
            max_chars=200,
            placeholder="name@domain.ch",
            key=email_key,
            on_change=_save_contact_field,
            args=(key, db, "email", email_key),
        )

        # The postal address the letter is actually sent to. One box rather
        # than three fields: it is copied off the AGIS extract as a block and
        # pasted into a letter as a block, and splitting it would ask the user
        # to take apart something they never assemble by hand.
        address_key = f"acq_contact_address_{slug}"
        st.text_area(
            "Adresse",
            value=str(row["owner_address"]),
            max_chars=300,
            height=88,
            placeholder="Empfängername\nLandstrasse 10B\n4313 Möhlin",
            key=address_key,
            on_change=_save_contact_field,
            args=(key, db, "owner_address", address_key),
        )

        third_row = st.columns(2)
        last_key = f"acq_contact_last_{slug}"
        third_row[0].text_input(
            "Letzter Kontakt",
            value=_contact_date_display(row["last_contact"]),
            max_chars=10,
            placeholder="TT.MM.JJJJ",
            key=last_key,
            on_change=_save_contact_field,
            args=(key, db, "last_contact", last_key),
            kwargs={"is_date": True},
        )
        # Was "Nächster Schritt", and no Wiedervorlage beside it any more: the
        # board sets the follow-up date when a card moves, so the only thing
        # left to type here is where the lead stands.
        next_key = f"acq_contact_next_{slug}"
        third_row[1].text_input(
            "Status",
            value=str(row["next_step"]),
            max_chars=300,
            placeholder="Was ist zu tun?",
            key=next_key,
            on_change=_save_contact_field,
            args=(key, db, "next_step", next_key),
        )

        note_key = f"acq_contact_note_{slug}"
        st.text_area(
            "Notizen",
            value=str(row["note"]),
            max_chars=1000,
            height=68,
            placeholder="Interne Notiz",
            key=note_key,
            on_change=_save_contact_field,
            args=(key, db, "note", note_key),
        )
        for error in st.session_state.get("acq_contact_errors", {}).values():
            st.error(error)

    with st.container(
        key="contact_modal_footer",
        horizontal=True,
        horizontal_alignment="distribute",
        vertical_alignment="center",
        gap="small",
    ):
        st.html(
            '<span class="scope-contact-footer-note">Änderungen werden sofort übernommen. '
            "Kontaktstufe per Drag &amp; Drop im Board ändern.</span>"
        )
        close_clicked = st.button("Fertig", key="acq_contact_save", type="primary")

    if close_clicked and not st.session_state.get("acq_contact_errors"):
        _close_contact_dialog()
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
