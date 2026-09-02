"""
The single-parcel analysis view — step 2 of the brief.

The list answers "which parcels are worth a look". This answers the next
question, "is this one worth doing", and it is the first screen in the tool
where the user's own assumptions matter more than the registers: sale price,
construction cost, the margin they work to. So everything the pipeline already
knows is carried over read-only, everything the user decides is an input, and
every intermediate step of the residual calculation is on screen rather than
folded into a total.

No new data is fetched here. Every figure in block A already exists by the time
a parcel appears in the hotlist; asking the cadastre again would only add
latency and a second answer that could differ from the one in the list.

Edits live in `st.session_state` and are keyed by parcel, so switching between
two parcels and back keeps both sets of assumptions for as long as the page is
open. They are deliberately not persisted — the brief calls that a separate
task, and a half-built table of saved analyses is worse than none.
"""
import json
from html import escape

import pandas as pd
import streamlit as st

import economics as E
import regulations as R
import formatting as F
import links as L
import navigation
import report
import paths
import workflow as WF

#: Which parcel the app is showing, or absent for the list view. The whole
#: navigation is this one key: the brief asks for a conditional view rather
#: than a second page, so nothing about the app's structure changes.
SELECTED = "selected_parcel_id"


def parcel_id(row):
    """`bfs:parcel` — the primary key of `parcel_results`, so the selection
    survives a filter change, a recompute, and a different sort order."""
    return f"{int(row['bfs'])}:{row['parcel']}"


def selected():
    return st.session_state.get(SELECTED)


def open_parcel(pid):
    st.session_state[SELECTED] = pid


def close():
    st.session_state.pop(SELECTED, None)


def find(parcels, pid):
    """The row behind a selection, looked up in the full result table rather
    than in the filtered view — otherwise narrowing the filter while a parcel
    is open would leave the detail view with nothing to show."""
    bfs, _, parcel = str(pid).partition(":")
    try:
        hit = parcels[(parcels["bfs"] == int(bfs)) & (parcels["parcel"] == parcel)]
    except ValueError:
        return None
    return None if hit.empty else hit.iloc[0]


def _on_merkliste(db, bfs, parcel):
    """Whether this parcel already has a `saved` row in `parcel_workflow`.

    Queried fresh rather than folded into `parcels`: that table is the
    pipeline's own output, replaced whole on a recompute, and a save made a
    second ago from this same page has to show up on this same page without a
    trip back through Screening or Merkliste first.
    """
    saved = WF.load(db)
    if saved.empty:
        return False
    hit = saved[(saved["bfs"] == int(bfs)) & (saved["parcel"] == str(parcel))]
    return bool(hit["saved"].astype(bool).any())


def _facts(rows):
    """A label/value list. A markdown table rather than `st.table`, because this
    is a data sheet: no index column, no sort arrows, no horizontal scrollbar."""
    def cell(value):
        # A pipe in a value would end the column early and shift every field
        # after it into the wrong row.
        return str(value).replace("|", "\\|")

    body = "\n".join(f"| {label} | {cell(value)} |" for label, value in rows)
    return f"| | |\n|---|---|\n{body}"


def _text(value):
    """Empty string for anything absent. Pandas 3 infers text columns as its own
    string dtype and turns None into NaN, which is truthy — `value or ""` yields
    the NaN and prints it into the sentence."""
    return "" if value is None or pd.isna(value) else str(value)


def extract_of(row, cache):
    """The rest of the ÖREB answer for this parcel, or None if it was never
    fetched. Stored as JSON when the shortlist was checked, so reading it costs
    nothing and cannot disagree with the restriction line above it — both come
    out of the same request."""
    egrid = _text(row.get("egrid"))
    if not egrid or egrid not in cache.index or "details" not in cache.columns:
        return None
    blob = _text(cache.loc[egrid, "details"])
    if not blob:
        return None
    try:
        return json.loads(blob)
    except ValueError:
        return None


def _document_line(doc):
    """`Bau- und Nutzungsordnung [4195]` plus its files. A plan and its annexes
    arrive as one title with several attachments, exactly as the printed extract
    stacks them."""
    head = doc["title"]
    if doc.get("abbr") and doc["abbr"] not in head:
        head += f" ({doc['abbr']})"
    if doc.get("number"):
        head += f" · {doc['number']}"
    links = " · ".join(
        f"[Dokument{'' if len(doc['urls']) == 1 else f' {i}'}]({u})"
        for i, u in enumerate(doc.get("urls") or [], 1)
    )
    return f"{head} — {links}" if links else head


def _oereb_text(row, cache):
    """What the cadastre says about this parcel, or that it has not been asked.

    Never silently blank: an empty ÖREB line reads as "nothing on this parcel",
    which is the opposite of "not checked", and the two lead to different
    decisions."""
    egrid = _text(row.get("egrid"))
    if not egrid:
        return "kein EGRID — nicht abfragbar"
    if egrid not in cache.index:
        return "noch nicht abgefragt (nur die Shortlist wird geprüft)"
    hard = _text(cache.loc[egrid, "hard"])
    notable = _text(cache.loc[egrid, "notable"])
    error = _text(cache.loc[egrid, "error"])
    if error:
        return f"Abfrage fehlgeschlagen: {error}"
    parts = []
    if hard:
        parts.append(f"**Harte Beschränkung:** {hard}")
    if notable:
        parts.append(f"Vermerk: {notable}")
    return " · ".join(parts) if parts else "keine Eigentumsbeschränkung im Kataster"


def _zone_rows(extract):
    """The extract's own "Legende beteiligter Objekte": every plan object that
    touches the parcel, with its area and share, in the cadastre's order and
    wording. A table of its own rather than a line in block A — a parcel can
    carry eight of these, and joined into one cell they read as a paragraph
    instead of a list you can compare against the tool's own zone pick."""
    rows = []
    for zone in extract.get("zones") or []:
        if zone.get("area") is None:
            share = "—"          # a building line has a length, not an area
        else:
            share = f"{E.chf(zone['area'])} m²"
            if zone.get("percent") is not None:
                share += f" ({zone['percent']:.1f}%)"
        rows.append((zone["text"], share))
    return rows


