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
import hmac
import json
import os
import sqlite3
from datetime import date

import pandas as pd
import streamlit as st

import detail
import economics as EC
import formatting as F
import land_prices as LP
import links as L
import oereb as O

import ingest as _ingest
import paths
from ranking import rank_candidates

HERE = paths.HERE
DB = paths.DB

# Rule of thumb, and labelled as one in the table header. 90 m² per dwelling is
# the figure the brief gives; it is not a planning standard. Defined once, in
# `economics`, because the detail view lets it be overridden and both have to
# start from the same number.
SQM_PER_UNIT = EC.SQM_PER_UNIT

# The brief's two-step approach: rank broadly, then pay for ÖREB only on the
# head of the list.
SHORTLIST = 50

# The committed result database was generated with this cascade boundary. A
# control outside it would look interactive while returning exactly the same
# rows, because parcels below it were never stored.
MIN_STORED_DELTA = 130

# Parcel area used to be a second such boundary — stored 300–5,000 m², offered
# 300–5,000 m² — so the slider ran into a wall rather than into the end of the
# data, and the largest lead in the canton sat behind it. The cascade now keeps
# every area, and this control genuinely filters.
#
# The steps are uneven on purpose: 95% of candidates are smaller than 3,400 m²,
# so a linear slider spends nearly all its travel on the remaining 5% and cannot
# be set precisely where the parcels actually are.
AREA_STEPS = (
    0, 100, 200, 300, 400, 500, 750, 1000, 1500, 2000, 3000,
    5000, 7500, 10000, 15000, 25000, 50000, 100000, float("inf"),
)
NO_LIMIT = AREA_STEPS[-1]

#: Lower end unchanged, so the familiar list is still what opens; the upper end
#: is open, which is the whole point of the change.
AREA_DEFAULT = (300, NO_LIMIT)

# The per-parcel link. Not a map: the binding cadastre extract, which lists every
# public-law restriction on the parcel and is the natural next step once a
# candidate looks interesting. The format goes in the path, the identifier is a
# query parameter — the same shape `oereb.py` records, and the URL the cadastre
# prints into its own QR code.
OEREB_PDF = "https://api.geo.ag.ch/v2/oereb/extract/pdf/?EGRID="

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


def _column(cache, name):
    blank = pd.Series("", index=cache.index)
    return cache.get(name, blank).fillna("")


def with_extract(cache):
    """EGRIDs whose cached answer is a complete extract.

    Presence in the cache is not the same question. A row written before the
    legal basis was stored carries the restrictions but none of the documents,
    and a row written after a failed request carries only the error — one
    transient 502 would otherwise leave a parcel permanently unchecked while the
    interface called the shortlist complete. Both get asked again on the next
    run, which costs one request each.
    """
    if cache.empty:
        return cache.index[:0]
    return cache.index[_column(cache, "details") != ""]


def failed_egrids(cache):
    """EGRIDs whose last request failed. Reported rather than retried on every
    rerun: the cadastre answered once with an error, and hammering it from a
    page refresh would not change that."""
    if cache.empty:
        return cache.index[:0]
    return cache.index[(_column(cache, "error") != "") & (_column(cache, "details") == "")]


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
                hard, notable, err, extract = fut.result()
            except Exception as exc:
                hard, notable, err, extract = [], [], str(exc), None
            con.execute(
                "INSERT OR REPLACE INTO oereb_cache "
                "(egrid, hard, notable, error, checked_at, details) "
                "VALUES (?,?,?,?,datetime('now'),?)",
                (
                    egrid, "; ".join(hard), "; ".join(notable), err or "",
                    json.dumps(extract, ensure_ascii=False) if extract else "",
                ),
            )
            con.commit()
            done += 1
            if progress:
                progress(done / max(len(egrids), 1), f"ÖREB {done}/{len(egrids)}")
    con.close()


