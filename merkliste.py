"""The shortlist: what has been kept, and how far each one has got.

The board in `acquisition.py` groups the same leads by stage, which is the
right shape for working through them one at a time. This page is the other
question — how much is on the list at all — so it totals them and puts them in
one table.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import acquisition as ACQ
import detail
import navigation
import ui_components as UI
import workflow as WF

#: Stages that count as a live conversation. Neither an untouched lead nor a
#: refusal is one, and counting either would report progress that has not
#: happened.
IN_DIALOG = ("contacted", "in_discussion", "meeting_scheduled")


_PAGE_CSS = """
<style>
.st-key-merkliste_header { margin: 12px 0 4px; }
.st-key-merkliste_header [data-testid="stColumn"]:first-child
  > [data-testid="stVerticalBlock"] {
  gap: 0 !important;
}
.scope-merkliste-kicker {
  margin: 0 0 7px;
  color: #9a9aa6;
  font-size: 10px;
  font-weight: 600;
  line-height: 12px;
  letter-spacing: .1em;
  text-transform: uppercase;
}
.st-key-merkliste_header [data-testid="stHeadingWithActionElements"] h3 {
  margin: 0;
  padding: 0;
  font-size: 21px;
  font-weight: 600;
  line-height: 26px;
  letter-spacing: -.015em;
}
.st-key-merkliste_header [data-testid="stHeading"]
  [data-testid="stMarkdownContainer"] {
  margin: 0 !important;
}
.st-key-merkliste_header [data-testid="stCaptionContainer"] {
  height: auto;
  margin: 7px 0 0;
  color: #77777f;
  font-size: 12.5px;
  line-height: 15px;
}
.st-key-merkliste_header [data-testid="stCaptionContainer"] p {
  margin: 0;
}
.st-key-merkliste_header_actions[data-testid="stHorizontalBlock"] {
  justify-content: flex-end;
  gap: 8px;
}
.st-key-merkliste_header_actions button {
  width: auto;
  min-height: 30px;
  height: 30px;
  padding: 0 12px;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 500;
  line-height: 1;
}
@media (max-width: 760px) {
  .st-key-merkliste_header [data-testid="stHorizontalBlock"] { flex-wrap: wrap; }
  .st-key-merkliste_header [data-testid="stColumn"] { min-width: 100%; }
}
</style>
"""


_METRICS_CSS = """
<style>
.st-key-merkliste_metrics [data-testid="stHorizontalBlock"] {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0;
  overflow: hidden;
  border: 1px solid #eaeaee;
  border-radius: 9px;
  background: #fff;
}

.st-key-merkliste_metrics [data-testid="stColumn"] {
  width: auto !important;
  min-width: 0;
  padding: 13px 16px;
  border-right: 1px solid #f0f0f3;
}

.st-key-merkliste_metrics [data-testid="stColumn"]:last-child {
  border-right: 0;
}

