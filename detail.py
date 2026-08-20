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
import report

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
  .st-key-result_bar { padding:.6rem 1rem .1rem; margin-bottom:.4rem;
      border-radius:8px; border:1px solid rgba(128,128,128,.38);
      background:rgba(127,127,127,.06);
      -webkit-backdrop-filter:blur(30px) saturate(1.7) brightness(1.08);
      backdrop-filter:blur(30px) saturate(1.7) brightness(1.08);
      box-shadow:0 6px 20px rgba(0,0,0,.10); }
  .st-key-result_bar [data-testid="stMetricValue"] { font-size:1.55rem; }

  .st-key-inputs_b, .st-key-inputs_c { padding:.7rem 1rem .1rem;
      margin-bottom:.3rem; border-left:3px solid rgba(255,75,75,.55);
      border-radius:0 8px 8px 0; background:rgba(127,127,127,.09); }

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

  /* Below about a thousand pixels the halves are too narrow for
     "Denkmal-/Inventarstatus" to fit on any line, and a word that cannot fit
     takes the table over the seam with it. Hard breaking only there — on a
     desktop it would split ordinary words mid-syllable for nothing. */
  @media (max-width: 1000px) {
    [class*="st-key-facts_"] td, [class*="st-key-facts_"] th {
        overflow-wrap:anywhere; }
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


def page(parcels, cache, price_of, news=None):
    """Render the detail view for the selected parcel.

    `price_of` is a callable returning the land-price reference for a row, so
    this module does not need to know how that lookup is configured. `news` is
    the `(edicts, error)` pair from `regulations.load()`, fetched and cached by
    the caller for the same reason.

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

    # Back on the left, export on the right — the two things done to the page
    # as a whole, at the top where document actions are looked for. The export
    # column is written at the end of this function, once there is a document
    # to hand over; a column keeps its place on the page whenever it is filled.
    top = st.columns([2, 5, 2], vertical_alignment="center")
    if top[0].button("← Zurück zur Liste", width="stretch"):
        close()
        st.rerun()

    if row is None:
        st.warning(
            f"Parzelle {pid} steht nicht mehr in der Ergebnistabelle — "
            "vermutlich wurde inzwischen neu gerechnet."
        )
        st.stop()

    st.markdown(PAGE_CSS, unsafe_allow_html=True)

    address = _text(row.get("address")) or f"Parzelle {row['parcel']}"
    st.title(address)
    st.caption(
        f"{row['municipality']} · Parzelle {row['parcel']} · "
        f"{_text(row.get('zone')) or 'ohne Zone'}"
    )

    price_ref = price_of(row)
    extract = extract_of(row, cache)

    # Claimed here and written at the end: the result needs the inputs below to
    # exist before it can be computed, but it belongs above them on the page.
    result_bar = st.container(key="result_bar")

    # 32px between the halves, not Streamlit's 64: the gutter was the one
    # place the page had spare room, and block A is the column that wanted
    # it. Measured — "medium" is 32px and gives each half 624px at 1440.
    facts, work = st.columns(2, gap="medium")

    # ── Block A ─────────────────────────────────────────────────────────────
    facts.subheader("A · Grunddaten")
    with facts.container(key="facts_a"):
        st.caption("Aus den Registern übernommen — hier ist nichts veränderbar.")
        st.markdown(_facts(_base_block(row, cache, price_ref, extract)))
        st.markdown(_links(row))
        zone_rows = _zone_rows(extract) if extract else []
        if zone_rows:
            st.markdown("**Legende beteiligter Objekte** (ÖREB-Auszug)")
            st.markdown(_facts(zone_rows))

    # ── Block B ─────────────────────────────────────────────────────────────
    work.subheader("B · Potenzial")
    with work.container(key="inputs_b"):
        b1, b2, b3 = st.columns(3)
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
        b3.metric(
            "Mögliche Wohnungen",
            "—" if possible is None else f"{possible:.1f}",
            help="Potenzial ÷ Wohnungsgrösse. Rechnerisch, ohne Grundriss.",
        )

    # ── Block C ─────────────────────────────────────────────────────────────
    work.subheader("C · Residualwertrechnung")
    # Two controls to a row rather than four: in the narrower half of the split
    # a four-wide row leaves each field about a stepper wide and wraps every
    # label onto three lines.
    with work.container(key="inputs_c"):
        st.caption(
            "Landwert = Verkaufserlös der neuen Flächen − Baukosten − Baunebenkosten "
            "− Abbruch − Finanzierung − Reserve. Der Verkaufspreis rechnet auf 80% "
            "der Geschossfläche, die Baukosten auf 100%. Mit der Maus über einen "
            "Schritt fahren zeigt die Formel dahinter."
        )
        c1, c2 = st.columns(2)
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

        c3, c4 = st.columns(2)
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

        d1, d2 = st.columns(2, vertical_alignment="bottom")
        has_building = bool(row["buildings"]) and row["existing"] > 0
        demolish = _remember(pid, "demolish", d1.checkbox(
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
        demolition = _number(
            d2, "Abbruchkosten (CHF/m²)", pid, "demolition",
            E.BENCHMARKS["demolition_chf_m2"].value, step=10.0,
            help=_benchmark_help("demolition_chf_m2", name="demolition"),
        )

        d3, d4 = st.columns(2)
        financing = _number(
            d3, "Finanzierung (%)", pid, "financing",
            E.BENCHMARKS["financing_pct"].value, step=0.5, maximum=100.0, fmt="%.1f",
            help=_benchmark_help("financing_pct", name="financing"),
        )
        reserve = _number(
            d4, "Reserve / Unvorhergesehenes (%)", pid, "reserve",
            E.BENCHMARKS["reserve_pct"].value, step=1.0, maximum=100.0,
            help=_benchmark_help("reserve_pct", name="reserve"),
        )

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
    # Where each figure above came from, kept with the figures rather than with
    # the calculation — and it closes the right-hand column, which is the shorter
    # of the two now that the table has left it.
    with work.expander("Annahmen und Quellen"):
        for note in notes:
            st.markdown(f"- {note}")
        if st.button("Annahmen zurücksetzen"):
            forget(pid)
            st.rerun()

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
    st.markdown(_calculation_table(steps), unsafe_allow_html=True)
    st.caption(DISCLAIMER)

    # ── The result bar ──────────────────────────────────────────────────────
    # Written last, drawn first. The warning belongs here rather than beside the
    # table: it explains the number, and this is where the number is read.
    with result_bar:
        r1, r2, r3 = st.columns(3)
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
    st.subheader("E · Neueste Änderungen")
    edicts, news_error = news if news else ([], "")
    # Looked up once. The panel and the exported sheet print the same sentence,
    # and two calls could answer differently the day the join changes.
    own, own_note = (
        (None, "") if news_error
        else R.for_municipality(edicts, _text(row["municipality"]), int(row["bfs"]))
    )
    with st.container(key="facts_e"):
        if news_error:
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
    top[2].download_button(
        "Als PDF exportieren",
        data=document,
        file_name=f"parzelle-{row['municipality']}-{row['parcel']}.pdf".replace(" ", "-"),
        mime="application/pdf",
        type="primary",
        width="stretch",
        help="Alle drei Blöcke samt vollständigem Rechenweg und Quellen.",
    )
