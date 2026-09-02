import concurrent.futures
import hashlib
import json
import sqlite3
from datetime import date

import pandas as pd
import streamlit as st

import detail
import economics as EC
import formatting as F
import ingest as _ingest
import land_cover as LC
import links as L
import navigation
import oereb as O
import paths
import searches as SR
import ui_components
import workflow as WF
from ranking import rank_candidates

# Rule of thumb, and labelled as one in the table header. 90 m² per dwelling is
# the figure the brief gives; it is not a planning standard. Defined once, in
# `economics`, because the detail view lets it be overridden and both have to
# start from the same number.
SQM_PER_UNIT = EC.SQM_PER_UNIT

# The brief's two-step approach: rank broadly, then pay for ÖREB only on the
# head of the list.
SHORTLIST = 50
RESULT_LIMITS = (5, 10, 20, 25, 50)

# The committed result database was generated with this cascade boundary. A
# control outside it would look interactive while returning exactly the same
# rows, because parcels below it were never stored.
MIN_STORED_DELTA = 130

# Parcel area used to be capped at 5,000 m², which hid the largest leads in the
# canton. The design uses explicit from/to fields instead of a range slider, so
# the upper value is optional: an empty field means there is no limit.
AREA_MIN_DEFAULT = 300
AREA_MAX_DEFAULT = None

# The per-parcel link. Not a map: the binding cadastre extract, which lists every
# public-law restriction on the parcel and is the natural next step once a
# candidate looks interesting. The format goes in the path, the identifier is a
# query parameter — the same shape `oereb.py` records, and the URL the cadastre
# prints into its own QR code.
OEREB_PDF = "https://api.geo.ag.ch/v2/oereb/extract/pdf/?EGRID="

#: Only Aargau has been ingested. The others are listed and labelled rather
#: than omitted: a selector that hides them implies they were never planned,
#: and one that offers them as working options returns an empty list that
#: reads as a fault in the app rather than as the end of the data.
CANTONS = (
    "Aargau",
    "Luzern (noch nicht verfügbar)",
    "Zürich (noch nicht verfügbar)",
)

PARCEL_TYPES = ("Alle", "Bebaut", "Unbebaut")

#: Every widget key the filter row owns. One list, not three: the reset
#: button clears exactly these, a saved search captures exactly these, and
#: applying one restores exactly these — three separate copies would drift,
#: and a key missing from one of them is a filter that silently does not
#: save, or does not reset.
FILTER_KEYS = (
    "screening_query", "screening_min_delta", "screening_area_min",
    "screening_area_max",
    "screening_municipality", "screening_type", "screening_ziffer",
    "screening_hide_inventory", "screening_hide_design_plan",
    "screening_hide_transport", "screening_top_n", "screening_min_age",
    "screening_canton",
)

#: Human labels for the "these values no longer exist" report `_apply_pending_
#: search` leaves behind — keyed by widget, not by database column, since
#: that is what the person restoring a search recognises.
_FILTER_LABELS = {
    "screening_municipality": "Gemeinde",
    "screening_canton": "Kanton",
    "screening_type": "Grundstückstyp",
    "screening_area_min": "Parzellenfläche von",
    "screening_area_max": "Parzellenfläche bis",
    "screening_ziffer": "Mind. AZ",
    "screening_min_delta": "Min. Potenzial m²",
    "screening_top_n": "Anzahl Resultate",
    "screening_min_age": "Mindestalter",
}

#: A parked "load this saved search" request, applied by `_apply_pending_search`
#: at the top of `page()` — before any filter widget exists. The same two-key
#: pattern `navigation.py` uses for page jumps, for the same reason: Streamlit
#: refuses `st.session_state.screening_min_delta = ...` once the widget keyed
#: `screening_min_delta` has been instantiated during the current run, and the
#: apply button is drawn well after the filter row.
PENDING_SEARCH = "screening_apply_search"

#: Where `_apply_pending_search` leaves the list of stored values it had to
#: drop, so the saved-search section — drawn after the filters — can tell the
#: user rather than silently discarding them.
SKIPPED_SEARCH_VALUES = "screening_skipped_search_values"