# A Railway volume survives image deployments. Seed it when empty, then widen
# its schema before any DataFrame reads it. Without the second step, adding a
# result column in a new release leaves a populated volume on the old schema and
# the UI crashes with a KeyError when it renders that column.
paths.ensure_db()
with sqlite3.connect(DB) as con:
    _ingest.schema(con)
parcels, runs = load()
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

# Step 2 of the brief is a conditional view, not a second page: one session-state
# key decides whether this script draws the list or one parcel. Nothing about the
# app's structure changes, and the back link is the key going away again.
if detail.selected():
    detail.page(parcels, read_oereb_cache(), price_of)
    st.stop()

st.title("Verdichtungspotenzial — Kanton Aargau")

# ── controls ────────────────────────────────────────────────────────────────
c1, c2, c3, c4, c5 = st.columns(5)
min_delta = c1.number_input(
    "Mindestpotenzial (m² GF)",
    MIN_STORED_DELTA,
    5000,
    MIN_STORED_DELTA,
    10,
    help=(
        f"Die Ergebnisdatenbank enthält nur Parzellen ab {MIN_STORED_DELTA} m² "
        "Potenzial. Für eine tiefere Grenze muss die Kaskade neu gerechnet werden."
    ),
)
area = c2.select_slider(
    "Parzellenfläche (m²)",
    options=AREA_STEPS,
    value=AREA_DEFAULT,
    format_func=lambda v: "ohne Limite" if v == NO_LIMIT else f"{v:,.0f}",
    help=(
        "Deckt die ganze Ergebnisdatenbank ab: Die Kaskade speichert jede "
        f"Parzellenfläche (grösste gespeicherte: {parcels['area'].max():,.0f} m²). "
        "«Ohne Limite» lässt das obere Ende offen — die frühere feste Obergrenze "
        "von 5,000 m² hat die grössten Grundstücke gar nicht erst gezeigt."
    ),
)
municipalities = sorted(parcels["municipality"].dropna().unique())
chosen = c3.multiselect("Gemeinde (leer = alle)", municipalities)
parcel_type = c4.selectbox(
    "Grundstückstyp",
    ("Bebaut", "Unbebaut", "Alle"),
    help=(
        "«Unbebaut» bedeutet: Im GWR ist kein stehendes Gebäude irgendeiner "
        "Nutzungsklasse mit dieser Parzelle verknüpft."
    ),
)
top_n = c5.number_input(
    "Anzahl Resultate",
    5,
    SHORTLIST,
    20,
    5,
    help=(
        f"Maximal {SHORTLIST}: Nur diese Shortlist wird gegen den ÖREB-Kataster "
        "geprüft."
    ),
)

#: Whether the cascade can be recomputed in this environment. False on the
#: deployment, which carries the results but not the ~600 MB of source geodata.
_full_run = _ingest.geodata_available()