def _base_block(row, cache, price_ref, extract=None):
    """Block A — everything the pipeline already computed, unchanged."""
    metric = F.METRIC_LABELS.get(row["metric"], "Ausnützungsziffer")
    heritage = _text(row.get("heritage"))
    rows = [
        ("Adresse", _text(row.get("address")) or "— (keine GWR-Adresse)"),
        ("Gemeinde", f"{row['municipality']} (BFS {int(row['bfs'])})"),
        ("Parzelle", row["parcel"]),
        ("EGRID", _text(row.get("egrid")) or "—"),
        ("Zone", _text(row.get("zone")) or "—"),
        (metric, f"{row['az']:.3f}".rstrip("0").rstrip(".")),
        ("Parzellenfläche", f"{row['area']:,.0f} m²".replace(",", "’")),
        (
            "davon in der Bauzone",
            f"{row['buildable']:,.0f} m² ({row['zone_share'] * 100:.0f}%)".replace(",", "’"),
        ),
        ("Bebauung", "unbebaut (kein stehendes GWR-Gebäude)"
            if row["buildings"] == 0 else f"{int(row['buildings'])} Gebäude"),
        ("Baujahr", F.short_year(_text(row.get("built")))),
        ("Nutzung", F.short_use(_text(row.get("use_class")))),
        (
            "Bestehende Geschossfläche",
            f"{row['existing']:,.0f} m² (GWR-Schätzung: Grundfläche × Geschosse)".replace(",", "’"),
        ),
        ("Denkmal-/Inventarstatus", heritage or "keine Eintragung in den kantonalen Registern"),
        ("Gestaltungsplan", "ja — die Ziffer der Grundzone ist evtl. überlagert"
            if row.get("design_plan") else "keiner"),
        ("ÖREB-Kataster", _oereb_text(row, cache)),
    ]
    if extract:
        registry = extract.get("land_registry_area")
        if registry:
            # Two numbers that should agree, both shown. The tool measures the
            # parcel from the cadastral geometry; this is what the land registry
            # writes down. A difference is not fatal but it changes how much the
            # potential is worth trusting, so it is stated rather than hidden.
            gap = abs(float(registry) - float(row["area"]))
            note = "" if gap <= 1 else f" — Abweichung zur berechneten Fläche: {E.chf(gap)} m²"
            rows.append(("Grundbuchfläche (ÖREB)", f"{E.chf(registry)} m²{note}"))
        office = extract.get("office") or {}
        if office.get("name"):
            rows.append((
                "Zuständige Stelle",
                f"[{office['name']}]({office['url']})" if office.get("url") else office["name"],
            ))
    if price_ref:
        rows.append((
            "Landpreis-Referenz",
            f"CHF {E.chf(price_ref.price_chf_m2)}/m² · Ebene {price_ref.scope} · "
            f"Stand {price_ref.as_of}",
        ))
    else:
        rows.append(("Landpreis-Referenz", "keine Referenz hinterlegt"))
    return rows


def _edict_rows(edicts):
    """One row per regulation: when it came into force, and which municipality's
    it is. The date leads because the list is a chronology — what changed most
    recently is the question the panel exists to answer."""
    rows = []
    for e in edicts:
        what = f"{e.municipality} · {e.label}"
        if e.document:
            what += f" — [Dokument]({e.document})"
        rows.append((e.when, what))
    return rows


@st.fragment(run_every=1)
def _regulation_news_poll():
    """Block E's placeholder while the canton-wide fetch is still in flight.

    A fragment reruns on its own schedule, independent of the rest of the
    page — that is what lets the panel fill in without the user clicking
    anything. `page` only ever calls this function while the fetch is not
    done, so once it is, escalating to a full `st.rerun()` (rather than
    rendering the result here, which this fragment has no access to) is also
    what stops the polling: the next full run takes `page`'s direct branch
    instead, and this fragment is never invoked again — a fragment `page`
    does not draw on a given run keeps no schedule of its own.
    """
    status, result = R.news_state()
    if status != "done":
        # Only when there is nothing to show. During a twelve-hour refresh the
        # previous list is on screen above this, and announcing a request the
        # reader did not make under a list they can already read is noise.
        if result is None:
            st.caption("Wird geladen …")
        return
    st.rerun()


def _regulation_block(own, own_note, news_error):
    """The one part of block E that belongs on paper.

    Only the parcel's own regulation: the canton-wide change list is news rather
    than a fact about this parcel, and 227 rows would bury a one-page data sheet.
    A printed analysis that does not say which edition of the building
    regulation it assumed cannot be checked a year later, which is why the
    not-found and could-not-ask cases print a line of their own instead of the
    block quietly disappearing.
    """
    if news_error:
        return ("Stand der Rechtsvorschrift",
                [("Nicht abrufbar", f"OEREBlex antwortete nicht: {news_error}")])
    if not own:
        return ("Stand der Rechtsvorschrift",
                [("Rechtsvorschrift", "in OEREBlex keine gültige Vorschrift für "
                                      "diese Gemeinde verzeichnet")])
    rows = [(own.label or "Rechtsvorschrift",
             " ".join(filter(None, [f"in Kraft seit {own.when}", own.document])))]
    if own_note:
        rows.append(("Hinweis", own_note))
    return ("Stand der Rechtsvorschrift", rows)


def _links(row):
    parts = [
        f"[AGIS-Karte]({L.agis_link(row['e'], row['n'])})",
        f"[Google Maps]({L.google_map_link(row['e'], row['n'])})",
        f"[Street View]({L.google_street_view_link(row.get('sv_e'), row.get('sv_n'), row['e'], row['n'])})",
    ]
    if _text(row.get("egrid")):
        parts.insert(
            1,
            f"[ÖREB-Auszug (PDF)](https://api.geo.ag.ch/v2/oereb/extract/pdf/?EGRID={row['egrid']})",
        )
    return " · ".join(parts)


#: Where the user's own assumptions live between reruns, as {parcel: {name:
#: value}}. Not the widget keys themselves: Streamlit discards the state of a
#: widget that a rerun did not draw, so every edit was lost the moment the back
#: link swapped the detail view for the list — the one place the brief does ask
#: for the values to survive. A plain session key is not garbage-collected, so
#: the widgets are re-created from it.
#: Two stores, because two different things are being remembered. What the
#: parcel is — its floor-area potential, whether the building comes down — is
#: the parcel's. What it is worth building for — sale price, construction cost,
#: the margin — is the user's, and does not change because they clicked a
#: different row. Keeping the second per parcel would mean retyping seven
#: numbers on every lead, which is the opposite of letting someone work with
#: their own figures.
STORE = "parcel_assumptions"   # {parcel: {name: value}}
OWN_STORE = "own_assumptions"  # {name: value}, for the whole session