_SCREENING_CSS = """
<style>
.screening-page-intro {
  margin: 10px 0 20px;
}

.screening-page-kicker {
  margin-bottom: 7px;
  color: #9a9aa6;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: .1em;
  text-transform: uppercase;
}

.screening-page-intro h1 {
  margin: 0;
  font-size: 21px;
  font-weight: 600;
  letter-spacing: -.015em;
}

.screening-page-intro p {
  max-width: 70ch;
  margin: 7px 0 0;
  color: #77777f;
  font-size: 12.5px;
  line-height: 1.35;
  text-wrap: pretty;
}

.st-key-screening_header {
  margin: 10px 0 10px;
}

.st-key-screening_header .screening-page-intro {
  margin: 0;
}

.st-key-screening_header_actions,
.st-key-screening_header_actions [data-testid="stHorizontalBlock"] {
  justify-content: flex-end;
  align-items: end;
}

.st-key-screening_header_actions {
  gap: 8px !important;
}

.st-key-screening_header_actions > [data-testid="stElementContainer"],
.st-key-screening_header_actions > [data-testid="stLayoutWrapper"] {
  flex: 0 0 119px !important;
  width: 119px !important;
}

.st-key-screening_header_actions [data-testid="stBaseButton-secondary"],
.st-key-screening_header_actions [data-testid="stDownloadButton"] button,
.st-key-screening_header_actions [data-testid="stPopoverButton"] {
  width: 119px !important;
  height: 30px !important;
  min-height: 30px !important;
  padding: 0 12px !important;
  border-radius: 6px !important;
  font-size: 12px !important;
  font-weight: 500 !important;
  white-space: nowrap;
}

/* The export's saved-search action is a plain button. Streamlit's popover adds
   a material expand_more glyph; keep the real popover behaviour but remove the
   extra glyph so the trigger keeps the supplied 30px button silhouette. */
.st-key-screening_header_actions .stPopover button [data-testid="stIconMaterial"],
.st-key-screening_header_actions .stPopover button .material-symbols-rounded {
  display: none !important;
}

.st-key-screening_filters {
  margin-bottom: 0;
  border: 1px solid #eaeaee;
  border-radius: 9px;
  background: #fff;
  gap: 0;
  overflow: hidden;
}

.st-key-screening_filters > [data-testid="stVerticalBlock"] {
  gap: 0;
}

.st-key-screening_filters [data-testid="stHorizontalBlock"] {
  align-items: end;
}

.st-key-screening_filter_header {
  display: flex;
  position: relative;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  border-bottom: 1px solid #f0f0f3;
}

.st-key-screening_filter_header > [data-testid="stElementContainer"] {
  flex: 0 0 auto !important;
  width: auto !important;
}

.st-key-screening_filter_header .st-key-screening_reset,
.st-key-screening_filter_header .st-key-screening_reset [data-testid="stButton"] {
  display: flex;
  justify-content: flex-end;
  margin-left: auto;
}

.st-key-screening_filter_header > [data-testid="stLayoutWrapper"]
  > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child {
  flex: 1 1 auto !important;
}

.st-key-screening_filter_header > [data-testid="stLayoutWrapper"]
  > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child {
  flex: 0 0 auto !important;
  width: auto !important;
  margin-left: auto;
}

.st-key-screening_filter_header [data-testid="stHorizontalBlock"]
  > [data-testid="stColumn"]:first-child {
  flex: 1 1 auto !important;
}

.st-key-screening_filter_header [data-testid="stHorizontalBlock"]
  > [data-testid="stColumn"]:last-child {
  flex: 0 0 auto !important;
  width: auto !important;
  margin-left: auto;
}

.st-key-screening_filter_header [data-testid="stHorizontalBlock"]
  > [data-testid="stColumn"]:last-child > [data-testid="stVerticalBlock"] {
  align-items: flex-end;
}

.screening-filter-label {
  color: #9a9aa6;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: .1em;
  text-transform: uppercase;
}

.st-key-screening_filter_primary,
.st-key-screening_filter_numeric,
.st-key-screening_filter_flags {
  padding-inline: 16px;
}

.st-key-screening_filters [data-testid="stWidgetLabel"] p,
.screening-area-label,
.screening-exclusion-label {
  color: #8a8a94;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: .07em;
  line-height: 1.2;
  text-transform: uppercase;
}

/* The export bottom-aligns the exclusion group with the 30px parcel-search
   field. Its shorter checkbox row therefore starts lower than the search
   group; Streamlit otherwise leaves both labels on the same top baseline. */
.screening-exclusion-label {
  display: inline-block;
  transform: translateY(14px);
}

.st-key-screening_filters input,
.st-key-screening_filters [data-baseweb="select"] > div {
  height: 30px;
  min-height: 30px;
  font-size: 12.5px;
  font-weight: 400;
}

.st-key-screening_filters .react-aria-ComboBox [role="group"],
.st-key-screening_filters [data-testid="stNumberInputContainer"],
.st-key-screening_filters [data-testid="stTextInputRootElement"] {
  height: 30px;
  min-height: 30px;
}

.st-key-screening_filters .react-aria-ComboBox input[role="combobox"] {
  display: flex;
  align-items: center;
  height: 28px;
  min-height: 28px;
  padding: 0 8px;
  font-size: 12.5px;
  font-weight: 400;
  line-height: 28px;
}

.st-key-screening_filters .react-aria-ComboBox [role="group"] {
  align-items: center !important;
  padding-block: 0 !important;
}

.st-key-screening_filters .react-aria-ComboBox button {
  height: 28px;
  min-height: 28px;
}

/* BaseWeb's default fill is grey even when the surrounding filter card is
   white. The supplied Scope design uses white number, text and select fields
   with a thin neutral border, plus the green focus treatment. */
.st-key-screening_filters [data-baseweb="input"],
.st-key-screening_filters [data-baseweb="base-input"],
.st-key-screening_filters input,
.st-key-screening_filters [data-baseweb="select"] > div,
.st-key-screening_filters .react-aria-ComboBox [role="group"],
.st-key-screening_filters .react-aria-ComboBox [role="group"] > *,
.st-key-screening_filters [data-testid="stNumberInputContainer"],
.st-key-screening_filters [data-testid="stNumberInputContainer"] > *,
.st-key-screening_filters [data-testid="stNumberInputContainer"] button,
.st-key-screening_filters [data-testid="stTextInputRootElement"],
.st-key-screening_filters [data-testid="stTextInputRootElement"] > * {
  background: #fff !important;
}

.st-key-screening_filters [data-baseweb="input"],
.st-key-screening_filters [data-baseweb="select"] > div,
.st-key-screening_filters .react-aria-ComboBox [role="group"],
.st-key-screening_filters [data-testid="stNumberInputContainer"],
.st-key-screening_filters [data-testid="stTextInputRootElement"] {
  border-color: #e0e0e6 !important;
}

.st-key-screening_filters [data-baseweb="input"]:focus-within,
.st-key-screening_filters [data-baseweb="select"] > div:focus-within,
.st-key-screening_filters .react-aria-ComboBox [role="group"]:focus-within,
.st-key-screening_filters [data-testid="stNumberInputContainer"]:focus-within,
.st-key-screening_filters [data-testid="stTextInputRootElement"]:focus-within {
  border-color: #1c4e4a !important;
  box-shadow: 0 0 0 3px #e2eceb !important;
}

.st-key-screening_filter_primary {
  padding-top: 7px;
  padding-bottom: 7px;
}

.st-key-screening_filter_numeric {
  padding-top: 7px;
  padding-bottom: 7px;
  border-top: 1px solid #f2f2f5;
}

.st-key-screening_filter_flags {
  padding-top: 7px;
  padding-bottom: 9px;
  border-top: 1px solid #f2f2f5;
}

.st-key-screening_filter_flags [data-testid="stCheckbox"] p {
  color: #4a4a54;
  font-size: 11.5px;
  font-weight: 400;
  letter-spacing: 0;
  text-transform: none;
}

/* The export keeps the exclusions as one compact, left-aligned group. Equal
   Streamlit columns stretched them across the whole card and the help props
   added icons that the design does not contain. */
.st-key-screening_exclusion_options {
  flex-wrap: wrap !important;
  justify-content: flex-start;
  align-items: center;
  gap: 8px 22px !important;
}

.st-key-screening_exclusion_options > [data-testid="stElementContainer"] {
  flex: 0 0 auto !important;
  width: auto !important;
  min-width: 0;
}

.st-key-screening_exclusion_options [data-testid="stCheckbox"] label {
  align-items: center;
}

/* Streamlit offsets the checkbox square by ~2px to suit its default line
   height. Scope's 11.5px labels use a tighter line height, so that offset made
   the square visibly lower than the text. */
.st-key-screening_exclusion_options [data-testid="stCheckbox"] label > div:first-of-type {
  margin-top: 0;
}

/* The Claude export uses plain numeric fields. Keep keyboard entry and native
   validation, but remove Streamlit's +/- steppers so the controls match it. */
.st-key-screening_filters [data-testid="stNumberInputContainer"] > div:has(> [data-testid="stNumberInputStepDown"]) {
  display: none !important;
}

.st-key-screening_reset button {
  min-height: 20px;
  height: 20px;
  padding: 0;
  border: 0;
  background: transparent;
  color: #77777f;
  font-size: 11.5px;
  white-space: nowrap;
}

.st-key-screening_area_range [data-testid="stHorizontalBlock"] {
  gap: 6px;
}

.screening-range-separator {
  display: flex;
  min-height: 30px;
  align-items: center;
  justify-content: center;
  color: #9a9aa6;
  font-size: 12px;
}

.screening-area-label {
  display: block;
  margin-bottom: 6px;
  /* `st.html` sits one 12px line above Streamlit's native widget-label slot.
     Move only the painted label down; the range inputs already share the
     correct baseline with the two neighbouring number inputs. */
  transform: translateY(12px);
}

.st-key-screening_result_toolbar {
  min-height: 26px;
  margin: 8px 0 -5px;
  align-items: baseline !important;
  gap: 14px !important;
}

.st-key-screening_result_toolbar > [data-testid="stElementContainer"]:first-child {
  flex: 0 1 auto !important;
  width: auto !important;
}

.st-key-screening_result_toolbar > [data-testid="stElementContainer"]:nth-child(2) {
  flex: 1 1 auto !important;
  width: auto !important;
}

.st-key-screening_result_toolbar > [data-testid="stLayoutWrapper"] {
  flex: 0 0 126px !important;
  width: 126px !important;
  height: 26px !important;
}

.st-key-screening_result_toolbar .react-aria-ComboBox [role="group"],
.st-key-screening_result_toolbar .react-aria-ComboBox [role="group"] > * {
  background: #fff !important;
}

.st-key-screening_result_toolbar .react-aria-ComboBox [role="group"] {
  width: 54px;
  height: 26px;
  min-height: 26px;
  border-color: #e0e0e6 !important;
}

.st-key-screening_result_toolbar .react-aria-ComboBox input[role="combobox"] {
  height: 24px;
  min-height: 24px;
  padding: 0 4px 0 6px;
  font-size: 11.5px;
  font-weight: 400;
}

.st-key-screening_result_toolbar .react-aria-ComboBox button {
  width: 22px;
  height: 24px;
  min-height: 24px;
}

.st-key-screening_result_toolbar .react-aria-ComboBox [role="group"]:focus-within {
  border-color: #1c4e4a !important;
  box-shadow: 0 0 0 3px #e2eceb !important;
}

.screening-result-summary {
  display: flex;
  align-items: baseline;
  gap: 14px;
  min-height: 30px;
}

.screening-result-summary strong {
  font-size: 12.5px;
  font-weight: 600;
}

.screening-result-summary span,
.screening-result-sort {
  color: #9a9aa6;
  font-size: 11.5px;
}

.stHtml:has(.screening-result-sort) {
  display: flex;
  justify-content: flex-end;
}

.screening-result-summary code {
  color: #4a4a54;
  font-family: "IBM Plex Mono", monospace;
  font-size: 11.5px;
}

.st-key-screening_result_limit {
  width: 126px !important;
  min-width: 126px;
  height: 26px !important;
  min-height: 26px !important;
  flex: 0 0 auto !important;
  justify-content: flex-end;
  align-items: center;
  gap: 7px !important;
}

.st-key-screening_result_limit > [data-testid="stElementContainer"] {
  flex: 0 0 auto !important;
  width: auto !important;
  min-width: 0;
}

.st-key-screening_result_limit > .st-key-screening_top_n {
  flex: 0 0 62px !important;
  width: 62px !important;
}

.screening-result-limit-label {
  color: #9a9aa6;
  font-size: 11.5px;
  white-space: nowrap;
}

.st-key-screening_result_limit [data-baseweb="select"] {
  min-width: 62px;
}

/* The native frame remains in the element tree as a regression-test and CSV
   oracle. The visible table is the local design-native component below. */
.st-key-screening_native_table,
.st-key-screening_native_actions,
.st-key-screening_non_design_footer {
  display: none;
}

.st-key-screening_design_table,
.st-key-screening_design_table [data-testid="stCustomComponentV1"],
.st-key-screening_design_table iframe {
  display: block;
  width: 100% !important;
  min-width: 0 !important;
  max-width: 100% !important;
}

@media (max-width: 960px) {
  .st-key-screening_result_toolbar > [data-testid="stLayoutWrapper"]
    > [data-testid="stHorizontalBlock"] {
    flex-wrap: wrap;
    gap: 8px;
  }

  .st-key-screening_result_toolbar > [data-testid="stLayoutWrapper"]
    > [data-testid="stHorizontalBlock"] > [data-testid="stElementContainer"]:first-child {
    flex: 0 0 100% !important;
    width: 100% !important;
  }

  .st-key-screening_result_toolbar > [data-testid="stLayoutWrapper"]
    > [data-testid="stHorizontalBlock"] > [data-testid="stElementContainer"]:nth-child(2) {
    margin-left: auto;
  }
}

@media (max-width: 760px) {
  .screening-page-intro {
    margin: 8px 0 16px;
  }

  .screening-page-intro h1 {
    font-size: 19px;
    line-height: 1.25;
  }

  .st-key-screening_filters {
    overflow: visible;
  }

  .st-key-screening_header [data-testid="stHorizontalBlock"] {
    flex-wrap: wrap;
  }

  .st-key-screening_header [data-testid="stColumn"] {
    min-width: 100%;
  }

  .st-key-screening_filter_primary,
  .st-key-screening_filter_numeric,
  .st-key-screening_filter_flags {
    padding-inline: 12px;
  }

  .st-key-screening_result_toolbar {
    flex-wrap: wrap;
  }

  .screening-result-summary {
    align-items: flex-start;
    flex-direction: column;
    gap: 3px;
  }
}
</style>
"""

