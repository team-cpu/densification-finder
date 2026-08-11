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
import concurrent.futures
import os
import sqlite3

import pandas as pd
import streamlit as st

import oereb as O

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "results.sqlite")

# Rule of thumb, and labelled as one in the table header. 90 m² per dwelling is
# the figure the brief gives; it is not a planning standard.
SQM_PER_UNIT = 90

# The brief's two-step approach: rank broadly, then pay for ÖREB only on the
# head of the list.
SHORTLIST = 50

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


def read_oereb_cache():
    """Not cached by Streamlit: it changes as a run progresses, and a stale read
    would show the button's own results as still missing."""
    con = sqlite3.connect(DB)
    try:
        return pd.read_sql_query("SELECT * FROM oereb_cache", con).set_index("egrid")
    except Exception:
        return pd.DataFrame(columns=["hard", "notable", "error"]).set_index(
            pd.Index([], name="egrid")
        )


def check_oereb(egrids, progress=None):
    """One call per parcel, eight at a time. Results are written as they arrive,
    so an interrupted run keeps what it already paid for."""
    con = sqlite3.connect(DB)
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(O.assess, e): e for e in egrids}
        for fut in concurrent.futures.as_completed(futures):
            egrid = futures[fut]
            try:
                hard, notable, err = fut.result()
            except Exception as exc:
                hard, notable, err = [], [], str(exc)
            con.execute(
                "INSERT OR REPLACE INTO oereb_cache VALUES (?,?,?,?,datetime('now'))",
                (egrid, "; ".join(hard), "; ".join(notable), err or ""),
            )
            con.commit()
            done += 1
            if progress:
                progress(done / max(len(egrids), 1), f"ÖREB {done}/{len(egrids)}")
    con.close()


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

# ── filter and rank (cascade steps 1–4) ─────────────────────────────────────
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

# Ranked by the delta/existing ratio rather than the absolute delta: a 400 m²
# gain on a small old house is a better lead than the same gain on a large one.
df = df.assign(ratio=df["delta"] / df["existing"].clip(lower=1)).sort_values(
    "ratio", ascending=False
)
shortlist = df.head(SHORTLIST)

# ── cascade step 5: ÖREB, shortlist only ────────────────────────────────────
cache = read_oereb_cache()
known = shortlist["egrid"].isin(cache.index)
pending = shortlist.loc[~known & shortlist["egrid"].notna() & (shortlist["egrid"] != ""), "egrid"]

run_col, note_col = st.columns([1, 4])
run = run_col.button(
    f"▶ Prüfen ({len(pending)} offen)",
    type="primary",
    disabled=pending.empty,
    help="Fragt den ÖREB-Kataster für die Shortlist ab und schliesst Parzellen "
         "mit einer harten Eigentumsbeschränkung aus. Rund eine Sekunde pro "
         "Parzelle. Die Geometrie-Auswertung selbst ist vorberechnet (ingest.py).",
)
if pending.empty:
    note_col.success(f"Shortlist vollständig ÖREB-geprüft ({len(shortlist)} Parzellen).")
else:
    note_col.info(
        f"{len(shortlist) - len(pending)} von {len(shortlist)} der Shortlist geprüft. "
        "Ungeprüfte Parzellen werden angezeigt, aber noch nicht ausgeschlossen."
    )

if run:
    bar = st.progress(0.0, "ÖREB")
    check_oereb(list(pending), progress=lambda f, t: bar.progress(f, t))
    bar.empty()
    # The button's own label and the counters above were rendered from the state
    # this run just invalidated; without a rerun the page would still offer to
    # check the parcels it has only now finished checking.
    st.rerun()

# Excluded: a hard restriction — in Aargau that means a Planungszone, a planning
# freeze under which no permit is issued.
#
# "Not checked yet" is carried in its own boolean rather than as a None inside
# the text columns. Pandas infers those as its string dtype and silently turns
# None into NaN, which is truthy — `value or ""` then yields NaN instead of "",
# and an `is None` test never matches. Both failures are quiet.
def oereb_of(egrid, field):
    return (cache.loc[egrid, field] or "") if egrid in cache.index else ""