OWN = ("unit", "sale", "share", "build", "ancillary", "demolition", "financing",
       "reserve")


def _shared(name):
    return name in OWN


def _widget_key(pid, name):
    """A shared assumption keeps one widget across parcels, so Streamlit itself
    carries the value rather than the app restoring it from a default."""
    return f"own::{name}" if _shared(name) else f"{pid}::{name}"


def _recall(pid, name, default):
    if _shared(name):
        return st.session_state.get(OWN_STORE, {}).get(name, default)
    return st.session_state.get(STORE, {}).get(pid, {}).get(name, default)


def _remember(pid, name, value):
    if _shared(name):
        st.session_state.setdefault(OWN_STORE, {})[name] = value
    else:
        st.session_state.setdefault(STORE, {}).setdefault(pid, {})[name] = value
    return value


def forget(pid):
    """Back to the published benchmarks. The economic assumptions were set for
    the session rather than for this parcel, so they are cleared in the same
    scope they were set in."""
    st.session_state.pop(OWN_STORE, None)
    st.session_state.get(STORE, {}).pop(pid, None)
    for key in [k for k in st.session_state
                if str(k).startswith(f"{pid}::") or str(k).startswith("own::")]:
        del st.session_state[key]


#: Two things the layout has to say that the words on the page cannot.
#:
#: The result sits above the split rather than under the calculation, because
#: the interaction on this screen is changing an assumption and reading the new
#: number, and a total at the foot of a long form makes that cost a scroll each
#: way. It was pinned there for a while. Philipp asked for that in the first
#: place and then asked for it back out once he had used it — a 140px bar held
#: over the page is a lot of it. Measured before removing: the bar and every
#: input together span 781px, so on any window taller than that the number is
#: still on screen while the fields under it are edited. It only leaves on a
#: short window, which is the case the pinning was worth least in anyway.
#:
#: And the fields the user may change sit in a tinted panel with an accent
#: edge, while block A — which comes from the registers and cannot be
#: overridden — does not. Without it the two halves of the screen look alike,
#: and "where can I intervene" has to be answered by clicking.
#:
#: NOTHING HERE NAMES A BACKGROUND COLOUR, and that is the whole point. An
#: earlier version painted the bar white and swapped to #0e1117 under
#: `prefers-color-scheme: dark`, on the assumption that the OS preference is
#: what Streamlit renders. It is not: the theme can be switched in Streamlit's
#: own Appearance menu, and then a light page carried a near-black bar with the
#: light theme's dark text on it — unreadable, and shipped. There is no
#: server-side signal to fix it with either: `st.context.theme` reports the OS
#: preference, exactly like the media query, and `st.get_option("theme.base")`
#: is None unless someone has pinned a theme in config.
#:
#: So the bar derives its surface instead of declaring it. `backdrop-filter`
#: takes whatever is behind it and `brightness` lifts it: over a white page it
#: clips at white, over a dark one it raises a little — both read as something
#: sitting above the page, from one unconditional rule. The neutral tint is only
#: 6%, enough to hold an edge; at the 17% it started on, a white page carried a
#: visibly grey box (#e5e5e6 against #ffffff — measured, and Philipp saw it).
#: The text on top stays Streamlit's own, so it is legible against its own
#: background whichever theme is running.
PAGE_CSS = """
<style>
  .st-key-detail_breadcrumb { margin:4px 0 14px; align-items:center; gap:8px; }
  .st-key-detail_breadcrumb [data-testid="stButton"] button { min-height:0;
      height:auto; padding:0; border:0; background:transparent; color:#1c4e4a;
      font-size:11.5px; }
  .detail-breadcrumb-copy { color:#9a9aa6; font-size:11.5px; }
  .detail-breadcrumb-copy strong { color:#4a4a54; font-weight:400; }
  .st-key-detail_header { margin:0 0 20px; }
  .st-key-detail_header [data-testid="stHeadingWithActionElements"] h1 {
      margin:0; font-size:21px; line-height:1.25; font-weight:600;
      letter-spacing:-.015em; }
  .st-key-detail_header [data-testid="stCaptionContainer"] { margin-top:4px; }
  .st-key-detail_actions [data-testid="stHorizontalBlock"] { align-items:end; }
  .st-key-detail_actions button { white-space:nowrap; }

  .st-key-result_bar { padding:20px 24px 14px; margin-bottom:24px;
      border-radius:10px; border:1px solid #dde9e7; background:#fbfdfd; }
  .st-key-result_bar [data-testid="stMetricLabel"] p { color:#8a8a94;
      font-size:10px; font-weight:600; letter-spacing:.1em; text-transform:uppercase; }
  .st-key-result_bar [data-testid="stMetricValue"] { font-family:"IBM Plex Mono",
      monospace; font-size:22px; font-weight:600; }
  .st-key-result_bar [data-testid="stColumn"]:first-child [data-testid="stMetricValue"] {
      font-size:38px; letter-spacing:-.02em; }
  .st-key-result_bar [data-testid="stColumn"] + [data-testid="stColumn"] {
      border-left:1px solid #eaeaee; padding-left:22px; }

  .st-key-facts_a, .st-key-inputs_b, .st-key-inputs_c {
      padding:14px 16px 12px; margin-bottom:20px; border:1px solid #eaeaee;
      border-radius:9px; background:#fff; }
  .st-key-inputs_c { margin-top:0; padding:14px 16px 4px; }
  .st-key-facts_a [data-testid="stHeadingWithActionElements"] h3,
  .st-key-inputs_b [data-testid="stHeadingWithActionElements"] h3,
  .st-key-inputs_c [data-testid="stHeadingWithActionElements"] h3 {
      color:#8a8a94; font-size:10px; font-weight:600; letter-spacing:.1em;
      text-transform:uppercase; }
  .detail-edit-pill { display:inline-flex; align-items:center; padding:3px 9px;
      border-radius:20px; background:#e8f0ef; color:#143a37; font-size:10.5px; }
  .detail-edit-pill.readonly { background:#f4f4f6; color:#8a8a94; }
  .st-key-inputs_b [data-testid="stNumberInput"] input,
  .st-key-inputs_c [data-testid="stNumberInput"] input { font-family:"IBM Plex Mono",
      monospace; font-size:13px; text-align:right; }
  .st-key-inputs_b [data-testid="stMetricValue"] { font-family:"IBM Plex Mono",
      monospace; font-size:26px; font-weight:600; }

  /* The data sheet has to stay inside its half. A markdown table sizes itself
     to its content, so from about 1400px down block A ran straight over block
     B — an EGRID and a zone name are wide and neither wraps on its own. */
  /* Streamlit gives every markdown table cell a border on all four sides, and
     an earlier pass only ever overrode the bottom one — so the data sheet grew
     column separators and an outer box that the design never had. A data sheet
     is a list of rows, not a grid. */
  /* A markdown table is not a table without a header row, so `_facts` emits an
     empty one — and Streamlit renders it: 13px of nothing with a rule above and
     a rule below, which is the doubled line at the top of every data sheet
     here. None of these tables has a header worth showing; they are label/value
     lists. Do not delete this without giving `_facts` a real header. */
  [class*="st-key-facts_"] table thead { display:none; }

  [class*="st-key-facts_"] table { width:100%; }
  [class*="st-key-facts_"] table td, [class*="st-key-facts_"] table th {
      border-left:0; border-right:0; }
  [class*="st-key-facts_"] td, [class*="st-key-facts_"] th {
      overflow-wrap:break-word; }
  [class*="st-key-facts_"] [data-testid="stMarkdownContainer"] {
      overflow-x:auto; }

  .st-key-inputs_c .calc__scroll { margin:14px -16px 0; }
  .st-key-inputs_c table.calc { width:100%; min-width:760px; }
  .st-key-inputs_c table.calc th, .st-key-inputs_c table.calc td {
      padding:9px 16px; border-bottom:1px solid #f4f4f7; }
  .st-key-inputs_c table.calc thead th { background:#fafafb; }
  .st-key-inputs_c table.calc tr.calc__result th,
  .st-key-inputs_c table.calc tr.calc__result td { background:#fafafb;
      border-top:1px solid #e4e4ea; }

  /* Below about a thousand pixels the halves are too narrow for
     "Denkmal-/Inventarstatus" to fit on any line, and a word that cannot fit
     takes the table over the seam with it. Hard breaking only there — on a
     desktop it would split ordinary words mid-syllable for nothing. */
  @media (max-width: 1000px) {
    [class*="st-key-facts_"] td, [class*="st-key-facts_"] th {
        overflow-wrap:anywhere; }
  }
  @media (max-width: 760px) {
    .st-key-detail_header [data-testid="stHorizontalBlock"] { flex-wrap:wrap; }
    .st-key-detail_header [data-testid="stColumn"] { min-width:100%; }
    .st-key-detail_actions [data-testid="stColumn"] { min-width:0; }
    .st-key-result_bar [data-testid="stHorizontalBlock"] { flex-wrap:wrap; }
    .st-key-result_bar [data-testid="stColumn"] { min-width:100%; border-left:0 !important;
        padding-left:0 !important; }
  }
</style>
"""