_SCREENING_INTRO = """
<div class="screening-page-intro">
  <div class="screening-page-kicker">Screening</div>
  <h1>Parzellen mit ungenutzter Ausnutzungsreserve</h1>
  <p>Abgeleitet aus Grundbuch, kommunalen Bau- und Zonenordnungen sowie
     amtlicher Vermessung. Ausnutzungsreserve = zulässige aBGF − bestehende aBGF.</p>
</div>
"""


def read_oereb_cache():
    """Not cached by Streamlit: it changes as a run progresses, and a stale read
    would show the button's own results as still missing."""
    con = sqlite3.connect(paths.DB)
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
    con = sqlite3.connect(paths.DB)
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


def parcel_key(row):
    """The stable key shared by calculated results and persistent workflow."""
    return int(row["bfs"]), str(row["parcel"])


def screening_table_rows(final, view):
    """Serialize the ranked result frame for the design-native table.

    The component receives display values and URLs only. Cadastral identity is
    carried separately so Python can validate every action against ``final``
    before changing workflow state or navigation.
    """
    rows = []
    for index, row in final.iterrows():
        shown = view.loc[index]
        raw_status = str(shown["Status"] or "")
        badges = screening_status_badges(raw_status)

        use = F.short_use(row.get("use_class"))
        object_type = (
            "Unbebaut" if int(row["buildings"]) == 0
            else use or f"{int(row['buildings'])} Gebäude"
        )
        price = row.get("_land_price")
        land_value = None if pd.isna(price) else float(price) * float(row["area"])
        rows.append(
            {
                "bfs": int(row["bfs"]),
                "parcel": str(row["parcel"]),
                "address": str(shown["Adresse"]),
                "municipality": str(row["municipality"]),
                "type": object_type,
                "year": F.short_year(row.get("built")) or "—",
                "zone": str(shown["Zone"]),
                "coeff": f"{float(row['az']):g}",
                "area": F.swiss(float(row["area"])),
                "potential": F.swiss(float(row["delta"])),
                "units": F.swiss(round(float(row["delta"]) / SQM_PER_UNIT)),
                "refPrice": "—" if pd.isna(price) else F.swiss(float(price)),
                "landValue": "—" if land_value is None else F.swiss(land_value),
                "priceSource": (
                    f"{row.get('_land_price_scope', '—')} · "
                    f"{row.get('_land_price_as_of', '—')}"
                ),
                "badges": badges,
                "saved": shown["Merkliste"] == "Gespeichert",
                "links": {
                    "gis": shown["AGIS"],
                    "oereb": shown["ÖREB"],
                    "google": shown["Google Maps"],
                    "streetView": shown["Street View"],
                },
            }
        )
    return rows


