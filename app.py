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
from contextlib import closing, contextmanager
from datetime import date

import pandas as pd
import streamlit as st

import acquisition as ACQ
import bootstrap
import detail
import land_prices as LP
import merkliste
import navigation
import screening
import shell
import login_page
import scope_auth
import workflow as WF

import paths

HERE = paths.HERE
DB = paths.DB

st.set_page_config(page_title="Verdichtungspotenzial Aargau", layout="wide")


@contextmanager
def database_access():
    """A busy database stops startup before protected content is rendered."""
    try:
        yield
    except (sqlite3.OperationalError, pd.errors.DatabaseError) as error:
        # pandas wraps SQLite read failures. Only BUSY/LOCKED (including their
        # extended codes) are transient; schema/I/O errors must remain visible.
        cause = error.__cause__ if isinstance(error, pd.errors.DatabaseError) else error
        code = getattr(cause, "sqlite_errorcode", None)
        if not isinstance(cause, sqlite3.OperationalError) or code is None or (
            code & 0xff
        ) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise
        st.warning("Die Datenbank ist vorübergehend gesperrt. Bitte versuchen Sie es erneut.")
        if st.button("Erneut versuchen", key="database_retry", type="primary"):
            st.rerun()
        st.stop()


def gate():
    """Select personal Scope login or the existing optional shared-password gate.

    A Railway URL is public and guessable, and this list is the output of
    Philipp's own research — which parcels to approach before anyone else does.
    Leaving that open would give it away. Unset locally, so development is
    unaffected; set in the deployed environment.

    Personal mode must be explicitly configured and never falls back to shared
    access when its configuration or identity provider is unavailable.
    """
    try:
        personal = scope_auth.enabled()
    except scope_auth.AuthError as error:
        st.error(str(error))
        st.stop()
    if personal:
        bootstrap.prepare_database()
        scope_auth.gate(DB)
        return
    secret = os.environ.get("APP_PASSWORD")
    if not secret or st.session_state.get("_ok"):
        bootstrap.prepare_database()
        return
    with login_page.card("Gemeinsamer Zugang mit Passwort."):
        with st.form("shared_login", border=False):
            entered = st.text_input("Passwort", type="password")
            submitted = st.form_submit_button("Anmelden", type="primary", width="stretch")
        if submitted:
            # Constant-time so a wrong guess cannot be narrowed down by timing;
            # bytes, so a non-ASCII entry is simply wrong rather than an error.
            if hmac.compare_digest(entered.encode(), secret.encode()):
                st.session_state["_ok"] = True
                st.rerun()
            st.error("Falsches Passwort.")
    st.stop()


@st.cache_data(ttl=60)
def load():
    if not os.path.exists(DB):
        return None, None
    with closing(sqlite3.connect(DB)) as con:
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
with database_access():
    gate()
    parcels, runs = load()
    parcel_workflow = WF.load(DB)
land_price_references = load_land_prices()


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
#
# `shell.header` draws the bar around the control and returns what
# `navigation.render()` selected; the router below only cares about that
# string, not about how it got drawn.
page = shell.header(shell.data_as_of(runs))

if page == "Screening":
    screening.page(parcels, parcel_workflow, DB, price_of, land_price_references, runs,
                   reload=load)
elif page == "Merkliste":
    merkliste.page(parcels, parcel_workflow, DB, price_of)
elif page == "Analyse":
    if detail.selected():
        with st.container(key="detail_page"):
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