CALC_CSS = """
<style>
  /* Its own scroller: in the right-hand column the three columns of the table
     no longer always fit, and a page that scrolls sideways is worse than a
     table that does. */
  div.calc__scroll { overflow-x:auto; margin:.4rem 0 1rem; }
  table.calc { border-collapse:collapse; width:auto; min-width:min(680px,100%);
      margin:0; }
  table.calc th, table.calc td { text-align:left; padding:.42rem .9rem .42rem 0;
      border-top:0; border-left:0; border-right:0;
      border-bottom:1px solid rgba(128,128,128,.28); font-weight:400; }
  table.calc thead th { font-weight:600; font-size:.86em; letter-spacing:.02em;
      text-transform:uppercase; opacity:.65; }
  table.calc td.calc__amount { text-align:right; padding-right:0;
      font-variant-numeric:tabular-nums; white-space:nowrap; }
  table.calc td.calc__formula { opacity:.75; }
  table.calc tr.calc__result th, table.calc tr.calc__result td { font-weight:700;
      border-top:2px solid currentColor; border-bottom:none; }

  /* The hover box. Its own element rather than a `title` attribute: the native
     tooltip waits about a second, strips the line breaks that make the formula
     readable, and cannot be styled to look like the code it is showing. */
  span.calc__name { position:relative; border-bottom:1px dotted rgba(128,128,128,.75);
      cursor:help; outline-offset:3px; }
  span.calc__tip { position:absolute; left:0; top:calc(100% + .45rem); z-index:9999;
      display:none; white-space:pre; padding:.6rem .75rem; border-radius:4px;
      font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;
      font-size:12.5px; line-height:1.5; letter-spacing:0; font-weight:400;
      background:#101014; color:#e9e6df; border:1px solid rgba(255,255,255,.18);
      box-shadow:0 8px 24px rgba(0,0,0,.45); }
  span.calc__name:hover span.calc__tip,
  span.calc__name:focus span.calc__tip,
  span.calc__name:focus-within span.calc__tip { display:block; }
  span.calc__tip b { color:#ff8a7a; font-weight:600; }
</style>
"""


def _tooltip(step):
    """What the hover box says: the expression as it is written in the code, the
    same expression with the numbers filled in, and the result. Read off the rule
    that computed the number, never typed out a second time — the point Philipp
    asked for is that changing the formula changes this text with it."""
    amount = (f"{E.chf(step.value)} m²" if step.unit == "m²"
              else f"CHF {E.chf(step.value)}")
    return (
        f"<b>{escape(step.label.lstrip('−= ').strip())}</b>\n"
        f"  = {escape(step.expr)}\n"
        f"  = {escape(step.formula)}\n"
        f"  = {escape(amount)}"
    )