def screening_status_badges(raw_status):
    """Translate calculated facts into the prototype's compact badge language.

    The source data remains authoritative: this only changes how a fact is
    presented. Long or uncommon ÖREB findings become ``Prüfen`` and retain the
    complete wording as a tooltip instead of stretching the result table.
    Facts already visible in the object-type column (unbuilt/multiple buildings)
    are intentionally not repeated as status badges.
    """
    text = str(raw_status or "").strip()
    if text.casefold() in {"", "frei", "unbelastet"}:
        return [{"label": "Unbelastet", "tone": "clear"}]

    badges = []
    seen = set()

    def add(label, tone, detail=""):
        key = (label, tone)
        if key in seen:
            return
        seen.add(key)
        badge = {"label": label, "tone": tone}
        if detail and detail != label:
            badge["detail"] = detail
        badges.append(badge)

    # ÖREB joins multiple findings with semicolons, while the screening status
    # joins independent facts with middle dots. Treat both as badge boundaries.
    facts = [
        fact.strip()
        for group in text.split(" · ")
        for fact in group.split(";")
        if fact.strip()
    ]
    for fact in facts:
        folded = fact.casefold()
        if (
            folded.startswith("unbebaut")
            or (folded.endswith("gebäude") and folded.split(" ", 1)[0].isdigit())
        ):
            continue
        if "gestaltungsplan" in folded:
            add("Gestaltungsplan", "dev", fact)
        elif "inventar" in folded or "denkmal" in folded:
            add("Inventar", "heritage", fact)
        elif "lärm" in folded:
            add("Lärm ES II", "noise", fact)
        elif "gewässer" in folded:
            add("Gewässerabstand", "water", fact)
        elif "dienstbarkeit" in folded:
            add("Dienstbarkeit", "servitude", fact)
        elif "baurecht" in folded:
            add("Baurecht", "lease", fact)
        elif "öreb offen" in folded:
            add("ÖREB offen", "muted")
        else:
            add("Prüfen", "muted", fact)

    return badges[:2] or [{"label": "Unbelastet", "tone": "clear"}]


def dismissed_table_rows(parcels, hidden_keys, price_of, cache):
    """Serialize hidden leads for the prototype's restore panel."""
    rows = []
    for _, row in parcels.iterrows():
        key = parcel_key(row)
        if key not in hidden_keys:
            continue
        egrid = row.get("egrid")
        checked = egrid in cache.index
        notable = (cache.loc[egrid, "notable"] or "") if checked else ""
        facts = [
            row.get("heritage") or "",
            "Gestaltungsplan" if row.get("design_plan") else "",
            notable,
            "" if checked else "ÖREB offen",
        ]
        reference = price_of(row)
        land_value = (
            None
            if reference is None
            else float(reference.price_chf_m2) * float(row["area"])
        )
        use = F.short_use(row.get("use_class"))
        rows.append(
            {
                "bfs": key[0],
                "parcel": key[1],
                "address": str(row.get("address") or "—"),
                "municipality": str(row.get("municipality") or "—"),
                "type": (
                    "Unbebaut" if int(row["buildings"]) == 0
                    else use or f"{int(row['buildings'])} Gebäude"
                ),
                "potential": F.swiss(float(row["delta"])),
                "landValue": "—" if land_value is None else F.swiss(land_value),
                "badges": screening_status_badges(
                    " · ".join(fact for fact in facts if fact) or "frei"
                ),
            }
        )
    return rows


def resolve_table_event(event, final):
    """Return a validated ``(action, key)`` pair for a component event."""
    if not isinstance(event, dict) or event.get("type") not in {
        "analyse", "save", "hide", "restore",
    }:
        return None
    try:
        key = int(event["bfs"]), str(event["parcel"])
    except (KeyError, TypeError, ValueError):
        return None
    allowed = {
        (int(row.bfs), str(row.parcel))
        for row in final[["bfs", "parcel"]].itertuples(index=False)
    }
    return (event["type"], key) if key in allowed else None


