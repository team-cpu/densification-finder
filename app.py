"""
Densification Potential Finder — the interface.

Reads `results.sqlite`, which `ingest.py` fills. Filtering happens here, over
already-computed potential, so a click is instant rather than a full canton
pass. The thresholds the brief calls configurable are the controls below.

    .venv/bin/streamlit run app.py
"""
import os
import sqlite3

import pandas as pd
import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "results.sqlite")
SQM_PER_UNIT = 90  # rule of thumb for converting GFA into dwellings

# The per-parcel link. Not a map: the binding cadastre extract, which lists every
# public-law restriction on the parcel and is the natural next step once a
# candidate looks interesting. The format goes in the path, the identifier is a
# query parameter — the same shape `oereb.py` records, and the URL the cadastre
# prints into its own QR code.
OEREB_PDF = "https://api.geo.ag.ch/v2/oereb/extract/pdf/?EGRID="

st.set_page_config(page_title="Verdichtungspotenzial Aargau", layout="wide")


@st.cache_data(ttl=60)
def load():
    if not os.path.exists(DB):
        return None, None
    con = sqlite3.connect(DB)
    parcels = pd.read_sql_query("SELECT * FROM parcel_results", con)
    runs = pd.read_sql_query("SELECT * FROM runs", con)
    return parcels, runs


parcels, runs = load()

st.title("Verdichtungspotenzial — Kanton Aargau")

if parcels is None or parcels.empty:
    st.warning("No results yet. Run `ingest.py` first.")
    st.stop()

# ── controls ────────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
min_delta = c1.number_input("Mindestpotenzial (m² GF)", 0, 5000, 130, 10)
area = c2.slider("Parzellenfläche (m²)", 0, 5000, (300, 5000), 50)
municipalities = sorted(parcels["municipality"].dropna().unique())
chosen = c3.multiselect("Gemeinde (leer = alle)", municipalities)
top_n = c4.number_input("Anzahl Resultate", 5, 200, 20, 5)

c5, c6 = st.columns(2)
hide_inventory = c5.checkbox(
    "Inventarisierte Gebäude ausblenden", value=False,
    help="Bauinventar und Kurzinventar verbieten einen Ersatzneubau nicht, "
         "erschweren ihn aber. Geschützte Gebäude sind ohnehin ausgeschlossen.",
)
hide_design_plan = c6.checkbox(
    "Parzellen mit Gestaltungsplan ausblenden", value=False,
    help="Wo ein rechtsgültiger Gestaltungsplan gilt, kann er eigene "
         "Nutzungsziffern festlegen — die Ausnützungsziffer der Grundzone ist "
         "dort nicht zwingend massgebend.",
)

# ── filter ──────────────────────────────────────────────────────────────────
df = parcels[
    (parcels["delta"] >= min_delta)
    & (parcels["area"].between(area[0], area[1]))
]
if chosen:
    df = df[df["municipality"].isin(chosen)]
if hide_inventory:
    df = df[df["heritage"].fillna("") == ""]
if hide_design_plan:
    df = df[df["design_plan"] == 0]

df = df.assign(ratio=df["delta"] / df["existing"].clip(lower=1)).sort_values(
    "ratio", ascending=False
).head(int(top_n))

# ── coverage, stated rather than implied ────────────────────────────────────
assessed = int(runs["assessed"].sum())
no_az = int(runs["no_az"].sum())
st.caption(
    f"{len(runs)} von 196 Gemeinden ausgewertet · {assessed:,} Parzellen beurteilt · "
    f"{no_az:,} nicht beurteilbar (keine Bauzone mit Ausnützungsziffer). "
    "33 Gemeinden publizieren gar keine AZ und fehlen deshalb vollständig."
)

if df.empty:
    st.info("Keine Parzelle erfüllt diese Kriterien.")
    st.stop()

# ── table ───────────────────────────────────────────────────────────────────
view = pd.DataFrame(
    {
        "Gemeinde": df["municipality"],
        "Parzelle": df["parcel"],
        "Fläche m²": df["area"].round(0),
        "in Bauzone": (df["zone_share"] * 100).round(0).astype(int).astype(str) + "%",
        "Geb.": df["buildings"],
        "bestehend m²": df["existing"].round(0),
        "Potenzial m² (geschätzt)": df["delta"].round(0),
        "≈ Wohnungen": (df["delta"] / SQM_PER_UNIT).round(1),
        "Status": df.apply(
            lambda r: " · ".join(
                x for x in (
                    r["heritage"] or "",
                    "Gestaltungsplan — AZ evtl. überlagert" if r["design_plan"] else "",
                ) if x
            ) or "frei",
            axis=1,
        ),
        # Blank rather than a broken link where the parcel carries no EGRID.
        "ÖREB-Auszug": df["egrid"].map(lambda e: OEREB_PDF + e if e else None),
    }
)

st.dataframe(
    view,
    width="stretch",
    hide_index=True,
    column_config={
        "ÖREB-Auszug": st.column_config.LinkColumn("ÖREB-Auszug", display_text="PDF")
    },
)

st.caption(
    "Potenzial = Fläche innerhalb der Bauzone × AZ − (Grundfläche × Geschosse) "
    "aller Gebäude auf der Parzelle. Die bestehende Geschossfläche ist aus dem "
    "GWR geschätzt, nicht die anrechenbare Geschossfläche — die Zahl ist eine "
    "Schätzung und eher zu tief als zu hoch. Eigentümerdaten werden nicht erhoben. "
    "Der ÖREB-Auszug ist der rechtsverbindliche Katasterauszug des Kantons und "
    "zeigt sämtliche Eigentumsbeschränkungen der Parzelle."
)