shortlist = shortlist.assign(
    _checked=[e in cache.index for e in shortlist["egrid"]],
    _hard=[oereb_of(e, "hard") for e in shortlist["egrid"]],
    _notable=[oereb_of(e, "notable") for e in shortlist["egrid"]],
)
excluded = shortlist[shortlist["_hard"] != ""]
final = shortlist[shortlist["_hard"] == ""].head(int(top_n))

# ── coverage, stated rather than implied ────────────────────────────────────
assessed = int(runs["assessed"].sum())
no_az = int(runs["no_az"].sum())
st.caption(
    f"{len(runs)} von 196 Gemeinden ausgewertet · {assessed:,} Parzellen beurteilt · "
    f"{no_az:,} nicht beurteilbar (keine Bauzone mit Ausnützungsziffer). "
    "33 Gemeinden publizieren gar keine AZ und fehlen deshalb vollständig. "
    f"{len(df):,} Treffer nach Filter → Shortlist {len(shortlist)} → "
    f"{len(excluded)} durch ÖREB ausgeschlossen."
)

if final.empty:
    st.info("Keine Parzelle erfüllt diese Kriterien.")
    st.stop()

# ── table ───────────────────────────────────────────────────────────────────
def status(r):
    bits = [
        r["heritage"] or "",
        "Gestaltungsplan — AZ evtl. überlagert" if r["design_plan"] else "",
        r["_notable"] or "",
        "" if r["_checked"] else "ÖREB offen",
    ]
    return " · ".join(x for x in bits if x) or "frei"


view = pd.DataFrame(
    {
        "Adresse": final["address"].fillna("—").replace("", "—"),
        "Gemeinde": final["municipality"],
        "Parzelle": final["parcel"],
        "Baujahr": final["built"].fillna("—").replace("", "—"),
        "Nutzung": final["use_class"].fillna("—").replace("", "—"),
        "Zone": final["zone"].fillna("—").replace("", "—"),
        "AZ": final["az"],
        "Fläche m²": final["area"].round(0),
        "in Bauzone": (final["zone_share"] * 100).round(0).astype(int).astype(str) + "%",
        "Geb.": final["buildings"],
        "bestehend m²": final["existing"].round(0),
        "Potenzial m² (Schätzung)": final["delta"].round(0),
        f"≈ Wohnungen (à {SQM_PER_UNIT} m², Annahme)": (final["delta"] / SQM_PER_UNIT).round(1),
        "Status": final.apply(status, axis=1),
        # Blank rather than a broken link where the parcel carries no EGRID.
        "ÖREB-Auszug": final["egrid"].map(lambda e: OEREB_PDF + e if e else None),
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

if not excluded.empty:
    with st.expander(f"{len(excluded)} Parzellen durch ÖREB ausgeschlossen"):
        st.dataframe(
            pd.DataFrame({
                "Gemeinde": excluded["municipality"],
                "Parzelle": excluded["parcel"],
                "Adresse": excluded["address"].fillna("—"),
                "Beschränkung": excluded["_hard"],
            }),
            width="stretch",
            hide_index=True,
        )

st.caption(
    "Potenzial = Fläche innerhalb der Bauzone × AZ − (Grundfläche × Geschosse) "
    "aller Gebäude auf der Parzelle. Die bestehende Geschossfläche ist aus dem "
    "GWR geschätzt, nicht die anrechenbare Geschossfläche — die Zahl ist eine "
    "Schätzung und eher zu tief als zu hoch. Baujahr ist das des ältesten "
    "Gebäudes auf der Parzelle, AZ der flächengewichtete Wert über alle "
    "berührten Zonen. Eigentümerdaten werden nicht erhoben. "
    "Der ÖREB-Auszug ist der rechtsverbindliche Katasterauszug des Kantons."
)