def _valid_search_values(parcels, filters):
    """Split a saved search's stored values into what the current data and
    control ranges still accept, and what has to be dropped.

    A saved search can outlive the data it was drawn from — a municipality
    disappears from the current run, the AZ range narrows on a fresh cascade —
    and Streamlit's option-constrained widgets (multiselect, selectbox and a
    bounded number_input) raise `StreamlitAPIException`
    outright when handed a `session_state` value outside their current
    domain, rather than clamping it themselves. So the check has to happen
    here, before any widget sees the value, not after.

    Returns (values, skipped): `values` only ever holds entries `page()` can
    hand straight to `session_state`; `skipped` names, in German, which
    widgets lost a value so the caller can tell the user.
    """
    municipalities = set(parcels["municipality"].dropna().unique())
    az = parcels["az"].dropna()
    az_min, az_max = (float(az.min()), float(az.max())) if not az.empty else (0.0, 0.0)

    filters = dict(filters)
    # Searches saved before the design-alignment release stored one slider
    # tuple. Preserve those searches by expanding it into the two explicit
    # from/to fields now shown in the interface.
    legacy_area = filters.pop("screening_area", None)
    if (
        "screening_area_min" not in filters
        and "screening_area_max" not in filters
        and isinstance(legacy_area, (list, tuple))
        and len(legacy_area) == 2
    ):
        filters["screening_area_min"] = legacy_area[0]
        filters["screening_area_max"] = (
            None if legacy_area[1] == float("inf") else legacy_area[1]
        )

    values = {}
    skipped = []
    for key, value in filters.items():
        if key not in FILTER_KEYS:
            continue  # a search saved by an older or newer release
        if key == "screening_municipality":
            # Partial application, not all-or-nothing: the municipalities
            # still in the data are exactly as usable as they were when the
            # search was saved, and dropping the whole filter because one
            # neighbour merged away would lose more than it protects.
            kept = [m for m in (value or []) if m in municipalities]
            if len(kept) != len(value or []):
                skipped.append(_FILTER_LABELS[key])
            values[key] = kept
            continue
        if key == "screening_canton":
            ok = value in CANTONS
        elif key == "screening_type":
            ok = value in PARCEL_TYPES
        elif key == "screening_area_min":
            ok = value is not None and float(value) >= 0
        elif key == "screening_area_max":
            ok = value is None or float(value) >= 0
        elif key == "screening_ziffer":
            ok = value is None or az_min <= float(value) <= az_max
        elif key == "screening_min_delta":
            ok = value is not None and MIN_STORED_DELTA <= value <= 5000
        elif key == "screening_top_n":
            ok = value in RESULT_LIMITS
        elif key == "screening_min_age":
            ok = value is not None and 0 <= value <= 100
        else:
            ok = True  # free text and plain booleans have no domain to outlive
        if ok:
            values[key] = value
        else:
            skipped.append(_FILTER_LABELS.get(key, key))
    return values, skipped


def _apply_pending_search(parcels, state=None):
    """Apply a parked saved-search request, if any. Call before rendering any
    filter widget — see PENDING_SEARCH."""
    state = st.session_state if state is None else state
    filters = state.pop(PENDING_SEARCH, None)
    if filters is None:
        return
    values, skipped = _valid_search_values(parcels, filters)
    # A saved search is a full snapshot of FILTER_KEYS (see the save button in
    # `page()`), so a key it doesn't mention is cleared back to the widget's
    # own default rather than left at whatever the previous run happened to
    # have — "load this search" should mean exactly that search, not that
    # search layered on top of leftover state.
    for key in FILTER_KEYS:
        state.pop(key, None)
    state.pop("screening_area", None)
    state.update(values)
    state[SKIPPED_SEARCH_VALUES] = skipped


def _initial_widget_value(key, state=None, **default):
    """Feed restored state in as the widget default without defining it twice.

    Restoring a saved search writes the filter values before the widgets are
    created. Passing `value=`/`index=` as well makes Streamlit log that the same
    widget received two defaults, even when both values agree. Value-based
    widgets can consume that state and receive it as their constructor default;
    Selectboxes are index-based, so they keep their session-state value and
    simply omit the competing index.
    """
    state = st.session_state if state is None else state
    if key not in state:
        return default
    if "index" in default:
        return {}
    parameter = next(iter(default))
    return {parameter: state.pop(key)}