def _calculation_table(steps):
    """An HTML table rather than markdown, because a markdown cell cannot carry
    a hover box and the formula has to sit on the name it explains."""
    rows = []
    for step in steps:
        amount = (f"{E.chf(step.value)} m²" if step.unit == "m²"
                  else f"CHF {E.chf(step.value)}")
        rows.append(
            f'<tr class="{"calc__result" if step.kind == "result" else ""}">'
            f'<th scope="row"><span class="calc__name" tabindex="0">'
            f'{escape(step.label)}'
            f'<span class="calc__tip">{_tooltip(step)}</span></span></th>'
            f'<td class="calc__formula">{escape(step.formula)}</td>'
            f'<td class="calc__amount">{escape(amount)}</td></tr>'
        )
    body = "".join(rows)
    return (CALC_CSS + '<div class="calc__scroll"><table class="calc"><thead><tr>'
            '<th>Schritt</th><th>Rechnung</th>'
            '<th class="calc__amount">Betrag</th></tr></thead><tbody>'
            + body + "</tbody></table></div>")


def _number(container, label, pid, name, default, *, step, minimum=0.0,
            maximum=None, fmt="%.0f", help=None):
    value = container.number_input(
        label,
        min_value=minimum,
        max_value=maximum,
        value=float(_recall(pid, name, default)),
        step=step,
        format=fmt,
        key=_widget_key(pid, name),
        help=help,
    )
    return _remember(pid, name, value)


#: Which symbol in the formulas each control sets. The help text is then built
#: from the formulas themselves — nothing to keep in sync by hand.
SYMBOL = {
    "gf": "potenzial_gf",
    "share": "verkaufsflaechenanteil",
    "sale": "verkaufspreis",
    "build": "baukosten_pro_m2",
    "ancillary": "baunebenkosten_prozent",
    "demolition": "abbruchkosten_pro_m2",
    "financing": "finanzierung_prozent",
    "reserve": "reserve_prozent",
}


def _in_formula(name):
    symbol = SYMBOL.get(name)
    if not symbol:
        return ""
    steps = E.used_in(symbol)
    where = ", ".join(steps) if steps else "keiner Formel"
    return f" · In den Formeln {{{symbol}}} — wirkt auf {where}."


def _benchmark_help(key, extra="", name=""):
    mark = E.BENCHMARKS[key]
    text = f"Vorgabewert {E.chf(mark.value)} {mark.unit}. {mark.provenance}"
    return f"{text} {extra}".strip() + _in_formula(name)


def _assumption_notes(used):
    """What each figure in the document rests on, and whether it is still the
    published benchmark or the user's own number. The distinction is the whole
    point of citing sources: an overridden value carries Philipp's authority,
    the default carries a website's."""
    notes = []
    for key, value, unit in used:
        mark = E.BENCHMARKS[key]
        if abs(value - mark.value) < 1e-9:
            notes.append(f"{unit}: {E.chf(value)} — {mark.provenance}")
        else:
            notes.append(
                f"{unit}: {E.chf(value)} — vom Nutzer angepasst "
                f"(Vorgabewert war {E.chf(mark.value)}: {mark.source})"
            )
    return notes


#: The caveat that belongs with the number rather than at the foot
#: of the page: read without it, the residual value looks like a valuation of
#: the parcel, which is the one thing it is not. The short form rides in the
#: result bar; the full one sits directly under the calculation it qualifies.
RESULT_CAVEAT = (
    "Bewertet nur das zusätzliche Potenzial — nicht die Parzelle und nicht "
    "das bestehende Gebäude."
)

#: The same line when the number comes out negative. One line either way, so
#: bar keeps its height: a warning box here added 72px to it, and did it in
#: exactly the case where the inputs underneath most need the room.
NEGATIVE_CAVEAT = (
    ":red[**Negativer Residualwert** — das zusätzliche Potenzial trägt die "
    "Erstellungskosten nicht.] Bewertet ist nur dieses Potenzial, nicht die "
    "Parzelle und nicht das bestehende Gebäude."
)

DISCLAIMER = (
    "Der Residualwert bewertet nur das zusätzliche Potenzial, nicht die "
    "Parzelle: der Wert des bestehenden Gebäudes ist darin nicht enthalten. "
    "Erschliessung, Baugrund, Lärmschutz, Auflagen aus einem Gestaltungsplan "
    "und die tatsächliche anrechenbare Geschossfläche sind hier nicht "
    "gerechnet. Die eigenen Annahmen aus Block C gelten für alle Parzellen "
    "dieser Sitzung — einmal eingetragen, nicht pro Parzelle wieder. "
    "Potenzial und Abbruch gehören zur Parzelle und bleiben dort."
)


