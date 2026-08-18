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

import pandas as pd
import streamlit as st

import economics as E
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
       "profit")


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


def _benchmark_help(key, extra=""):
    mark = E.BENCHMARKS[key]
    text = f"Vorgabewert {E.chf(mark.value)} {mark.unit}. {mark.provenance}"
    return f"{text} {extra}".strip()


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


def page(parcels, cache, price_of):
    """Render the detail view for the selected parcel.

    `price_of` is a callable returning the land-price reference for a row, so
    this module does not need to know how that lookup is configured.
    """
    pid = selected()
    row = find(parcels, pid)

    top = st.columns([1, 4])
    if top[0].button("← Zurück zur Liste", width="stretch"):
        close()
        st.rerun()

    if row is None:
        st.warning(
            f"Parzelle {pid} steht nicht mehr in der Ergebnistabelle — "
            "vermutlich wurde inzwischen neu gerechnet."
        )
        st.stop()

    address = _text(row.get("address")) or f"Parzelle {row['parcel']}"
    st.title(address)
    st.caption(
        f"{row['municipality']} · Parzelle {row['parcel']} · "
        f"{_text(row.get('zone')) or 'ohne Zone'}"
    )

    price_ref = price_of(row)
    extract = extract_of(row, cache)

    # ── Block A ─────────────────────────────────────────────────────────────
    st.subheader("A · Grunddaten")
    st.markdown(_facts(_base_block(row, cache, price_ref, extract)))
    st.markdown(_links(row))
    zone_rows = _zone_rows(extract) if extract else []
    if zone_rows:
        st.markdown("**Legende beteiligter Objekte** (ÖREB-Auszug)")
        st.markdown(_facts(zone_rows))

    # ── Block B ─────────────────────────────────────────────────────────────
    st.subheader("B · Potenzial")
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
    st.subheader("C · Residualwertrechnung")
    st.caption(
        "Landwert = Verkaufserlös der neuen Flächen − Baukosten − Baunebenkosten "
        "− Abbruch − Finanzierung − Gewinn/Risiko. Die Vorgabewerte sind "
        "publizierte Richtwerte mit Quelle, keine Bewertung dieser Parzelle — "
        "die Zahlen unten sind zum Überschreiben da."
    )
    c1, c2, c3, c4 = st.columns(4)
    sale_price = _number(
        c1, "Verkaufspreis (CHF/m²)", pid, "sale",
        E.BENCHMARKS["sale_price_chf_m2"].value, step=100.0,
        help=_benchmark_help("sale_price_chf_m2"),
    )
    sale_share = _number(
        c2, "Verkaufsflächenanteil (%)", pid, "share",
        E.BENCHMARKS["sale_area_pct"].value, step=1.0, minimum=10.0, maximum=100.0,
        help=_benchmark_help(
            "sale_area_pct",
            "Erlös und Baukosten werden beide auf diese Fläche gerechnet.",
        ),
    )
    construction = _number(
        c3, "Baukosten (CHF/m²)", pid, "build",
        E.BENCHMARKS["construction_chf_m2"].value, step=50.0,
        help=_benchmark_help("construction_chf_m2"),
    )
    ancillary = _number(
        c4, "Baunebenkosten (%)", pid, "ancillary",
        E.BENCHMARKS["ancillary_pct"].value, step=1.0, maximum=100.0,
        help=_benchmark_help("ancillary_pct"),
    )

    d1, d2, d3, d4 = st.columns(4)
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
        help=_benchmark_help("demolition_chf_m2"),
    )
    financing = _number(
        d3, "Finanzierung (%)", pid, "financing",
        E.BENCHMARKS["financing_pct"].value, step=0.5, maximum=100.0, fmt="%.1f",
        help=_benchmark_help("financing_pct"),
    )
    profit = _number(
        d4, "Gewinn / Risiko (%)", pid, "profit",
        E.BENCHMARKS["profit_pct"].value, step=1.0, maximum=100.0,
        help=_benchmark_help("profit_pct"),
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
        profit_pct=profit,
        demolish=bool(demolish),
    )
    land = E.land_value(steps)
    per_m2 = E.per_square_metre(steps, float(row["area"]))

    # Every line, not just the total: the people this is for adjust assumptions,
    # and an assumption cannot be adjusted if only the result is visible.
    path = "\n".join(
        f"| {'**' + s.label + '**' if s.kind == 'result' else s.label} | {s.formula} | "
        f"{(E.chf(s.value) + ' m²') if s.unit == 'm²' else 'CHF ' + E.chf(s.value)} |"
        for s in steps
    )
    st.markdown(f"| Schritt | Rechnung | Betrag |\n|---|---|---:|\n{path}")

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
    if land < 0:
        st.warning(
            "Negativer Residualwert: Mit diesen Annahmen trägt das zusätzliche "
            "Potenzial die Erstellungskosten nicht."
        )

    used = [
        ("sale_price_chf_m2", sale_price, "Verkaufspreis CHF/m²"),
        ("construction_chf_m2", construction, "Baukosten CHF/m²"),
        ("sale_area_pct", sale_share, "Verkaufsflächenanteil %"),
        ("ancillary_pct", ancillary, "Baunebenkosten %"),
        ("demolition_chf_m2", demolition, "Abbruchkosten CHF/m²"),
        ("financing_pct", financing, "Finanzierung %"),
        ("profit_pct", profit, "Gewinn / Risiko %"),
    ]
    notes = _assumption_notes(used)
    with st.expander("Annahmen und Quellen"):
        for note in notes:
            st.markdown(f"- {note}")
        if st.button("Annahmen zurücksetzen"):
            forget(pid)
            st.rerun()

    # ── Block D ─────────────────────────────────────────────────────────────
    # The regulations themselves, straight out of the same extract. Before this,
    # the answer to "what may I build here" ended at a number; now the document
    # that sets the number is one click away, and it is the one the cadastre
    # names for this parcel rather than one found by matching municipality names.
    st.subheader("D · Rechtsgrundlagen")
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
    st.download_button(
        "Als PDF exportieren",
        data=document,
        file_name=f"parzelle-{row['municipality']}-{row['parcel']}.pdf".replace(" ", "-"),
        mime="application/pdf",
        type="primary",
        help="Alle drei Blöcke samt vollständigem Rechenweg und Quellen.",
    )
    st.caption(
        "Der Residualwert bewertet nur das zusätzliche Potenzial, nicht die "
        "Parzelle: der Wert des bestehenden Gebäudes ist darin nicht enthalten. "
        "Erschliessung, Baugrund, Lärmschutz, Auflagen aus einem Gestaltungsplan "
        "und die tatsächliche anrechenbare Geschossfläche sind hier nicht "
        "gerechnet. Die eigenen Annahmen aus Block C gelten für alle Parzellen "
        "dieser Sitzung — einmal eingetragen, nicht pro Parzelle wieder. "
        "Potenzial und Abbruch gehören zur Parzelle und bleiben dort."
    )
