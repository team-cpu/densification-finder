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
    st.subheader("Merkliste")
    st.caption(
        "Gemerkte Parzellen. Kontaktstand und Wiedervorlagen werden in der "
        "Akquisition geführt; hier steht, was insgesamt auf der Liste liegt."
    )

    leads = ACQ.leads(parcels, decisions, "saved")
    if leads.empty:
        st.info("Noch keine Parzellen gemerkt.")
        return

    def land_value(row):
        reference = price_of(row)
        if reference is None or pd.isna(row["area"]):
            return None
        return row["area"] * reference.price_chf_m2

    totals = summary(leads, land_value)
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

    left, right = st.columns(2)
    if left.button("Zur Akquisition", width="stretch"):
        navigation.go_to("Akquisition")
        st.rerun()
    chosen = right.selectbox(
        "Parzelle analysieren",
        list(ordered.index),
        format_func=lambda i: (
            f"{ACQ._or_dash(ordered.loc[i, 'address'])} · "
            f"{ordered.loc[i, 'municipality']}"
        ),
        key="merkliste_analyse_pick",
    )
    if right.button("Analyse öffnen", width="stretch"):
        detail.open_parcel(detail.parcel_id(ordered.loc[chosen]))
        navigation.go_to("Analyse")
        st.rerun()
