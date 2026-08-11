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
import re
import sqlite3
from datetime import date

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

c5, c6, c7 = st.columns([2, 2, 1])
# The brief lists the year-built cutoff among the filter inputs. Unlike the
# others it is a pipeline parameter, not a display filter: the age rule runs
# inside the cascade, so a change takes effect on the next "Neu berechnen".
min_age = c7.number_input(
    "Mindestalter (Jahre)", 0, 100, 15, 1,
    help="Parzellen, deren Gebäude alle sicher jünger sind, werden aussortiert. "
         "Ohne exaktes Baujahr entscheidet die GWR-Bauperiode — eine Periode, "
         "die die Grenze überspannt, bleibt Kandidat. Wirkt beim nächsten "
         "«Neu berechnen», nicht auf die bereits angezeigte Liste.",
)
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
def select(src):
    """A function rather than inline code because the Run button has to apply
    exactly these filters a second time, to the freshly recomputed table, before
    it knows which parcels the ÖREB step should pay for."""
    out = src[
        (src["delta"] >= min_delta) & (src["area"].between(area[0], area[1]))
    ]
    if chosen:
        out = out[out["municipality"].isin(chosen)]
    if hide_inventory:
        out = out[out["heritage"].fillna("") == ""]
    if hide_design_plan:
        out = out[out["design_plan"] == 0]
    # Ranked by the delta/existing ratio rather than the absolute delta: a 400 m²
    # gain on a small old house is a better lead than the same gain on a large one.
    return out.assign(ratio=out["delta"] / out["existing"].clip(lower=1)).sort_values(
        "ratio", ascending=False
    )


df = select(parcels)
shortlist = df.head(SHORTLIST)

# ── cascade step 5: ÖREB, shortlist only ────────────────────────────────────
cache = read_oereb_cache()
known = shortlist["egrid"].isin(cache.index)
pending = shortlist.loc[~known & shortlist["egrid"].notna() & (shortlist["egrid"] != ""), "egrid"]

run_col, note_col = st.columns([1, 4])
run = run_col.button(
    "▶ Neu berechnen",
    type="primary",
    help="Rechnet die Filterkaskade für alle 163 Gemeinden neu und fragt "
         "anschliessend den ÖREB-Kataster für die Shortlist ab. Rund zwei "
         "Minuten. Die Parzellengeometrien werden dabei nicht neu vom WFS "
         "geladen — dafür data/parcels_*.xml löschen.",
)
if pending.empty:
    note_col.success(f"Shortlist vollständig ÖREB-geprüft ({len(shortlist)} Parzellen).")
else:
    note_col.info(
        f"{len(shortlist) - len(pending)} von {len(shortlist)} der Shortlist geprüft. "
        "Ungeprüfte Parzellen werden angezeigt, aber noch nicht ausgeschlossen."
    )

if run:
    import ingest

    bar = st.progress(0.0, "Kaskade")
    ingest.recompute(
        progress=lambda f, t: bar.progress(f * 0.2, "Kaskade — " + t),
        built_after=date.today().year - int(min_age),
    )

    # The shortlist the ÖREB step should pay for only exists once the table has
    # been rewritten, so the filters are applied a second time here rather than
    # reusing the selection made from the pre-run data.
    load.clear()
    fresh, _ = load()
    fresh_short = select(fresh).head(SHORTLIST)
    todo = fresh_short.loc[
        ~fresh_short["egrid"].isin(read_oereb_cache().index)
        & fresh_short["egrid"].notna()
        & (fresh_short["egrid"] != ""),
        "egrid",
    ]
    check_oereb(
        list(todo),
        progress=lambda f, t: bar.progress(0.2 + f * 0.8, t),
    )
    bar.empty()
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
    """Reports the exception, never the norm.

    Two facts matter only when they are unusual: 92% of parcels lie entirely
    inside the building zone and 98.5% carry a single building. As columns they
    were twenty rows of "100%" and "1"; as status text they appear on the ~5%
    and ~1.5% where they change how the number should be read.
    """
    bits = [
        r["heritage"] or "",
        "Gestaltungsplan — AZ evtl. überlagert" if r["design_plan"] else "",
        r["_notable"] or "",
        f"nur {r['zone_share'] * 100:.0f}% in der Bauzone" if r["zone_share"] < 0.95 else "",
        f"{r['buildings']} Gebäude" if r["buildings"] > 1 else "",
        "" if r["_checked"] else "ÖREB offen",
    ]
    return " · ".join(x for x in bits if x) or "frei"


# GWR's own wording is exact but repetitive — ten rows of "Gebäude mit einer
# Wohnung" cost more width than they carry meaning.
USE_SHORT = {"1110": "1 Whg.", "1121": "2 Whg.", "1122": "3+ Whg."}


def short_year(text):
    """"von 1946 bis 1960" is seventeen characters to say what "1946–60" says in
    seven, and the column is competing for width with the address."""
    t = (text or "").strip()
    m = re.match(r"von (\d{4}) bis (\d{2})(\d{2})$", t)
    if m:
        return f"{m.group(1)}–{m.group(3)}"
    m = re.match(r"nach (\d{4})$", t)
    if m:
        return f"ab {m.group(1)}"
    return t or "—"


def short_use(text):
    t = (text or "").strip()
    if "einer Wohnung" in t:
        return USE_SHORT["1110"]
    if "zwei" in t and "Wohnung" in t:
        return USE_SHORT["1121"]
    if "drei oder mehr" in t:
        return USE_SHORT["1122"]
    return t or "—"


# Column order follows the brief: who and where, then how old and what, then
# what the zone allows, then the answer. The diagnostics that explain how the
# answer was reached sit after it — putting them first pushed the potential, the
# unit estimate and the link off the right edge, which is the whole payload.
view = pd.DataFrame(
    {
        "Adresse": final["address"].fillna("—").replace("", "—"),
        "Gemeinde": final["municipality"],
        "Parzelle": final["parcel"],
        "Baujahr": final["built"].map(short_year),
        "Nutzung": final["use_class"].map(short_use),
        "Zone": final["zone"].fillna("—").replace("", "—"),
        "AZ": final["az"],
        "Potenzial m² (Schätzung)": final["delta"].round(0),
        f"≈ Whg. (à {SQM_PER_UNIT} m²)": (final["delta"] / SQM_PER_UNIT).round(1),
        # Blank rather than a broken link where the parcel carries no EGRID.
        "ÖREB": final["egrid"].map(lambda e: OEREB_PDF + e if e else None),
        "Status": final.apply(status, axis=1),
        "Fläche m²": final["area"].round(0),
        "bestehend m²": final["existing"].round(0),
    }
)

st.dataframe(
    view,
    width="stretch",
    # Tall enough that the full list is one glance rather than a scroll; the
    # brief asks for a top 20 and 20 rows is the default.
    height=min(len(view), 20) * 35 + 40,
    hide_index=True,
    column_config={
        "ÖREB": st.column_config.LinkColumn("ÖREB", display_text="PDF", width="small"),
        "Potenzial m² (Schätzung)": st.column_config.NumberColumn(format="%.0f"),
        # The address is the row's identity, so it gets the width it needs.
        # Gemeinde stays a column of its own despite looking redundant with it:
        # the postal town differs from the political municipality on 28% of
        # these parcels, and sometimes names a different place entirely
        # (Densbüren → Asp, Küttigen → Rombach), which is also the dimension
        # the filter above works in.
        "Adresse": st.column_config.TextColumn(width="large"),
        # Truncation here is acceptable: the deciding word comes first.
        "Status": st.column_config.TextColumn(width="medium"),
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