def page(parcels, decisions, db, price_of, land_price_references, runs):
    """The screening list: filters, ranking, the ÖREB check and the table."""
    # Before any filter widget below is created — see PENDING_SEARCH.
    _apply_pending_search(parcels)

    st.html(_SCREENING_CSS)
    with st.container(key="screening_header"):
        header_copy, header_action_column = st.columns(
            [5, 4], vertical_alignment="bottom"
        )
        header_copy.html(_SCREENING_INTRO)
        # Filled once `view` exists. A placeholder keeps data-dependent actions
        # beside the title instead of forcing them below the filter/result
        # summary where the prototype never places page-level actions.
        header_actions = header_action_column.container(
            key="screening_header_actions", horizontal=True
        )

    workflow_by_key = {
        (int(row.bfs), str(row.parcel)): row
        for row in decisions.itertuples(index=False)
    }
    hidden_keys = {
        key for key, row in workflow_by_key.items() if bool(row.hidden)
    }

    # ── controls ────────────────────────────────────────────────────────────────
    # Build the visual slots first so the query can still be instantiated before
    # the reset button (the reset regression test depends on that lifecycle)
    # while appearing in the compact FILTER header from the design.
    filter_box = st.container(key="screening_filters")
    filter_header = filter_box.container(
        key="screening_filter_header",
        horizontal=True,
        horizontal_alignment="distribute",
        vertical_alignment="center",
    )
    primary_box = filter_box.container(key="screening_filter_primary")
    numeric_box = filter_box.container(key="screening_filter_numeric")
    flags_box = filter_box.container(key="screening_filter_flags")
    filter_header.html('<span class="screening-filter-label">Filter</span>')
    if filter_header.button("Zurücksetzen", key="screening_reset"):
        for key in FILTER_KEYS:
            st.session_state.pop(key, None)
        st.session_state.pop("screening_area", None)
        st.rerun()

    # The prototype deliberately uses two readable filter rows instead of one
    # seven-column strip. Keep the widget creation order below unchanged — the
    # tests and saved-search state both rely on it — while assigning the
    # columns to the same 3 + 3 visual grouping as the design.
    primary = primary_box.columns(3)
    numeric = numeric_box.columns(3)
    c0, c4, c5 = primary
    c1, c2, c3 = numeric
    canton = c0.selectbox(
        "Kanton",
        CANTONS,
        key="screening_canton",
        **_initial_widget_value("screening_canton", index=0),
    )
    if canton != "Aargau":
        st.warning("Für diesen Kanton liegen noch keine Daten vor.")
        return
    min_delta = c1.number_input(
        "Min. Potenzial m²",
        min_value=MIN_STORED_DELTA,
        max_value=5000,
        step=10,
        key="screening_min_delta",
        **_initial_widget_value("screening_min_delta", value=MIN_STORED_DELTA),
    )
    ziffer = c2.number_input(
        "Mind. AZ",
        min_value=float(parcels["az"].min()),
        max_value=float(parcels["az"].max()),
        step=0.1,
        format="%g",
        placeholder="z. B. 0.8",
        key="screening_ziffer",
        **_initial_widget_value("screening_ziffer", value=None),
    )
    c3.html('<span class="screening-area-label">Fläche m² (von–bis)</span>')
    with c3.container(key="screening_area_range"):
        area_from, area_separator, area_to = st.columns([1, 0.08, 1])
        area_min = area_from.number_input(
            "Fläche von (m²)",
            min_value=0,
            step=50,
            key="screening_area_min",
            label_visibility="collapsed",
            placeholder="von",
            **_initial_widget_value(
                "screening_area_min", value=AREA_MIN_DEFAULT
            ),
        )
        area_separator.html(
            '<span class="screening-range-separator">–</span>'
        )
        area_max = area_to.number_input(
            "Fläche bis (m²)",
            min_value=0,
            step=50,
            key="screening_area_max",
            label_visibility="collapsed",
            placeholder="bis",
            **_initial_widget_value(
                "screening_area_max", value=AREA_MAX_DEFAULT
            ),
        )
    area_upper = float("inf") if area_max is None else area_max
    municipalities = sorted(parcels["municipality"].dropna().unique())
    chosen = c4.multiselect(
        "Gemeinde",
        municipalities,
        key="screening_municipality",
        placeholder="Alle Gemeinden",
    )
    parcel_type = c5.selectbox(
        "Objekttyp",
        PARCEL_TYPES,
        key="screening_type",
        format_func=lambda value: "Alle Objekttypen" if value == "Alle" else value,
    )
    #: Whether the cascade can be recomputed in this environment. False on the
    #: deployment, which carries the results but not the ~600 MB of source geodata.
    _full_run = _ingest.geodata_available()
    flag_area, query_area = flags_box.columns([3, 1], vertical_alignment="bottom")
    flag_area.html(
        '<span class="screening-exclusion-label">Ausschliessen</span>'
    )
    with flag_area.container(
        key="screening_exclusion_options",
        horizontal=True,
        vertical_alignment="center",
        gap="medium",
    ):
        hide_design_plan = st.checkbox(
            "Gestaltungsplan",
            key="screening_hide_design_plan",
            **_initial_widget_value("screening_hide_design_plan", value=False),
        )
        hide_inventory = st.checkbox(
            "Denkmalschutz / Inventar",
            key="screening_hide_inventory",
            **_initial_widget_value("screening_hide_inventory", value=True),
        )
        hide_transport = st.checkbox(
            "Strassen-/Bahnparzellen",
            key="screening_hide_transport",
            **_initial_widget_value("screening_hide_transport", value=False),
        )
    query = query_area.text_input(
        "Parzellen-Nr. suchen",
        key="screening_query",
        placeholder="z. B. 1284",
        icon=":material/search:",
    )

    # ── filter and rank (cascade steps 1–4) ─────────────────────────────────────
    def select(src):
        """A function rather than inline code because the Run button has to apply
        exactly these filters a second time, to the freshly recomputed table, before
        it knows which parcels the ÖREB step should pay for."""
        # User decisions are applied before ranking, so hiding a lead pulls the next
        # best candidate into the shortlist instead of merely leaving a blank row.
        visible = src.loc[
            [
                (int(bfs), str(parcel)) not in hidden_keys
                for bfs, parcel in zip(src["bfs"], src["parcel"])
            ]
        ]
        # Applied before ranking so a search returns the best matches, not the
        # matches that happen to survive the ranking of everything else.
        text = (query or "").strip().lower()
        if text:
            parcel_numbers = visible["parcel"].astype(str).str.lower()
            haystack = (
                parcel_numbers
                + " " + visible["address"].fillna("").str.lower()
                + " " + visible["municipality"].fillna("").str.lower()
            )
            exact_parcel = parcel_numbers.eq(text)
            # A typed parcel number is an identifier, not a fuzzy term. Prefer
            # exact parcel matches when they exist; otherwise keep the broader
            # address/municipality search supported by this control.
            visible = visible[
                exact_parcel
                if exact_parcel.any()
                else haystack.str.contains(text, regex=False)
            ]
        out = visible[
            (visible["delta"] >= min_delta)
            & (visible["area"].between(area_min, area_upper))
        ]
        if ziffer is not None:
            # Stored figures are rounded to three decimals. A half-unit tolerance
            # at the fourth decimal keeps an entered 0.8 equal to stored 0.800 while
            # not turning this exact-value field into an undocumented range filter.
            out = out[(out["az"] - float(ziffer)).abs() < 0.0005]
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
        if hide_transport:
            # NULL means an older/unclassified result, not a confirmed road. Keep
            # it visible rather than silently treating missing source data as proof.
            out = out[
                out["transport_share"].isna()
                | (out["transport_share"] < LC.MIN_TRANSPORT_SHARE)
            ]
        return rank_candidates(out, parcel_type)


    df = select(parcels)
    shortlist = df.head(SHORTLIST)

    # ── cascade step 5: ÖREB, shortlist only ────────────────────────────────────
    cache = read_oereb_cache()
    known = shortlist["egrid"].isin(with_extract(cache))
    pending = shortlist.loc[
        ~known & shortlist["egrid"].notna() & (shortlist["egrid"] != ""), "egrid"
    ]
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

    result_toolbar = st.container(
        key="screening_result_toolbar",
        horizontal=True,
        horizontal_alignment="right",
        vertical_alignment="center",
        gap="medium",
    )
    result_summary = result_toolbar.empty()
    result_toolbar.html(
        '<span class="screening-result-sort">Sortiert nach Potenzial ↓</span>'
    )
    with result_toolbar.container(
        key="screening_result_limit",
        horizontal=True,
        horizontal_alignment="right",
        vertical_alignment="center",
        gap="small",
    ):
        st.html('<span class="screening-result-limit-label">Anzeigen</span>')
        top_n = st.selectbox(
            "Anzeigen",
            RESULT_LIMITS,
            key="screening_top_n",
            label_visibility="collapsed",
            help=f"Maximal {SHORTLIST}: Nur diese Shortlist wird ÖREB-geprüft.",
            **_initial_widget_value("screening_top_n", index=2),
        )
    final = shortlist[shortlist["_hard"] == ""].head(int(top_n))

    # Kept for the non-design maintenance surface below. It is intentionally
    # absent from the customer-facing page but remains available to operators.
    MUNICIPALITIES_AG = 196
    assessed = int(runs["assessed"].sum())
    no_az = int(runs["no_az"].sum())
    missing = MUNICIPALITIES_AG - len(runs)
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

    if final.empty:
        st.info("Keine Parzelle erfüllt diese Kriterien.")
        return

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


    def saved_label(row):
        workflow_row = workflow_by_key.get(parcel_key(row))
        return "Gespeichert" if workflow_row is not None and workflow_row.saved else "—"


    def contact_label(row):
        workflow_row = workflow_by_key.get(parcel_key(row))
        if workflow_row is None or not workflow_row.saved:
            return "—"
        return WF.CONTACT_STATUS_LABELS.get(
            workflow_row.contact_status,
            WF.CONTACT_STATUS_LABELS[WF.DEFAULT_CONTACT_STATUS],
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
            "Merkliste": final.apply(saved_label, axis=1),
            "Kontaktstatus": final.apply(contact_label, axis=1),
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

    # Skipping nulls rather than summing them is the same rule the Merkliste
    # tiles use, for the same reason: one unpriced parcel would otherwise
    # blank the figure.
    priced = final[final["_land_price"].notna()]
    land_value_total = float((priced["area"] * priced["_land_price"]).sum())

    result_summary.markdown(
        '<div class="screening-result-summary">'
        f'<strong>{len(view)} Parzellen</strong>'
        '<span>Summe Potenzial '
        f'<code>{F.swiss(final["delta"].sum())}</code> m² · '
        'Summe Landwert '
        f'<code>CHF {F.swiss(land_value_total)}</code></span>'
        '</div>',
        unsafe_allow_html=True,
    )
    # ── saved searches ───────────────────────────────────────────────────────────
    # Beside the export because both act on the filters just arrived at, not on
    # the rows: a screening run is a research position — "Wohnzone, 800 m²
    # potential, Bezirk Horgen" — and retyping twelve controls to get back to it
    # is exactly the friction saving one removes.
    header_actions.download_button(
        "CSV exportieren",
        view.to_csv(index=False).encode("utf-8"),
        file_name="verdichtungspotenzial.csv",
        mime="text/csv",
        key="screening_csv",
    )

    # The prototype exposes one compact page action. Naming and managing saved
    # searches happens inside it instead of adding a permanent third field to
    # the page header.
    with header_actions.popover("Suche speichern"):
        search_name = st.text_input(
            "Name der Suche",
            key="screening_search_name",
            placeholder="z. B. Wohnzone Aarau",
        )
        if st.button("Speichern", width="stretch"):
            try:
                SR.save(
                    search_name,
                    {key: st.session_state.get(key) for key in FILTER_KEYS},
                    db,
                )
            except ValueError as error:
                st.error(str(error))
            else:
                st.session_state.pop("screening_search_name", None)
                st.toast(f"Suche „{search_name}“ gespeichert.")
                st.rerun()

        # Not cached, like `workflow.load`: a save or delete above must be
        # visible in this same picker on the next run.
        saved = SR.load(db)
        if not saved.empty:
            picked = st.selectbox(
                "Gespeicherte Suche",
                list(saved["name"]),
                key="screening_search_pick",
            )
            apply_col, delete_col = st.columns(2)
            if apply_col.button("Anwenden", width="stretch"):
                st.session_state[PENDING_SEARCH] = (
                    saved.set_index("name").loc[picked, "filters"]
                )
                st.rerun()
            if delete_col.button("Löschen", width="stretch"):
                SR.delete(picked, db)
                st.session_state.pop("screening_search_pick", None)
                st.toast(f"Suche „{picked}“ gelöscht.")
                st.rerun()

    # Reported once, right after the rerun that applied a search — not read
    # again on the next unrelated rerun, which is why `_apply_pending_search`
    # writes it and this is the only place that pops it back out.
    skipped = st.session_state.pop(SKIPPED_SEARCH_VALUES, [])
    if skipped:
        st.warning(
            "Diese Werte der geladenen Suche gibt es nicht mehr und wurden "
            "übersprungen: " + ", ".join(skipped) + "."
        )

    # The supplied design is a dense action table, not Streamlit's generic
    # dataframe toolbar. The local component mirrors that table and returns
    # only a parcel intent; Python validates the key before doing anything.
    dismissed_rows = dismissed_table_rows(parcels, hidden_keys, price_of, cache)
    with st.container(key="screening_design_table"):
        table_event = ui_components.screening_table(
            screening_table_rows(final, view),
            dismissed=dismissed_rows,
            key="screening_results",
        )
    table_event = ui_components.consume_event(
        table_event, "screening_results"
    )
    hidden_candidates = parcels[
        [parcel_key(row) in hidden_keys for _, row in parcels.iterrows()]
    ]
    valid_actions = pd.concat([final, hidden_candidates], ignore_index=True)
    resolved_event = resolve_table_event(table_event, valid_actions)
    if resolved_event:
        action, key = resolved_event
        if action == "analyse":
            detail.open_parcel(f"{key[0]}:{key[1]}")
            navigation.go_to("Analyse")
        elif action == "save":
            current = workflow_by_key.get(key)
            target = not bool(current is not None and current.saved)
            WF.set_saved([key], target, db)
            st.toast("Auf die Merkliste gesetzt." if target else "Von der Merkliste entfernt.")
        elif action == "restore":
            WF.set_hidden([key], False, db)
            st.toast("Parzelle wiederhergestellt.")
        else:
            WF.set_hidden([key], True, db)
            st.toast("Parzelle als nicht interessant ausgeblendet.")
        st.rerun()

    # Multi-row selection supports saving and dismissing several leads at once. An
    # explicit action opens the single-parcel analysis because a row click can no
    # longer mean both "select this batch" and "navigate away immediately".
    #
    # Streamlit stores selected ROW POSITIONS under the widget key. Filtering or
    # hiding rows changes which parcels those positions mean, so the key includes
    # the ordered cadastral IDs. A changed table gets a fresh selection instead of
    # silently applying an old selection to different leads.
    table_identity = "|".join(
        f"{int(row['bfs'])}:{row['parcel']}" for _, row in final.iterrows()
    )
    hotlist_key = "hotlist_" + hashlib.sha256(table_identity.encode()).hexdigest()[:16]
    native_table = st.container(key="screening_native_table")
    event = native_table.dataframe(
        view,
        key=hotlist_key,
        on_select="rerun",
        selection_mode="multi-row",
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
            "Merkliste": st.column_config.TextColumn(width="small"),
            "Kontaktstatus": st.column_config.TextColumn(width="medium"),
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

    # `final` is what `view` was built from, in the same order, so the position the
    # table reports is the row the user pointed at.
    chosen_rows = list(event.selection["rows"]) if event and event.selection else []
    selected = final.iloc[chosen_rows] if chosen_rows else final.iloc[0:0]
    selected_keys = [parcel_key(row) for _, row in selected.iterrows()]

    native_actions = st.container(key="screening_native_actions")
    selection_col, open_col, save_col, dismiss_col = native_actions.columns(
        [2, 1, 1, 1]
    )
    selection_col.caption(
        f"{len(selected_keys)} Parzelle(n) ausgewählt. Mehrere Zeilen können "
        "gemeinsam gespeichert oder ausgeblendet werden."
    )
    open_selected = open_col.button(
        "Einzelanalyse öffnen",
        disabled=len(selected_keys) != 1,
        width="stretch",
    )
    save_selected = save_col.button(
        "In Merkliste speichern",
        disabled=not selected_keys,
        type="primary",
        width="stretch",
    )
    dismiss_selected = dismiss_col.button(
        "Nicht interessant",
        disabled=not selected_keys,
        width="stretch",
        help="Blendet die ausgewählten Parzellen aus der Ergebnisliste aus.",
    )

    if save_selected:
        WF.set_saved(selected_keys, True, db)
        st.toast(f"{len(selected_keys)} Parzelle(n) gespeichert.")
        st.rerun()
    if dismiss_selected:
        WF.set_hidden(selected_keys, True, db)
        st.toast(f"{len(selected_keys)} Parzelle(n) ausgeblendet.")
        st.rerun()
    if open_selected:
        detail.open_parcel(detail.parcel_id(selected.iloc[0]))
        navigation.go_to("Analyse")
        st.rerun()

    # This operator-only surface predates the supplied design. Keep the
    # functionality available in the DOM for now, but remove its three generic
    # Streamlit expanders from the customer-facing screening layout.
    maintenance = st.container(key="screening_non_design_footer")
    with maintenance.expander("Daten aktualisieren", expanded=False):
        run_col, age_col, note_col = st.columns([1, 1, 4])
        run = run_col.button(
            "▶ Neu berechnen" if _full_run else "▶ ÖREB prüfen",
            type="primary",
            help=(
                f"Rechnet die Filterkaskade für alle {len(runs)} Gemeinden neu und "
                "fragt anschliessend den ÖREB-Kataster für die Shortlist ab."
                if _full_run else
                "Fragt den ÖREB-Kataster für die Shortlist ab. Die Kaskade kann "
                "hier ohne die lokalen Geodaten nicht neu gerechnet werden."
            ),
        )
        min_age = age_col.number_input(
            "Mindestalter (Jahre)",
            min_value=0,
            max_value=100,
            step=1,
            key="screening_min_age",
            disabled=not _full_run,
            help=(
                "Wirkt beim nächsten «Neu berechnen», nicht auf die bereits "
                "angezeigte Liste."
                if _full_run else
                "Die Liste wurde mit 15 Jahren erzeugt."
            ),
            **_initial_widget_value("screening_min_age", value=15),
        )
        retry = int(shortlist["egrid"].isin(failed_egrids(cache)).sum())
        if pending.empty:
            note_col.success(
                f"Shortlist vollständig ÖREB-geprüft ({len(shortlist)} Parzellen)."
            )
        else:
            note_col.info(
                f"{len(shortlist) - len(pending)} von {len(shortlist)} geprüft. "
                + (f"{retry} Abfrage(n) werden erneut versucht. " if retry else "")
                + "Ungeprüfte Parzellen bleiben sichtbar."
            )

    if run:
        import ingest

        full = ingest.geodata_available()
        bar = st.progress(0.0, "Kaskade" if full else "ÖREB")
        if full:
            ingest.recompute(
                progress=lambda f, t: bar.progress(f * 0.2, "Kaskade — " + t),
                built_after=date.today().year - int(min_age),
            )
        else:
            st.info(
                "Geodaten in dieser Umgebung nicht vorhanden — nur der "
                "ÖREB-Kataster wird abgefragt."
            )
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

    if not excluded.empty:
        with maintenance.expander(f"{len(excluded)} Parzellen durch ÖREB ausgeschlossen"):
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

    methodology = maintenance.expander("Datenabdeckung und Methodik", expanded=False)
    methodology.caption(
        f"{len(runs)} von {MUNICIPALITIES_AG} Gemeinden ausgewertet · "
        f"{assessed:,} Parzellen beurteilt · {no_az:,} nicht beurteilbar."
        f"{reason_text} {missing} Gemeinden ohne verwertbare Nutzungsziffer. "
        f"{len(df):,} Treffer → Shortlist {len(shortlist)} → "
        f"{len(excluded)} durch ÖREB ausgeschlossen."
    )
    methodology.caption(
        "Potenzial = Fläche innerhalb der Bauzone × AZ − (Grundfläche × Geschosse) "
        "aller Gebäude auf der Parzelle. Die bestehende Geschossfläche ist aus dem "
        "GWR geschätzt, nicht die anrechenbare Geschossfläche — die Zahl ist eine "
        "Schätzung und eher zu tief als zu hoch. Baujahr ist das des ältesten "
        "Gebäudes auf der Parzelle, AZ der flächengewichtete Wert über alle "
        "berührten Zonen. Eigentümerdaten werden nicht automatisch erhoben; "
        "Namen können nach dem manuellen Nachschlagen in der Merkliste erfasst werden. "
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
    if paths.on_persistent_disk() is False:
        methodology.warning(
            "Kein persistenter Speicher eingebunden — ÖREB-Auszüge, Merkliste und "
            "Kontaktstatus gehen bei jedem Deployment verloren. "
            "(Railway: Volume auf /data mounten.)"
        )
