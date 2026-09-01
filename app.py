"""
Densification Potential Finder — the interface.

Reads `results.sqlite`, which `ingest.py` fills, and applies the rest of the
cascade over it: filter, rank, take a shortlist, check the shortlist against the
ÖREB cadastre, and show what survives.

The split is deliberate. Steps 1–4 of the cascade need every parcel's geometry
intersected with every zone — 15 minutes for the canton — so they are computed
once by `ingest.py`. Step 5 is one network call per parcel and only ever applies
to the shortlist, so it runs here, on demand, behind the Run button. That keeps a
run inside the minute or two the brief expects instead of a quarter of an hour,
and a filter change stays instant.

    .venv/bin/streamlit run app.py
"""
import hmac
import os
import sqlite3
from datetime import date

import pandas as pd
import streamlit as st

import acquisition as ACQ
import detail
import land_prices as LP
import merkliste
import navigation
import screening
import workflow as WF

import ingest as _ingest
import paths

HERE = paths.HERE
DB = paths.DB

st.set_page_config(page_title="Verdichtungspotenzial Aargau", layout="wide")


def gate():
    """A shared password, active only when APP_PASSWORD is set.

    A Railway URL is public and guessable, and this list is the output of
    Philipp's own research — which parcels to approach before anyone else does.
    Leaving that open would give it away. Unset locally, so development is
    unaffected; set in the deployed environment.

    Deliberately not a login: the brief describes a single-user internal tool,
    and accounts would be more machinery than it is worth.
    """
    secret = os.environ.get("APP_PASSWORD")
    if not secret or st.session_state.get("_ok"):
        return
    st.title("Verdichtungspotenzial — Kanton Aargau")
    entered = st.text_input("Passwort", type="password")
    if entered:
        # Constant-time so a wrong guess cannot be narrowed down by timing.
        if hmac.compare_digest(entered, secret):
            st.session_state["_ok"] = True
            st.rerun()
        st.error("Falsches Passwort.")
    st.stop()


gate()


@st.cache_data(ttl=60)
def load():
    if not os.path.exists(DB):
        return None, None
    con = sqlite3.connect(DB)
    parcels = pd.read_sql_query("SELECT * FROM parcel_results", con)
    runs = pd.read_sql_query("SELECT * FROM runs", con)
    return parcels, runs


@st.cache_data(ttl=60)
def load_land_prices():
    return LP.load()


# A Railway volume survives image deployments. Seed it when empty, then widen
# its schema before any DataFrame reads it. Without the second step, adding a
# result column in a new release leaves a populated volume on the old schema and
# the UI crashes with a KeyError when it renders that column.
paths.ensure_db()
with sqlite3.connect(DB) as con:
    _ingest.schema(con)
parcels, runs = load()
land_price_references = load_land_prices()
parcel_workflow = WF.load(DB)


def price_of(row):
    """The land-price reference for one parcel. A function because the table and
    the detail view have to resolve it the same way; two lookups could disagree
    about which row of `land_prices.csv` is the most specific match."""
    return LP.resolve(
        land_price_references, row["bfs"], row["municipality"], row["zone"]
    )


if parcels is None or parcels.empty:
    st.title("Verdichtungspotenzial — Kanton Aargau")
    st.warning("No results yet. Run `ingest.py` first.")
    st.stop()

# The brief asked for a conditional view rather than a second page, and for a
# long time one session-state key was the whole navigation. The workflow has
# since grown two more places to stand — a shortlist and an acquisition board —
# and stacking them under the result table made the page a scroll rather than a
# structure. So: four pages behind one control, and the parcel key now decides
# what Analyse shows rather than whether the list is drawn at all.
#
# `st.segmented_control` rather than `st.tabs` because tabs are not lazy: every
# tab body runs on every rerun, and Analyse recomputes residual values, reads
# the ÖREB cache and can build a PDF. One `if` renders one page.
st.title("Verdichtungspotenzial — Kanton Aargau")
page = navigation.render()

if page == "Screening":
    screening.page(parcels, parcel_workflow, DB, price_of, land_price_references, runs)
elif page == "Merkliste":
    merkliste.page(parcels, parcel_workflow, DB, price_of)
elif page == "Analyse":
    if detail.selected():
        detail.page(
            parcels,
            screening.read_oereb_cache(),
            price_of,
        )
    else:
        st.info(
            "Keine Parzelle ausgewählt. Eine Parzelle im Screening oder auf der "
            "Merkliste öffnen."
        )
elif page == "Akquisition":
    ACQ.render(parcels, parcel_workflow, DB, date.today().isoformat(), price_of)