c6, c7, c8 = st.columns([2, 2, 1])
# The brief lists the year-built cutoff among the filter inputs. Unlike the
# other six it is a pipeline parameter, not a display filter: the age rule runs
# inside the cascade, where a parcel whose buildings are all certainly newer
# never becomes a row at all. So it takes effect on the next recompute — and
# where no recompute is possible it takes effect never, which is why the control
# is disabled there rather than silently doing nothing.
min_age = c8.number_input(
    "Mindestalter (Jahre)", 0, 100, 15, 1,
    disabled=not _full_run,
    help=(
        "Parzellen, deren Gebäude alle sicher jünger sind, werden aussortiert. "
        "Ohne exaktes Baujahr entscheidet die GWR-Bauperiode — eine Periode, "
        "die die Grenze überspannt, bleibt Kandidat. Wirkt beim nächsten "
        "«Neu berechnen», nicht auf die bereits angezeigte Liste."
        if _full_run else
        "Hier nicht änderbar: Das Alterskriterium wirkt in der Kaskade, und die "
        "kann in dieser Umgebung nicht neu gerechnet werden (die Geodaten fehlen). "
        "Die Liste ist mit 15 Jahren gerechnet; für einen anderen Wert lokal neu "
        "rechnen und mitdeployen."
    ),
)
hide_inventory = c6.checkbox(
    "Inventarisierte Gebäude ausblenden", value=False,
    help="Bauinventar und Kurzinventar verbieten einen Ersatzneubau nicht, "
         "erschweren ihn aber. Geschützte Gebäude sind ohnehin ausgeschlossen.",
)
hide_design_plan = c7.checkbox(
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
    if parcel_type == "Bebaut":
        out = out[out["buildings"] > 0]
    elif parcel_type == "Unbebaut":
        out = out[out["buildings"] == 0]
    if hide_inventory:
        out = out[out["heritage"].fillna("") == ""]
    if hide_design_plan:
        out = out[out["design_plan"] == 0]
    return rank_candidates(out, parcel_type)


df = select(parcels)
shortlist = df.head(SHORTLIST)

# ── cascade step 5: ÖREB, shortlist only ────────────────────────────────────
cache = read_oereb_cache()
known = shortlist["egrid"].isin(with_extract(cache))
pending = shortlist.loc[~known & shortlist["egrid"].notna() & (shortlist["egrid"] != ""), "egrid"]

run_col, note_col = st.columns([1, 4])
run = run_col.button(
    "▶ Neu berechnen" if _full_run else "▶ ÖREB prüfen",
    type="primary",
    # The label and the help have to agree with what this environment can
    # actually do. Deployed there is no geodata, so promising a recompute would
    # be a lie the user only discovers by pressing the button.
    help=(
        f"Rechnet die Filterkaskade für alle {len(runs)} Gemeinden neu und fragt "
        f"anschliessend den ÖREB-Kataster für die Shortlist ab. Rund zwei "
        f"Minuten. Die Parzellengeometrien werden dabei nicht neu vom WFS "
        f"geladen — dafür data/parcels_*.xml löschen."
        if _full_run else
        "Fragt den ÖREB-Kataster für die Shortlist ab. Die Kaskade kann hier "
        "nicht neu gerechnet werden — dafür fehlen die Geodaten; das geschieht "
        "lokal und wird mitdeployt."
    ),
)
retry = int(shortlist["egrid"].isin(failed_egrids(cache)).sum())
if pending.empty:
    note_col.success(f"Shortlist vollständig ÖREB-geprüft ({len(shortlist)} Parzellen).")
else:
    note_col.info(
        f"{len(shortlist) - len(pending)} von {len(shortlist)} der Shortlist geprüft. "
        + (f"{retry} Abfrage(n) sind fehlgeschlagen und werden beim nächsten Lauf "
           "erneut versucht. " if retry else "")
        + "Ungeprüfte Parzellen werden angezeigt, aber noch nicht ausgeschlossen."
    )

if run:
    import ingest

    # Deployed, the source geodata is not present, so only the ÖREB half can
    # run. Asked up front rather than discovered: attempting it raised an
    # IndexError from globbing an absent dataset, which would have reached the
    # user as a traceback instead of an explanation.
    full = ingest.geodata_available()
    bar = st.progress(0.0, "Kaskade" if full else "ÖREB")
    if full:
        ingest.recompute(
            progress=lambda f, t: bar.progress(f * 0.2, "Kaskade — " + t),
            built_after=date.today().year - int(min_age),
        )
    else:
        st.info(
            "Geodaten in dieser Umgebung nicht vorhanden — die Kaskade wird "
            "nicht neu gerechnet, nur der ÖREB-Kataster abgefragt. Die "
            "Nutzungsplanung ändert sich jährlich, das GWR quartalsweise; "
            "neu gerechnet wird lokal und das Resultat mitdeployt."
        )

    # The shortlist the ÖREB step should pay for only exists once the table has
    # been rewritten, so the filters are applied a second time here rather than
    # reusing the selection made from the pre-run data.
    load.clear()
    fresh, _ = load()
    fresh_short = select(fresh).head(SHORTLIST)
    todo = fresh_short.loc[
        ~fresh_short["egrid"].isin(with_extract(read_oereb_cache()))
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
MUNICIPALITIES_AG = 196

assessed = int(runs["assessed"].sum())
no_az = int(runs["no_az"].sum())
missing = MUNICIPALITIES_AG - len(runs)

# Why a parcel could not be assessed, aggregated across the canton. The lump
# figure alone cannot distinguish "no building zone here" from "zoned, but the
# published figure does not convert into floor area" — and §3.5 step 1 asks for
# the distinction rather than a silent skip.
reasons = {}
for blob in runs.get("reasons", pd.Series(dtype=str)).dropna():
    try:
        for k, v in json.loads(blob).items():
            reasons[k] = reasons.get(k, 0) + int(v)
    except (ValueError, TypeError):
        continue
reason_text = (
    " Davon " + ", ".join(f"{v:,} wegen {k}" for k, v in sorted(reasons.items())) + "."
    if reasons else ""
)

st.caption(
    f"{len(runs)} von {MUNICIPALITIES_AG} Gemeinden ausgewertet · {assessed:,} Parzellen "
    f"beurteilt · {no_az:,} nicht beurteilbar (keine Bauzone mit verwertbarer "
    f"Nutzungsziffer).{reason_text} "
    f"{missing} Gemeinden publizieren gar keine Nutzungsziffer und fehlen deshalb "
    f"vollständig. {len(df):,} Treffer nach Filter → Shortlist {len(shortlist)} → "
    f"{len(excluded)} durch ÖREB ausgeschlossen."
)

# Reported only when it is wrong, like the other exceptions in this interface.
# Phrased as the consequence rather than the cause: "no volume mounted" means
# nothing to the person using this, but losing the cadastre answers does.
if paths.on_persistent_disk() is False:
    st.warning(
        "Kein persistenter Speicher eingebunden — abgefragte ÖREB-Auszüge gehen "
        "bei jedem Deployment verloren und müssen neu abgefragt werden. "
        "(Railway: Volume auf /data mounten.)"
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
        "unbebaut (kein stehendes GWR-Gebäude)" if r["buildings"] == 0 else "",
        r["heritage"] or "",
        "Gestaltungsplan — AZ evtl. überlagert" if r["design_plan"] else "",
        r["_notable"] or "",
        # Naming the metric only when it is not the canton's usual one:
        # labelling an Überbauungsziffer "AZ" would be a real error for an
        # architect reading the list, but repeating "AZ" on every row is noise.
        F.METRIC_LABELS.get(r["metric"], "") if r["metric"] not in ("", "AZ") else "",
        f"nur {r['zone_share'] * 100:.0f}% in der Bauzone" if r["zone_share"] < 0.95 else "",
        f"{r['buildings']} Gebäude" if r["buildings"] > 1 else "",
        "" if r["_checked"] else "ÖREB offen",
    ]
    return " · ".join(x for x in bits if x) or "frei"


# Column order follows the brief: who and where, then how old and what, then
# what the zone allows, then the answer. The diagnostics that explain how the
# answer was reached sit after it — putting them first pushed the potential, the
# unit estimate and the link off the right edge, which is the whole payload.
final = final.copy()
final["_land_price_ref"] = final.apply(price_of, axis=1)
final["_land_price"] = final["_land_price_ref"].map(
    lambda ref: ref.price_chf_m2 if ref else None
)
final["_land_price_scope"] = final["_land_price_ref"].map(
    lambda ref: ref.scope if ref else "—"
)
final["_land_price_as_of"] = final["_land_price_ref"].map(
    lambda ref: ref.as_of if ref else "—"
)
final["_land_price_source"] = final["_land_price_ref"].map(
    lambda ref: ref.source_url or None if ref else None
)

view = pd.DataFrame(
    {
        "Adresse": final["address"].fillna("—").replace("", "—"),
        "Gemeinde": final["municipality"],
        "Parzelle": final["parcel"],
        "Typ": final["buildings"].map(lambda n: "unbebaut" if n == 0 else "bebaut"),
        "Baujahr": final["built"].map(F.short_year),
        "Nutzung": final["use_class"].map(F.short_use),
        "Zone": final["zone"].fillna("—").replace("", "—"),
        "Ziffer": final["az"],
        # Shown since the area filter stopped being a fixed 300–5,000 m² window:
        # it is now the dimension the user moves, and a row that arrives from the
        # open upper end has to say how large it actually is. It also completes
        # the formula on screen — Fläche × Ziffer − Bestand ≈ Potenzial.
        "Fläche m²": final["area"].round(0),
        "Potenzial m² (Schätzung)": final["delta"].round(0),
        f"≈ Whg. (à {SQM_PER_UNIT} m²)": (final["delta"] / SQM_PER_UNIT).round(1),
        "Landpreis Ref. CHF/m²": final["_land_price"],
        "≈ Ref.-Landwert CHF": (final["_land_price"] * final["area"]).round(-3),
        "≈ Landwert / Potenzial-GF": (
            final["_land_price"] * final["area"] / final["delta"]
        ).round(0),
        "Preisebene": final["_land_price_scope"],
        "Preisstand": final["_land_price_as_of"],
        "Preisquelle": final["_land_price_source"],
        "Status": final.apply(status, axis=1),
        # The links answer different questions. AGIS is where ownership gets
        # looked up — the manual step the brief deliberately keeps manual —
        # ÖREB is the binding list of public-law restrictions, and Google makes
        # the real-world site inspection one click instead of an address copy.
        "AGIS": final.apply(lambda r: L.agis_link(r["e"], r["n"]), axis=1),
        # Blank rather than a broken link where the parcel carries no EGRID.
        "ÖREB": final["egrid"].map(lambda e: OEREB_PDF + e if e else None),
        "Google Maps": final.apply(
            lambda r: L.google_map_link(r["e"], r["n"]), axis=1
        ),
        # Street View is asked from the building's ENTRANCE, not the parcel
        # centre. Google returns a panorama only within roughly 50 m of the
        # requested point, and a representative point sits 23–68 m inside the
        # plot — far enough that the parcel centre reliably returned a black
        # screen. The entrance faces the road, where the panoramas are. Older
        # Railway volumes predate the entrance columns, so ``Series.get`` plus
        # the parcel coordinate fallback keeps them compatible instead of
        # raising a KeyError during rendering.
        "Street View": final.apply(
            lambda r: L.google_street_view_link(
                r.get("sv_e"), r.get("sv_n"), r["e"], r["n"]
            ),
            axis=1,
        ),
    }
)

# Selecting a row is how a parcel opens. Streamlit cannot put a callback inside
# a dataframe cell, so the "Analysieren" control the brief describes is the
# selection column the table grows here — one click, in the row itself, next to
# the links it sits with in the brief.
event = st.dataframe(
    view,
    key="hotlist",
    on_select="rerun",
    selection_mode="single-row",
    width="stretch",
    # Tall enough that the full list is one glance rather than a scroll; the
    # brief asks for a top 20 and 20 rows is the default.
    height=min(len(view), 20) * 35 + 40,
    hide_index=True,
    column_config={
        "AGIS": st.column_config.LinkColumn("AGIS", display_text="Karte", width="small"),
        "ÖREB": st.column_config.LinkColumn("ÖREB", display_text="PDF", width="small"),
        "Google Maps": st.column_config.LinkColumn(
            "Google", display_text="Karte", width="small"
        ),
        "Street View": st.column_config.LinkColumn(
            "Street View", display_text="Öffnen", width="small"
        ),
        "Preisquelle": st.column_config.LinkColumn(
            "Preisquelle", display_text="Quelle", width="small"
        ),
        "Fläche m²": st.column_config.NumberColumn(
            format="%.0f",
            width="small",
            help="Parzellenfläche laut Kataster — die Grösse, die der Regler links begrenzt.",
        ),
        "Potenzial m² (Schätzung)": st.column_config.NumberColumn(format="%.0f"),
        "Landpreis Ref. CHF/m²": st.column_config.NumberColumn(
            format="CHF %.0f",
            help=(
                "Grobe Referenz, keine Parzellenbewertung. Genauere Gemeinde- "
                "oder Zonenwerte aus land_prices.csv haben Vorrang vor dem "
                "kantonalen Rückfallwert."
            ),
        ),
        "≈ Ref.-Landwert CHF": st.column_config.NumberColumn(
            format="CHF %.0f",
            help="Parzellenfläche × Landpreisreferenz; ohne Gebäude-, Abbruch- oder Nebenkosten.",
        ),
        "≈ Landwert / Potenzial-GF": st.column_config.NumberColumn(
            format="CHF %.0f/m²",
            help=(
                "Referenz-Landwert ÷ zusätzliches Geschossflächenpotenzial. "
                "Ein tieferer Wert ist als grober Screening-Indikator günstiger; "
                "er ist keine Projekt-Rendite und enthält bei bebauten Parzellen "
                "nicht den Wert des bestehenden Gebäudes."
            ),
        ),
        # The address is the row's identity, so it gets the width it needs.
        # Gemeinde stays a column of its own despite looking redundant with it:
        # the postal town differs from the political municipality on 28% of
        # these parcels, and sometimes names a different place entirely
        # (Densbüren → Asp, Küttigen → Rombach), which is also the dimension
        # the filter above works in.
        "Adresse": st.column_config.TextColumn(width="large"),
        "Zone": st.column_config.TextColumn(width="medium"),
        # Truncation here is acceptable: the deciding word comes first.
        "Status": st.column_config.TextColumn(width="medium"),
        # Header shortened to buy that width back. "≈" plus the tooltip and the
        # caption below carry the assumption the brief asks to be explicit about.
        f"≈ Whg. (à {SQM_PER_UNIT} m²)": st.column_config.NumberColumn(
            "≈ Whg.",
            help=f"Grobe Umrechnung des Potenzials in mögliche zusätzliche "
                 f"Wohnungen — Annahme {SQM_PER_UNIT} m² pro Wohnung, keine "
                 f"Planungsgrösse.",
        ),
    },
)

st.caption(
    "Zeile anwählen öffnet die Einzelanalyse: Grunddaten, Potenzial und "
    "Residualwertrechnung mit eigenen Annahmen, als PDF exportierbar."
)

# `final` is what `view` was built from, in the same order, so the position the
# table reports is the row the user pointed at.
chosen_rows = list(event.selection["rows"]) if event and event.selection else []
if chosen_rows:
    detail.open_parcel(detail.parcel_id(final.iloc[chosen_rows[0]]))
    st.rerun()

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
    "«Karte» öffnet die Parzelle im AGIS-Geoportal — dort lassen sich die "
    "Eigentumsverhältnisse mit dem eigenen eGovernment-Login nachschlagen. "
    "Der ÖREB-Auszug ist der rechtsverbindliche Katasterauszug des Kantons; "
    "er listet alle Eigentumsbeschränkungen, aber keine Eigentümer. Google "
    "Maps öffnet die aus der LV95-Parzellenkoordinate umgerechnete Position; "
    "Street View verwendet wenn vorhanden den GWR-Gebäudeeingang und springt "
    "zum nächstgelegenen verfügbaren Panorama. Landpreis, Referenz-Landwert "
    "und Landwert pro Potenzial-GF sind grobe Benchmarks, keine "
    "Bewertung: Der mitgelieferte Rückfallwert von CHF 950/m² ist der von "
    "Wüest Partner publizierte Aargauer Median für voll erschlossenes, "
    "unbebautes EFH-Bauland mit tiefer Ausnützung (Q2 2021). "
    "Derzeit ist deshalb für alle Gemeinden nur die Preisebene «Kanton AG» "
    "verfügbar. Belastbare Gemeinde-/Zonenwerte von Wüest Partner sind "
    "lizenzpflichtig und können in land_prices.csv ergänzt werden."
)