def page(parcels, cache, price_of, db=None):
    """Render the detail view for the selected parcel.

    `price_of` is a callable returning the land-price reference for a row, so
    this module does not need to know how that lookup is configured. Block E's
    canton-wide edict list is a different kind of dependency: it is fetched in
    the background (see `regulations.ensure_news_started`) rather than passed
    in, because the whole point of that block is that it must not make this
    function wait on it.

    `db` is the workflow database for the Merkliste action below and defaults
    to `paths.DB`, resolved inside this function rather than at import.
    `screening.py` learned this the hard way: a module-level `DB = paths.DB`
    freezes at first import and ignores every database a caller — a test, a
    per-request override — points `paths.DB` at afterwards. `app.py`'s router
    does not pass `db` yet; once it does, the default here just falls away.

    Two columns, because the two halves of this screen are read against each
    other: what the registers say about the parcel is fixed and stands on the
    left, what the user assumes about it is edited on the right, and neither
    has to be scrolled away to consult the other. The result rides above both
    in a strip that stays put — the interaction here is changing an assumption
    and reading the new number, and a total at the foot of a long form makes
    that cost a scroll down and a scroll back every time.
    """
    pid = selected()
    row = find(parcels, pid)

    if row is None:
        if st.button("← Zurück zur Liste"):
            close()
            navigation.go_back()
            st.rerun()
        st.warning(
            f"Parzelle {pid} steht nicht mehr in der Ergebnistabelle — "
            "vermutlich wurde inzwischen neu gerechnet."
        )
        st.stop()

    # Merkliste, in the header action group — the other thing done to the page
    # as a whole, next to leaving it and exporting it. Reading a parcel's
    # analysis and deciding it is worth pursuing used to mean navigating back
    # to Screening, finding the row again, and ticking it there; this is that
    # decision made where it is actually made. The label carries the current
    # state rather than always reading "Auf Merkliste" — a button that offers
    # to add something already added is a lie the user finds out about by
    # clicking it.
    key = (int(row["bfs"]), row["parcel"])
    db_path = db if db is not None else paths.DB
    st.markdown(PAGE_CSS, unsafe_allow_html=True)

    address = _text(row.get("address")) or f"Parzelle {row['parcel']}"
    with st.container(key="detail_breadcrumb", horizontal=True):
        if st.button("← Screening", key="detail_back"):
            close()
            navigation.go_back()
            st.rerun()
        st.html(
            '<span class="detail-breadcrumb-copy">/ '
            f'{escape(str(row["municipality"]))} / '
            f'<strong>Parzelle {escape(str(row["parcel"]))}</strong></span>'
        )

    with st.container(key="detail_header"):
        heading, action_column = st.columns([5, 3], vertical_alignment="bottom")
        with heading:
            st.title(address)
            st.caption(
                f"{row['municipality']} · Parzelle {row['parcel']} · "
                f"{_text(row.get('zone')) or 'ohne Zone'}"
            )
        with action_column.container(key="detail_actions"):
            saved_action, pdf_action = st.columns([1.6, 1.2])
            if _on_merkliste(db_path, *key):
                if saved_action.button(
                    "✓ Merkliste — entfernen", width="stretch"
                ):
                    WF.set_saved([key], False, db_path)
                    st.toast("Von der Merkliste entfernt.")
                    st.rerun()
            elif saved_action.button(
                "Auf Merkliste", width="stretch", type="primary"
            ):
                WF.set_saved([key], True, db_path)
                st.toast("Auf die Merkliste gesetzt.")
                st.rerun()

    price_ref = price_of(row)
    extract = extract_of(row, cache)

    # Claimed here and written at the end: the result needs the inputs below to
    # exist before it can be computed, but it belongs above them on the page.
    result_bar = st.container(key="result_bar")

    facts, potential_panel = st.columns(2, gap="medium")

    # ── Block A ─────────────────────────────────────────────────────────────
    with facts.container(key="facts_a"):
        fact_title, fact_badge = st.columns([3, 1], vertical_alignment="center")
        fact_title.subheader("A · Grunddaten")
        fact_badge.html(
            '<span class="detail-edit-pill readonly">Nicht editierbar</span>'
        )
        st.caption("Aus den Registern übernommen — hier ist nichts veränderbar.")
        st.markdown(_facts(_base_block(row, cache, price_ref, extract)))
        st.markdown(_links(row))
        zone_rows = _zone_rows(extract) if extract else []
        if zone_rows:
            st.markdown("**Legende beteiligter Objekte** (ÖREB-Auszug)")
            st.markdown(_facts(zone_rows))

    # ── Block B ─────────────────────────────────────────────────────────────
    with potential_panel.container(key="inputs_b"):
        potential_title, potential_badge = st.columns(
            [3, 1], vertical_alignment="center"
        )
        potential_title.subheader("B · Potenzial")
        potential_badge.html('<span class="detail-edit-pill">Editierbar</span>')
        b1, b2 = st.columns(2)
        potential = _number(
            b1, "Potenzial (m² GF)", pid, "gf", float(row["delta"]), step=10.0,
            help=(
                "Vorbelegt mit dem berechneten Wert: Fläche in der Bauzone × Ziffer "
                "− geschätzte bestehende Geschossfläche. Überschreibbar, sobald eine "
                "eigene Flächenberechnung vorliegt."
            ),
        )
        unit_size = _number(
            b2, "Wohnungsgrösse (m²)", pid, "unit", float(E.SQM_PER_UNIT), step=5.0,
            minimum=10.0,
            help="Faustregel aus dem Auftrag, keine Planungsgrösse.",
        )
        possible = E.units(potential, unit_size)
        st.divider()
        st.metric(
            "Mögliche Wohnungen",
            "—" if possible is None else f"{possible:.1f}",
            help="Potenzial ÷ Wohnungsgrösse. Rechnerisch, ohne Grundriss.",
        )

    # ── Block C ─────────────────────────────────────────────────────────────
    calculation_panel = st.container(key="inputs_c")
    with calculation_panel:
        calculation_title, calculation_badge = st.columns(
            [4, 1], vertical_alignment="center"
        )
        calculation_title.subheader("C · Residualwertrechnung")
        calculation_badge.html('<span class="detail-edit-pill">Editierbar</span>')
        st.caption(
            "Landwert = Verkaufserlös der neuen Flächen − Baukosten − Baunebenkosten "
            "− Abbruch − Finanzierung − Reserve. Der Verkaufspreis rechnet auf 80% "
            "der Geschossfläche, die Baukosten auf 100%. Mit der Maus über einen "
            "Schritt fahren zeigt die Formel dahinter."
        )
        c1, c2, c3, c4 = st.columns(4)
        sale_price = _number(
            c1, "Verkaufspreis (CHF/m²)", pid, "sale",
            E.BENCHMARKS["sale_price_chf_m2"].value, step=100.0,
            help=_benchmark_help("sale_price_chf_m2", name="sale"),
        )
        sale_share = _number(
            c2, "Verkaufsflächenanteil (%)", pid, "share",
            E.BENCHMARKS["sale_area_pct"].value, step=1.0, minimum=10.0, maximum=100.0,
            help=_benchmark_help(
                "sale_area_pct",
                "Wirkt nur auf den Erlös; die Baukosten rechnen auf der ganzen "
                "Geschossfläche.",
                name="share",
            ),
        )
        construction = _number(
            c3, "Baukosten (CHF/m²)", pid, "build",
            E.BENCHMARKS["construction_chf_m2"].value, step=50.0,
            help=_benchmark_help("construction_chf_m2", name="build"),
        )
        ancillary = _number(
            c4, "Baunebenkosten (%)", pid, "ancillary",
            E.BENCHMARKS["ancillary_pct"].value, step=1.0, maximum=100.0,
            help=_benchmark_help("ancillary_pct", name="ancillary"),
        )

        d1, d2, d3, d4 = st.columns(4, vertical_alignment="bottom")
        has_building = bool(row["buildings"]) and row["existing"] > 0
        demolition = _number(
            d1, "Abbruchkosten (CHF/m²)", pid, "demolition",
            E.BENCHMARKS["demolition_chf_m2"].value, step=10.0,
            help=_benchmark_help("demolition_chf_m2", name="demolition"),
        )
        financing = _number(
            d2, "Finanzierung (%)", pid, "financing",
            E.BENCHMARKS["financing_pct"].value, step=0.5, maximum=100.0, fmt="%.1f",
            help=_benchmark_help("financing_pct", name="financing"),
        )
        reserve = _number(
            d3, "Reserve / Unvorhergesehenes (%)", pid, "reserve",
            E.BENCHMARKS["reserve_pct"].value, step=1.0, maximum=100.0,
            help=_benchmark_help("reserve_pct", name="reserve"),
        )
        demolish = _remember(pid, "demolish", d4.checkbox(
            "Bestehendes Gebäude abbrechen",
            value=bool(_recall(pid, "demolish", has_building)),
            key=_widget_key(pid, "demolish"),
            disabled=not has_building,
            help=(
                "Aus, wenn aufgestockt oder angebaut statt ersetzt wird."
                if has_building else
                "Auf dieser Parzelle steht kein Gebäude, das abgebrochen werden müsste."
            ),
        ))

    steps = E.residual(
        potential_gf=potential,
        sale_area_pct=sale_share,
        sale_price_chf_m2=sale_price,
        construction_chf_m2=construction,
        ancillary_pct=ancillary,
        existing_gf=float(row["existing"]),
        demolition_chf_m2=demolition,
        financing_pct=financing,
        reserve_pct=reserve,
        demolish=bool(demolish),
    )
    land = E.land_value(steps)
    per_m2 = E.per_square_metre(steps, float(row["area"]))

    used = [
        ("sale_price_chf_m2", sale_price, "Verkaufspreis CHF/m²"),
        ("construction_chf_m2", construction, "Baukosten CHF/m²"),
        ("sale_area_pct", sale_share, "Verkaufsflächenanteil %"),
        ("ancillary_pct", ancillary, "Baunebenkosten %"),
        ("demolition_chf_m2", demolition, "Abbruchkosten CHF/m²"),
        ("financing_pct", financing, "Finanzierung %"),
        ("reserve_pct", reserve, "Reserve / Unvorhergesehenes %"),
    ]
    notes = _assumption_notes(used)
    # ── The calculation ─────────────────────────────────────────────────────
    # Below both columns, on the full width, because it does not fit in half of
    # one: the longest line of it is about seventy characters of arithmetic, and
    # in the right-hand column that wrapped the step names and pushed the table
    # into its own sideways scroll. The split is for the two things read against
    # each other — the registers and the assumptions; the calculation is read on
    # its own, and it is the one thing on this page that genuinely wants width.
    #
    # It costs little to put it here: what the eye follows while a number is
    # being changed is the total, and the total is at the top of the page — the
    # bar and every input together span 781px, so both are on screen at once on
    # any window taller than that. Every line, not just that total, because an
    # assumption cannot be adjusted if only the result is visible. Hovering a
    # step name shows the expression behind it, symbols and all — read off the
    # rule that computed the number, so the two cannot drift apart.
    calculation_panel.markdown(
        _calculation_table(steps), unsafe_allow_html=True
    )
    calculation_panel.caption(DISCLAIMER)
    with calculation_panel.expander("Annahmen und Quellen"):
        for note in notes:
            st.markdown(f"- {note}")
        if st.button("Annahmen zurücksetzen"):
            forget(pid)
            st.rerun()

    # ── The result bar ──────────────────────────────────────────────────────
    # Written last, drawn first. The warning belongs here rather than beside the
    # table: it explains the number, and this is where the number is read.
    with result_bar:
        r1, r2, r3 = st.columns([1.35, 1, 1])
        r1.metric("Residualer Landwert", f"CHF {E.chf(land)}")
        r2.metric(
            "pro m² Parzelle",
            "—" if per_m2 is None else f"CHF {E.chf(per_m2)}",
            help="Direkt vergleichbar mit der Landpreis-Referenz in der Liste.",
        )
        if price_ref:
            r3.metric(
                f"Referenz {price_ref.scope}",
                f"CHF {E.chf(price_ref.price_chf_m2)}/m²",
                delta=None if per_m2 is None
                else f"{E.chf(per_m2 - price_ref.price_chf_m2)} /m² Differenz",
                help=(
                    "Die Referenz gilt der ganzen Parzelle inklusive Bestand, der "
                    "Residualwert nur der zusätzlichen Geschossfläche. Ein "
                    "Screening-Vergleich, keine Bewertung."
                ),
            )
        st.caption(NEGATIVE_CAVEAT if land < 0 else RESULT_CAVEAT)

    # ── Block D ─────────────────────────────────────────────────────────────
    # The regulations themselves, straight out of the same extract. Before this,
    # the answer to "what may I build here" ended at a number; now the document
    # that sets the number is one click away, and it is the one the cadastre
    # names for this parcel rather than one found by matching municipality names.
    #
    # Folded away by default: it is a reference to open when a candidate is
    # worth the reading, not something to scroll past on every parcel.
    with st.expander("D · Rechtsgrundlagen", expanded=False):
        if not extract:
            st.caption(
                "Erst nach der ÖREB-Abfrage verfügbar — geprüft wird die Shortlist, "
                "über «▶ Neu berechnen» in der Liste."
            )
        else:
            left, right = st.columns(2)
            left.markdown("**Rechtsvorschriften**")
            for doc in extract.get("provisions") or []:
                left.markdown(f"- {_document_line(doc)}")
            if not extract.get("provisions"):
                left.caption("keine im Auszug")
            right.markdown("**Gesetzliche Grundlagen**")
            for doc in extract.get("laws") or []:
                right.markdown(f"- {_document_line(doc)}")
            if not extract.get("laws"):
                right.caption("keine im Auszug")
            st.caption(
                "Aus dem ÖREB-Auszug dieser Parzelle"
                + (f" vom {extract['created'][:10]}" if extract.get("created") else "")
                + ". Die Bau- und Nutzungsordnung ist die der zuständigen Gemeinde, "
                "so wie der Kataster sie dieser Parzelle zuordnet."
            )

    # ── Block E ─────────────────────────────────────────────────────────────
    # What block D cannot say. The ÖREB extract names the documents that govern
    # this parcel and marks them `inForce`, and stops — it carries no date. That
    # date is the whole question behind "is this analysis still on the current
    # rules", and OEREBlex answers it for the canton in one request.
    #
    # Three rows visible and the rest folded, rather than folded entirely: a
    # change list nobody opens is a change list nobody reads, and the top of it
    # is short enough to take in at a glance.
    #
    # The request itself runs in a background thread (`regulations.
    # ensure_news_started`), started here rather than awaited here: block E is
    # the only thing in this view that depends on a third-party server, and
    # this function has no business making the other four blocks wait on it.
    st.subheader("E · Neueste Änderungen")
    # Started first, then read — never "started, therefore nothing yet". A
    # twelve-hour re-arm also returns True while the previous list is still
    # held, and collapsing those two cases threw that list away for exactly
    # the render that triggered the refresh.
    started = R.ensure_news_started()
    news_status, news_result = R.news_state()
    if started:
        # The status, not the result, is forced: the worker can finish between
        # these two lines when `load` is fast or stubbed, and the placeholder
        # would then appear or not depending on thread scheduling.
        news_status = "in_flight"
    # Rendered from whatever result exists, not from the status: once a
    # twelve-hour refetch re-arms, the previous list is still the best
    # answer available, and swapping it for a placeholder every half day
    # would take information away to announce a request nobody asked for.
    edicts, news_error = (
        news_result if news_result is not None
        else ([], "Änderungsliste wird noch geladen.")
    )
    # Looked up once. The panel and the exported sheet print the same sentence,
    # and two reads could answer differently if the fetch finishes in between
    # them — including "in flight" vs. "done" now that this runs in the
    # background rather than being handed in as one fixed value.
    #: Whether there is anything to draw yet, which is not the same question
    #: as whether a fetch is running. Every branch below keys off this rather
    #: than off the status: during a twelve-hour refresh a fetch *is* running
    #: and the previous list is still the best answer there is.
    have_news = news_result is not None
    own, own_note = (
        (None, "") if not have_news or news_error
        else R.for_municipality(edicts, _text(row["municipality"]), int(row["bfs"]))
    )
    with st.container(key="facts_e"):
        if not have_news:
            # Nothing to show yet — fills itself in on its own schedule, see
            # `_regulation_news_poll`.
            _regulation_news_poll()
        elif news_error:
            # Never a blank panel: an empty change list reads as "nothing has
            # changed lately", which is the opposite of "we could not ask".
            st.caption(
                f"Änderungsliste nicht abrufbar: {news_error}. Block D ist davon "
                "unberührt — der stammt aus dem ÖREB-Auszug dieser Parzelle, "
                "nicht aus dieser Abfrage."
            )
        else:
            if own:
                line = f"**{own.label}** in Kraft seit **{own.when}**"
                if own.document:
                    line += f" — [Dokument]({own.document})"
                st.markdown(f"{row['municipality']}: {line}")
            else:
                st.markdown(
                    f"{row['municipality']}: in OEREBlex keine gültige "
                    "Rechtsvorschrift verzeichnet."
                )
            if own_note:
                st.caption(own_note)
            if edicts:
                st.markdown("**Im Kanton zuletzt in Kraft getreten**")
                st.markdown(_facts(_edict_rows(edicts[:3])))
                if len(edicts) > 3:
                    with st.expander(f"Alle {len(edicts)} Änderungen"):
                        st.markdown(_facts(_edict_rows(edicts[3:])))
            st.caption(
                "Quelle: oereblex.ag.ch — dieselbe Plattform, auf die die "
                "Dokumente in Block D verlinken. Verzeichnet, was in Kraft ist; "
                "Revisionen im Mitwirkungsverfahren stehen dort nicht und sind "
                "nur über das Amtsblatt-Abonnement zu sehen."
            )
            if news_status != "done":
                # A refresh is running behind the list above. The fragment
                # draws nothing while a result exists; it is here so the
                # replacement arrives on its own rather than waiting for the
                # reader to click something unrelated.
                _regulation_news_poll()

    # ── Export ──────────────────────────────────────────────────────────────
    blocks = [
        ("A · Grunddaten", _base_block(row, cache, price_ref, extract)),
        (
            "B · Potenzial",
            [
                ("Potenzial", f"{potential:,.0f} m² GF".replace(",", "’")),
                ("Wohnungsgrösse (Annahme)", f"{unit_size:,.0f} m²".replace(",", "’")),
                ("Mögliche Wohnungen", "—" if possible is None else f"{possible:.1f}"),
            ],
        ),
    ]
    # Printed with the full URL rather than a hyperlink: the data sheet is meant
    # to survive being printed and marked up, and a link nobody can read is a
    # dead end on paper.
    if zone_rows:
        blocks.append(("Legende beteiligter Objekte (ÖREB)", zone_rows))
    for heading, key in (
        ("Rechtsvorschriften", "provisions"),
        ("Gesetzliche Grundlagen", "laws"),
    ):
        entries = (extract or {}).get(key) or []
        if entries:
            blocks.append((heading, [
                (
                    doc["title"] + (f" ({doc['abbr']})" if doc.get("abbr") and doc["abbr"] not in doc["title"] else ""),
                    " ".join(filter(None, [doc.get("number", ""), *doc.get("urls", [])])),
                )
                for doc in entries
            ]))
    # Only the parcel's own half of block E reaches the paper. The canton-wide
    # change list is news, not a fact about this parcel, and 227 rows would bury
    # a one-page data sheet. What belongs here is the date the sheet was
    # computed under: a printed analysis that does not say which edition of the
    # building regulation it assumed cannot be checked later.
    blocks.append(_regulation_block(own, own_note, news_error))

    document = report.build(
        title=address,
        subtitle=(
            f"{row['municipality']} · Parzelle {row['parcel']} · "
            f"{_text(row.get('zone')) or 'ohne Zone'} · Datenblatt Verdichtungspotenzial"
        ),
        blocks=blocks,
        steps=steps,
        notes=["Annahmen und Quellen:"] + notes,
    )
    pdf_action.download_button(
        "Als PDF exportieren",
        data=document,
        file_name=f"parzelle-{row['municipality']}-{row['parcel']}.pdf".replace(" ", "-"),
        mime="application/pdf",
        width="stretch",
        help="Alle drei Blöcke samt vollständigem Rechenweg und Quellen.",
    )