.st-key-merkliste_metrics [data-testid="stMetricLabel"] {
  display: block;
  height: 12px;
  min-height: 0;
  color: #8a8a94;
  font-family: "Instrument Sans", "Source Sans", sans-serif;
  font-size: 10px;
  font-weight: 600;
  line-height: normal;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.st-key-merkliste_metrics [data-testid="stMetricLabel"] p {
  color: inherit;
  font-family: inherit;
  font-size: 10px;
  font-weight: inherit;
  line-height: inherit;
  letter-spacing: inherit;
}

.st-key-merkliste_metrics [data-testid="stMetricValue"] {
  margin-top: 6px;
  padding: 0;
  color: #17171b;
  font-family: "IBM Plex Mono", monospace;
  font-size: 18px;
  font-weight: 600;
  line-height: normal;
  font-variant-numeric: tabular-nums;
}

.st-key-merkliste_metrics [data-testid="stMetricValue"] p {
  color: inherit;
  font-family: inherit;
  font-size: inherit;
  font-weight: inherit;
  line-height: inherit;
}

@media (max-width: 760px) {
  .st-key-merkliste_metrics [data-testid="stHorizontalBlock"] {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .st-key-merkliste_metrics [data-testid="stColumn"] {
    padding: 11px 12px;
    border-bottom: 1px solid #f0f0f3;
  }

  .st-key-merkliste_metrics [data-testid="stColumn"]:nth-child(2) {
    border-right: 0;
  }

  .st-key-merkliste_metrics [data-testid="stColumn"]:nth-child(n+3) {
    border-bottom: 0;
  }
}
</style>
"""


def summary(leads: pd.DataFrame, land_value) -> dict:
    """The four tiles, computed from the rows the table will show.

    `land_value` is a callable rather than a column so that this page and the
    result table resolve a parcel's reference price the same way. It returns
    None where no reference matches, and those parcels are skipped rather than
    summed as NaN — one unpriced parcel would otherwise blank the whole tile.
    """
    if leads.empty:
        return {"parcels": 0, "potential": 0.0, "land_value": 0.0, "in_dialog": 0}
    values = [land_value(row) for _, row in leads.iterrows()]
    return {
        "parcels": len(leads),
        "potential": float(leads["delta"].fillna(0).sum()),
        "land_value": float(sum(v for v in values if v is not None)),
        "in_dialog": int(leads["contact_status"].isin(IN_DIALOG).sum()),
    }


def table_rows(ordered: pd.DataFrame, land_value) -> list[dict]:
    """JSON-safe rows for the design-native shortlist component."""
    rows = []
    for _, row in ordered.iterrows():
        value = land_value(row)
        rows.append(
            {
                "bfs": int(row["bfs"]),
                "parcel": str(row["parcel"]),
                "address": ACQ._or_dash(row["address"]),
                "municipality": str(row["municipality"]),
                "potential": ACQ._swiss(float(row["delta"])),
                "landValue": "—" if value is None else ACQ._swiss(float(value)),
                "statusCode": str(row["contact_status"]),
                "status": WF.CONTACT_STATUS_LABELS.get(
                    row["contact_status"],
                    WF.CONTACT_STATUS_LABELS[WF.DEFAULT_CONTACT_STATUS],
                ),
                "lastContact": ACQ._or_dash(row["last_contact"]),
                "owner": ACQ._or_dash(row["owner_name"]),
                "note": str(row["note"]).strip(),
            }
        )
    return rows


def handle_table_event(event: dict, leads: pd.DataFrame, state=None) -> bool:
    """Validate and apply one row action returned by the component.

    A component can post arbitrary JSON, so the parcel must still exist in the
    shortlist before an action may name it. The frontend only expresses intent;
    Python remains the authority for navigation and contact-dialog state.
    """
    if not isinstance(event, dict):
        return False
    try:
        bfs, parcel = int(event.get("bfs")), str(event.get("parcel"))
    except (TypeError, ValueError):
        return False
    hit = leads[
        (leads["bfs"] == bfs) & (leads["parcel"].astype(str) == parcel)
    ]
    if hit.empty:
        return False
    target = st.session_state if state is None else state
    if event.get("type") == "owner":
        target[ACQ.CONTACT_OPEN] = f"{bfs}:{parcel}"
        return True
    if event.get("type") == "analyse":
        target[detail.SELECTED] = f"{bfs}:{parcel}"
        navigation.go_to("Analyse", target)
        return True
    return False


def page(parcels, decisions, db, price_of):
    """Summary tiles, the shortlist, and the way on to the board."""
    st.html(_PAGE_CSS)
    leads = ACQ.leads(parcels, decisions, "saved")
    with st.container(key="merkliste_header"):
        header_copy, header_action_column = st.columns(
            [5, 3], vertical_alignment="bottom"
        )
        with header_copy:
            st.html(
                '<div class="scope-merkliste-kicker">Merkliste</div>'
            )
            st.subheader("Gemerkte Parzellen")
            st.caption(
                f"{ACQ._swiss(len(leads))} Parzellen · manuell gepflegt. "
                "Kontaktstand und Wiedervorlagen werden in der Akquisition geführt."
            )
        with header_action_column.container(
            key="merkliste_header_actions", horizontal=True
        ):
            if st.button("Weitere Parzellen suchen"):
                navigation.go_to("Screening")
                st.rerun()
            if st.button("Zur Akquisition"):
                navigation.go_to("Akquisition")
                st.rerun()

    if leads.empty:
        st.info("Noch keine Parzellen gemerkt.")
        return

    def land_value(row):
        # Unreachable today and deliberately kept: the committed
        # `land_prices.csv` carries a canton-wide fallback, so every one of the
        # 36,274 stored parcels resolves to a reference. Replace that fallback
        # with licensed municipality rows — which the README describes as the
        # intended path — and a parcel outside them resolves to None. Without
        # this the tile would not merely be wrong, it would raise.
        reference = price_of(row)
        if reference is None or pd.isna(row["area"]):
            return None
        return row["area"] * reference.price_chf_m2

    totals = summary(leads, land_value)
    with st.container(key="merkliste_metrics"):
        st.html(_METRICS_CSS)
        tiles = st.columns(4)
        tiles[0].metric("Parzellen", ACQ._swiss(totals["parcels"]))
        tiles[1].metric("Summe Potenzial m²", ACQ._swiss(totals["potential"]))
        tiles[2].metric("Summe Landwert CHF", ACQ._swiss(totals["land_value"]))
        tiles[3].metric("Im Dialog", ACQ._swiss(totals["in_dialog"]))

    ordered = leads.sort_values(["municipality", "parcel"], kind="stable")
    event = UI.merkliste_table(
        table_rows(ordered, land_value), key="merkliste_design_table"
    )
    event = UI.consume_event(event, "merkliste")
    if event is not None and handle_table_event(event, leads):
        st.rerun()

    ACQ._render_open_contact_dialog(leads, db)
