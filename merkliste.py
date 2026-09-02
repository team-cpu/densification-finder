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
import workflow as WF

#: Stages that count as a live conversation. Neither an untouched lead nor a
#: refusal is one, and counting either would report progress that has not
#: happened.
IN_DIALOG = ("contacted", "in_discussion", "meeting_scheduled")


_PAGE_CSS = """
<style>
.st-key-merkliste_header { margin: 10px 0 20px; }
.st-key-merkliste_header [data-testid="stHeadingWithActionElements"] h3 {
  margin: 0;
  font-size: 21px;
  font-weight: 600;
  letter-spacing: -.015em;
}
.st-key-merkliste_header_actions [data-testid="stHorizontalBlock"] {
  justify-content: flex-end;
}
.st-key-merkliste_header_actions button { white-space: nowrap; }
.st-key-merkliste_table {
  margin-top: 14px;
  padding: 0;
  border: 1px solid #eaeaee;
  border-radius: 9px;
  background: #fff;
  overflow: hidden;
}
.st-key-merkliste_row_actions {
  margin-top: 10px;
  padding: 10px 12px;
  border: 1px solid #eaeaee;
  border-radius: 9px;
  background: #fff;
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


def page(parcels, decisions, db, price_of):
    """Summary tiles, the shortlist, and the way on to the board."""
    st.html(_PAGE_CSS)
    with st.container(key="merkliste_header"):
        header_copy, header_action_column = st.columns(
            [5, 3], vertical_alignment="bottom"
        )
        with header_copy:
            st.html(
                '<div style="margin:0 0 7px;color:#9a9aa6;font-size:10px;'
                'font-weight:600;letter-spacing:.1em;text-transform:uppercase">'
                'Merkliste</div>'
            )
            st.subheader("Gemerkte Parzellen")
            st.caption(
                "Gemerkte Parzellen. Kontaktstand und Wiedervorlagen werden in der "
                "Akquisition geführt; hier steht, was insgesamt auf der Liste liegt."
            )
        with header_action_column.container(
            key="merkliste_header_actions", horizontal=True
        ):
            if st.button("Weitere Parzellen suchen", width="stretch"):
                navigation.go_to("Screening")
                st.rerun()
            if st.button("Zur Akquisition", width="stretch"):
                navigation.go_to("Akquisition")
                st.rerun()

    leads = ACQ.leads(parcels, decisions, "saved")
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
    table = pd.DataFrame(
        {
            "_bfs": ordered["bfs"].astype(int),
            "_parcel": ordered["parcel"].astype(str),
            "Adresse": ordered["address"].map(ACQ._or_dash),
            "Gemeinde": ordered["municipality"],
            "Potenzial m²": ordered["delta"].round(0),
            "Landwert CHF": [land_value(row) for _, row in ordered.iterrows()],
            "Kontaktstand": ordered["contact_status"].map(
                lambda code: WF.CONTACT_STATUS_LABELS.get(
                    code, WF.CONTACT_STATUS_LABELS[WF.DEFAULT_CONTACT_STATUS]
                )
            ),
            "Letzter Kontakt": ordered["last_contact"].map(ACQ._or_dash),
            "Eigentümerschaft / Notiz": [
                " · ".join(x for x in (str(row["owner_name"]).strip(),
                                       str(row["note"]).strip()) if x) or "—"
                for _, row in ordered.iterrows()
            ],
            "Entfernen": False,
        }
    )

    with st.container(key="merkliste_table"):
        with st.form("merkliste_form"):
            edited = st.data_editor(
                table,
                key="merkliste_editor",
                width="stretch",
                hide_index=True,
                column_order=(
                    "Adresse", "Gemeinde", "Potenzial m²", "Landwert CHF",
                    "Kontaktstand", "Letzter Kontakt", "Eigentümerschaft / Notiz",
                    "Entfernen",
                ),
                disabled=(
                    "_bfs", "_parcel", "Adresse", "Gemeinde", "Potenzial m²",
                    "Landwert CHF", "Letzter Kontakt", "Eigentümerschaft / Notiz",
                ),
                column_config={
                    "Potenzial m²": st.column_config.NumberColumn(format="%.0f"),
                    "Landwert CHF": st.column_config.NumberColumn(format="%.0f"),
                    "Kontaktstand": st.column_config.SelectboxColumn(
                        options=list(WF.CONTACT_STATUS_LABELS.values()),
                        required=True,
                        width="medium",
                    ),
                    "Entfernen": st.column_config.CheckboxColumn(width="small"),
                },
            )
            store = st.form_submit_button("Änderungen speichern")

    if store:
        codes = {label: code for code, label in WF.CONTACT_STATUS_LABELS.items()}
        for _, row in edited.iterrows():
            key = int(row["_bfs"]), str(row["_parcel"])
            if bool(row["Entfernen"]):
                WF.set_saved([key], False, db)
            else:
                WF.update([key], contact_status=codes[row["Kontaktstand"]], db=db)
        st.toast("Merkliste gespeichert.")
        st.rerun()

    with st.container(key="merkliste_row_actions"):
        picker, owner_action, analyse_action = st.columns(
            [4, 1, 1], vertical_alignment="bottom"
        )
        chosen = picker.selectbox(
            "Parzellenaktion",
            list(ordered.index),
            format_func=lambda i: (
                f"{ACQ._or_dash(ordered.loc[i, 'address'])} · "
                f"{ordered.loc[i, 'municipality']} · {ordered.loc[i, 'parcel']}"
            ),
            key="merkliste_analyse_pick",
        )
        selected_row = ordered.loc[chosen]
        selected_key = int(selected_row["bfs"]), str(selected_row["parcel"])
        with owner_action:
            ACQ._eigentuemer_button(selected_key, "merkliste_owner_open")
        if analyse_action.button("Analyse", width="stretch"):
            detail.open_parcel(detail.parcel_id(selected_row))
            navigation.go_to("Analyse")
            st.rerun()

    ACQ._render_open_contact_dialog(leads, db)
