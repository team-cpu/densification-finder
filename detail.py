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
open. Personal accounts can explicitly save and load a team snapshot when
the organisation enables shared calculations; unsaved drafts remain local.
"""
import json
import re
from datetime import date, datetime
from functools import partial
from html import escape

import pandas as pd
import streamlit as st

import amtsblatt as AB
import economics as E
import planning as PL
import planning_preview as PP
import regulations as R
import formatting as F
import links as L
import navigation
import report
import source_downloads
import paths
import workflow as WF
import ui_components as UI

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


def _detail_header_badges(row, cache):
    """Compact parcel facts used beside the detail title in the prototype."""
    badges = []

    def add(label, tone):
        if label not in {item[0] for item in badges}:
            badges.append((label, tone))

    if bool(row.get("design_plan")):
        add("Gestaltungsplan", "dev")
    if _text(row.get("heritage")):
        add("Inventar", "heritage")

    egrid = _text(row.get("egrid"))
    notable = ""
    if (
        egrid
        and cache is not None
        and not cache.empty
        and egrid in cache.index
        and "notable" in cache.columns
    ):
        notable = _text(cache.loc[egrid, "notable"]).casefold()
    if "lärm" in notable:
        add("Lärm ES II", "warning")
    elif "gewässer" in notable:
        add("Gewässerabstand", "warning")
    elif "dienstbarkeit" in notable:
        add("Dienstbarkeit", "warning")

    return badges[:2] or [("Unbelastet", "clear")]


def _detail_calculated_at(value):
    """Prototype timestamp text, using the parcel's real calculation time."""
    text = _text(value)
    if not text:
        return "Zuletzt gerechnet —"
    try:
        return pd.Timestamp(text).strftime("Zuletzt gerechnet %d.%m.%Y, %H:%M")
    except (TypeError, ValueError):
        return "Zuletzt gerechnet —"


def _result_bar_html(row, land, per_m2, price_ref):
    """Render the three-cell result strip with the prototype's exact hierarchy."""
    per_m2_text = "—" if per_m2 is None else f"{E.chf(per_m2)} CHF"
    reference_label = "Referenz"
    reference_value = "—"
    comparison = ""
    if price_ref:
        reference_label = f"Referenz {price_ref.scope}"
        reference_value = E.chf(price_ref.price_chf_m2)
        if per_m2 is not None and price_ref.price_chf_m2:
            delta = (per_m2 / price_ref.price_chf_m2 - 1) * 100
            delta_label = f"{'+' if delta >= 0 else '−'}{abs(delta):.1f}%"
            delta_tone = "positive" if delta >= 0 else "negative"
            comparison = (
                '<div class="detail-result-comparison">'
                f'<span class="detail-result-delta {delta_tone}">'
                f'{escape(delta_label)}</span>'
                '<span class="detail-result-note">vs. Referenz</span></div>'
            )

    caveat = NEGATIVE_CAVEAT if land < 0 else RESULT_CAVEAT
    return (
        '<div class="detail-result-grid" '
        f'data-residual="{escape(str(land))}" '
        f'data-per-square-metre="{escape(str(per_m2))}">'
        '<div class="detail-result-cell detail-result-primary">'
        '<div class="detail-result-label">Residualer Landwert</div>'
        '<div class="detail-result-primary-value">'
        '<span class="detail-result-currency">CHF</span>'
        f'<span class="detail-result-hero">{escape(E.chf(land))}</span></div>'
        f'<div class="detail-result-note">{escape(caveat)}</div></div>'
        '<div class="detail-result-cell">'
        '<div class="detail-result-label">Pro m² Grundstück</div>'
        f'<div class="detail-result-value">{escape(per_m2_text)}</div>'
        '<div class="detail-result-note">Grundstück '
        f'{escape(E.chf(float(row["area"])))} m²</div></div>'
        '<div class="detail-result-cell">'
        f'<div class="detail-result-label">{escape(reference_label)}</div>'
        f'<div class="detail-result-value reference">{escape(reference_value)}</div>'
        f'{comparison}</div></div>'
    )


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


def _detail_facts_html(row, cache, price_ref, extract=None):
    """The compact official-data card used by the HTML prototype.

    The full register payload still feeds the PDF and block D.  This summary is
    deliberately kept to the ten rows a user needs beside the assumptions;
    only material ÖREB or area discrepancies add a warning row.
    """
    metric = F.METRIC_LABELS.get(row["metric"], "Ausnützungsziffer")
    permitted = float(row["existing"]) + float(row["delta"])
    use = F.short_use(_text(row.get("use_class")))
    year = F.short_year(_text(row.get("built")))
    if int(row["buildings"]) == 0:
        building = "unbebaut"
    else:
        building = ", ".join(filter(None, [use, f"Baujahr {year}" if year else ""]))
        building = building or f"{int(row['buildings'])} Gebäude"

    reference = "keine Referenz hinterlegt"
    if price_ref:
        reference = (
            f"CHF {E.chf(price_ref.price_chf_m2)}/m² · "
            f"{price_ref.scope} · {price_ref.as_of}"
        )

    rows = [
        ("Adresse", _text(row.get("address")) or "— (keine GWR-Adresse)", False, ""),
        ("Gemeinde / Kanton", f"{row['municipality']} / Aargau", False, ""),
        (
            "Parzellen-Nr. (E-GRID)",
            " · ".join(filter(None, [str(row["parcel"]), _text(row.get("egrid"))])) or "—",
            True,
            "",
        ),
        ("Grundstücksfläche", f"{E.chf(float(row['area']))} m²", True, ""),
        ("Zone", _text(row.get("zone")) or "—", False, ""),
        (metric, f"{row['az']:.3f}".rstrip("0").rstrip("."), True, ""),
        ("Zulässige aBGF", f"{E.chf(permitted)} m²", True, ""),
        ("Bestehende aBGF", f"{E.chf(float(row['existing']))} m²", True, ""),
        ("Bestehendes Gebäude", building, False, ""),
        ("Referenzpreis Land", reference, True, ""),
    ]

    oereb = _oereb_text(row, cache).replace("**", "")
    if oereb != "keine Eigentumsbeschränkung im Kataster":
        rows.append(("ÖREB-Kataster", oereb, False, "warning"))

    if extract and extract.get("land_registry_area"):
        registry = float(extract["land_registry_area"])
        gap = abs(registry - float(row["area"]))
        if gap > 1:
            rows.append((
                "Abweichung Grundbuchfläche",
                f"{E.chf(registry)} m² im ÖREB · Abweichung zur berechneten "
                f"Fläche {E.chf(gap)} m²",
                True,
                "warning",
            ))

    row_html = "".join(
        '<div class="detail-fact-row '
        f'{escape(tone)}">'
        f'<span class="detail-fact-label">{escape(str(label))}</span>'
        '<span class="detail-fact-value'
        f'{" mono" if mono else ""}">{escape(str(value))}</span></div>'
        for label, value, mono, tone in rows
    )
    return (
        '<section class="detail-facts-card">'
        '<div class="detail-facts-head">'
        '<span class="detail-facts-title">A · Amtliche Grunddaten</span>'
        '<span class="detail-edit-pill readonly">Nicht editierbar</span>'
        '</div>'
        f'<div class="detail-facts-body">{row_html}'
        '<div class="detail-facts-source">Quelle: Grundbuch Kanton Aargau, '
        f'kommunale Bau- und Zonenordnung {escape(str(row["municipality"]))}, '
        'amtliche Vermessung.</div></div></section>'
    )


def _potential_result_html(row, potential, unit_size, possible, *, demolish,
                           demolition_step):
    """Render block B's calculated part in the prototype's compact layout."""
    units = "—" if possible is None else f"{possible:.1f}"
    formula = "—"
    if possible is not None:
        formula = f"{E.chf(potential)} m² ÷ {E.chf(unit_size)} m²"

    replacement_note = ""
    existing = float(row.get("existing") or 0)
    if existing > 0 or demolition_step.overridden:
        if existing > 0 and demolish:
            decision = (f'Ersatzneubau geprüft: bestehendes Volumen von '
                        f'{E.chf(existing)} m² aBGF wird ersetzt. ')
        elif existing > 0:
            decision = (f'Kein Ersatzneubau ausgewählt: bestehendes Volumen von '
                        f'{E.chf(existing)} m² aBGF bleibt erhalten. ')
        else:
            decision = 'Kein bestehendes Gebäude erfasst. '
        if demolition_step.overridden:
            cost_note = (f'Abbruchkosten in Block C manuell auf CHF '
                         f'{E.chf(abs(demolition_step.value))} gesetzt.')
        elif demolition_step.value != 0:
            cost_note = 'Abbruchkosten in Block C berücksichtigt.'
        else:
            cost_note = 'Keine Abbruchkosten in Block C angesetzt.'
        replacement_note = ('<div class="detail-replacement-note">'
                            f'{decision}{cost_note}</div>')

    return (
        '<div class="detail-potential-result" '
        f'data-units="{escape(units)}">'
        '<div><div class="detail-potential-result-label">'
        'Resultierende Wohneinheiten</div>'
        f'<div class="detail-potential-formula">{escape(formula)}</div></div>'
        f'<div class="detail-potential-units">{escape(units)}</div></div>'
        f'{replacement_note}'
    )


def _safe_href(value):
    """Return an escaped web URL, or an empty string for unsafe schemes."""
    url = _text(value).strip()
    if not url.lower().startswith(("https://", "http://")):
        return ""
    return escape(url, quote=True)


def _reference_card_html(extract, zone_rows, notes):
    """Legal references and assumptions in the prototype's compact card."""
    documents = []
    if extract:
        for category, key in (
            ("Rechtsvorschrift", "provisions"),
            ("Gesetzliche Grundlage", "laws"),
        ):
            for doc in extract.get(key) or []:
                title = _text(doc.get("title")) or category
                abbreviation = _text(doc.get("abbr"))
                if abbreviation and abbreviation not in title:
                    title += f" ({abbreviation})"
                qualifier = " · ".join(
                    part for part in (
                        _text(doc.get("number")),
                    ) if part and part not in title
                )
                documents.append((category, title, qualifier, doc.get("urls") or []))

    link_count = sum(max(1, len(urls)) for _, _, _, urls in documents)
    meta = (
        f"{link_count} Referenz{'en' if link_count != 1 else ''} · PDF"
        if documents else "ÖREB-Abfrage ausstehend"
    )

    rows = []
    for category, title, qualifier, urls in documents:
        actions = []
        for index, url in enumerate(urls, 1):
            href = _safe_href(url)
            if href:
                label = "PDF" if len(urls) == 1 else f"PDF {index}"
                actions.append(
                    f'<a class="detail-reference-action" href="{href}" '
                    f'target="_blank" rel="noopener noreferrer">{label}</a>'
                )
        rows.append(
            '<div class="detail-reference-row">'
            f'<span class="detail-reference-type">{escape(category)}</span>'
            '<div><div class="detail-reference-title">'
            f'{escape(title)}</div>'
            f'<div class="detail-reference-detail">{escape(qualifier or "ÖREB-Auszug dieser Parzelle")}</div></div>'
            f'<div class="detail-reference-actions">{"".join(actions) or "—"}</div></div>'
        )

    if not documents:
        rows.append(
            '<div class="detail-reference-empty">Erst nach der ÖREB-Abfrage '
            'verfügbar.</div>'
        )

    if extract and (extract.get("office") or {}).get("name"):
        office = extract["office"]
        office_name = escape(_text(office.get("name")))
        office_url = _safe_href(office.get("url"))
        office_value = (
            f'<a href="{office_url}" target="_blank" rel="noopener noreferrer">'
            f'{office_name}</a>' if office_url else office_name
        )
        rows.append(
            '<div class="detail-reference-row detail-reference-row--plain">'
            '<span class="detail-reference-type">Zuständige Stelle</span>'
            f'<div class="detail-reference-title">{office_value}</div><div></div></div>'
        )

    if zone_rows:
        zone_summary = " · ".join(
            f"{_text(label)}: {_text(value)}" for label, value in zone_rows[:3]
        )
        rows.append(
            '<div class="detail-reference-row detail-reference-row--plain">'
            '<span class="detail-reference-type">ÖREB-Objekte</span>'
            f'<div class="detail-reference-detail">{escape(zone_summary)}</div><div></div></div>'
        )

    assumption_rows = "".join(f'<li>{escape(note)}</li>' for note in notes)
    created = ""
    if extract and extract.get("created"):
        created = f" · Stand {escape(_text(extract['created'])[:10])}"

    return (
        '<details class="detail-reference-card detail-reference-card--legal" open>'
        '<summary><span class="detail-reference-summary-copy">'
        '<span class="detail-reference-summary-title">Rechtliche Grundlagen &amp; Quellen</span>'
        f'<span class="detail-reference-summary-meta">{escape(meta)}</span>'
        '</span><span class="detail-reference-sign" aria-hidden="true"></span></summary>'
        '<div class="detail-reference-body">'
        + "".join(rows)
        + '<div class="detail-assumptions"><div class="detail-assumptions-title">'
          f'Annahmen &amp; Benchmarks{created}</div><ul>{assumption_rows}</ul></div>'
          '</div></details>'
    )


def _open_link(href):
    """The arrow that opens the original — the Amtsblatt publication or the
    regulation's own document. Nothing is copied into Scope. A link that opens
    a search instead says so: the arrow promises the publication itself."""
    kind = AB.link_kind(href)
    href = _safe_href(href)
    if not href:
        return ""
    if kind == "suche":
        return (f'<a class="detail-regulation-open detail-regulation-open--search" href="{href}" '
                'target="_blank" rel="noopener noreferrer" '
                'title="Amtsblatt-Suche öffnen — keine Einzelpublikation">Suche ↗</a>')
    if kind not in ("publikation", "dokument"):
        return (f'<a class="detail-regulation-open detail-regulation-open--search" href="{href}" '
                'target="_blank" rel="noopener noreferrer" '
                'title="Quelle öffnen — keine Einzelpublikation">Link ↗</a>')
    label = "Publikation öffnen" if kind == "publikation" else "Dokument öffnen"
    return (f'<a class="detail-regulation-open" href="{href}" target="_blank" '
            f'rel="noopener noreferrer" title="{label}" aria-label="{label}">↗</a>')


def _status_badge(status):
    return (f'<span class="detail-regulation-status '
            f'detail-regulation-status--{PL.STATUS_TONES[status]}">'
            f'{escape(PL.STATUS_LABELS[status])}</span>')


def _regulation_row(date_text, title, details, status, href, date_kind="", badge=None, source_link=None):
    """Date, title with what the sources say about it, status badge, arrow.
    `details` are HTML fragments, each already escaped. `date_kind` says
    which date the row is dated by: the notice's publication or the
    regulation's entry into force — never one for the other."""
    kind = f'<span class="detail-regulation-date-kind">{escape(date_kind)}</span>' if date_kind else ""
    return (
        '<div class="detail-regulation-row">'
        f'<span class="detail-regulation-date">{escape(date_text)}{kind}</span>'
        f'<div><div class="detail-reference-title">{escape(title)}</div>'
        f'<div class="detail-reference-detail">{" · ".join(details)}</div></div>'
        f'{_status_badge(status) if badge is None else badge}'
        f'<div class="detail-regulation-arrow">{_open_link(href) if source_link is None else source_link}</div></div>'
    )


#: Version 1 reads no step from a publication. A row shows one of Philipp's
#: badges only for a stage a person checked against a source, while that
#: source supports it on the day shown (`planning.stage_badge`); every other
#: row reads "Publikation".
_PUBLICATION = "Publikation"
_STEP_NOTE = ("Publikation im Amtsblatt, wie publiziert — kein geprüfter Verfahrensschritt, "
              "nicht der heutige Stand")
_PUBLICATIONS_NOTE = ("Je Publikation Titel und Datum, wie im Amtsblatt publiziert — was sie an "
                      "diesem Tag bekanntgab, nicht der heutige Stand des Verfahrens. Einen Schritt "
                      "zeigt eine Zeile nur, wenn er an einer Quelle geprüft und heute noch belegt "
                      "ist; sonst steht «Publikation».")



def _publication_title(pub):
    """The notice's own title — for a canton notice naming several
    municipalities, the part that concerns this one (`planning.publication_title`)."""
    return PL.publication_title(pub)


def _publication_row(pub, today):
    """Version 1: one publication as the Amtsblatt printed it — its date, its
    title, its number, the original. "Publikation", unless a person checked
    its stage against a source that still supports it today; none merged
    with another, no step read from its words."""
    details = [escape(f"Amtsblatt · publiziert {pub.published_on:%d.%m.%Y}")]
    if pub.pub_nr:
        details.append(escape(f"Publ.-Nr. {pub.pub_nr}"))
    if pub.level == "kanton" and pub.municipality:
        details.append(escape(f"aus: {pub.title}"))
    status, note = PL.stage_badge(pub, today)
    if note:
        href = _safe_href(pub.verified.source)
        details.append(escape(note) + (f' · <a href="{href}" target="_blank" rel="noopener noreferrer">'
                                       'Nachweis ↗</a>' if href else ""))
    badge = None if status else (
        f'<span class="detail-regulation-status detail-regulation-status--step" '
        f'title="{escape(_STEP_NOTE)}">{_PUBLICATION}</span>')
    return (pub.published_on, _regulation_row(
        f"{pub.published_on:%d.%m.%Y}", _publication_title(pub), details, status or "", pub.url,
        "publiziert", badge=badge))


def _store_state(planning, today):
    """(what the list covers, warnings): its period and last update — and,
    when it is out of date or its last update failed, saying so. A list that
    looks complete has to be complete as of a date the reader can see."""
    covered = ", ".join(filter(None, [
        f"erfasst seit {planning.since:%d.%m.%Y}" if planning.since else "Erfassungsbeginn nicht angegeben",
        f"Stand {planning.stand:%d.%m.%Y}" if planning.stand else "",
        f"aktualisiert {planning.updated_at:%d.%m.%Y %H:%M}" if planning.updated_at else ""]))
    warnings = []
    if planning.stand and (today - planning.stand).days > PL.STALE_DAYS:
        warnings.append(f"Verzeichnis veraltet: Stand {planning.stand:%d.%m.%Y}, "
                        f"{(today - planning.stand).days} Tage alt — neuere Publikationen fehlen hier.")
    if planning.failed_at:
        why = " ".join(planning.failure.split())[:240]
        warnings.append(f"Letzte Aktualisierung am {planning.failed_at:%d.%m.%Y %H:%M} fehlgeschlagen "
                        f"({why}) — gezeigt wird der letzte gültige Stand.")
    return covered, warnings


def _printed_link(url):
    """A source on paper: the address, readable when printed and clickable on
    screen — or nothing for anything but a web address."""
    if not PL.web_link(url):
        return ""
    # Brackets would end the link's own markup; `web_link` lets no quote,
    # space or angle bracket through.
    href = re.sub(r"[()\[\]]", lambda m: f"%{ord(m.group()):02X}", url)
    shown = re.sub(r"^https?://(www\.)?", "", url).replace("[", "").replace("]", "")
    return f"[{shown}]({href})"


def _printed_publication(pub, today):
    """One publication on paper: date, badge or "Publikation", title, number
    and link — and what the check shows, when there is one."""
    status, note = PL.stage_badge(pub, today)
    title = re.sub(r"[\[\]*]", "", _publication_title(pub))
    number = re.sub(r"[\[\]*]", "", pub.pub_nr)
    source = " · ".join(filter(None, [f"Publ.-Nr. {number}" if number else "", _printed_link(pub.url)]))
    checked = re.sub(r"[\[\]*]", "", note)
    if checked and pub.verified and _printed_link(pub.verified.source):
        checked += f" · Nachweis {_printed_link(pub.verified.source)}"
    label = PL.STATUS_LABELS[status] if status else _PUBLICATION
    return " — ".join(filter(None, [f"{pub.published_on:%d.%m.%Y} {label}: {title}", source, checked]))


def _edict_row(edict, approval, today):
    """A regulation OEREBlex lists — in force, or with its in-force date ahead."""
    title = edict.title or edict.label
    if edict.abbreviation and edict.abbreviation not in title:
        title += f" ({edict.abbreviation})"
    upcoming = edict.in_force > today
    details = [escape(f"OEREBlex · in Kraft {'ab' if upcoming else 'seit'} {edict.when}")]
    if approval is not None:
        href = _safe_href(approval.url)
        note = f"Genehmigung publiziert {approval.published_on:%d.%m.%Y}"
        if AB.link_kind(approval.url) != "publikation":
            note += " (Suche)" if AB.link_kind(approval.url) == "suche" else " (Link)"
        details.append(f'<a href="{href}" target="_blank" rel="noopener noreferrer">'
                       f'{escape(note)} ↗</a>' if href else escape(note))
    # A date ahead is all OEREBlex says — not that an approval is final.
    return (edict.in_force, _regulation_row(
        edict.when, title, details, "kuenftig" if upcoming else "in_kraft", edict.document,
        "in Kraft ab" if upcoming else "in Kraft"))


#: How a zone is linked to the plan whose force may have ended it.
_ZONE_LINK = {
    "explicit": "laut Publikation sichert die Planungszone diesen Plan (§ 29 Abs. 2 BauG)",
    "name": "gleichnamiger Plan — ob die Planungszone ihn sichert, ist nicht belegt",
    "kind": "die Planungszone sichert laut Publikation einen Plan dieser Art, ohne Namen — ob es "
            "dieser ist, ist nicht belegt",
    "general": "die Planungszone sichert laut Publikation die Nutzungsplanung — ob diese "
               "Ausgabe sie umsetzt, ist nicht belegt",
}


def _zone_note(revision, today):
    """Why a Planungszone is shown ended or open: the term it ran out of, or
    what may be the entry into force of the plan it secures — and whether a
    notice links that plan to the zone."""
    kind, when, title, link = revision.zone_end or ("", None, "", "")
    day = f"{when:%d.%m.%Y}" if when else ""
    if kind == "frist":
        return f"Höchstdauer (5 Jahre ab öffentlicher Auflage) am {day} abgelaufen (§ 29 Abs. 2 BauG)"
    if kind == "frist_ca":
        return (f"Höchstdauer (5 Jahre ab öffentlicher Auflage) abgelaufen — Auflage laut "
                f"Publikation vom {day}, Beginn nicht angegeben (§ 29 Abs. 2 BauG)")
    if kind == "teilaufhebung":
        return (f"teilweise aufgehoben (publiziert {day}) — ob diese Parzelle im aufgehobenen Teil "
                "liegt, zeigt die Publikation")
    if kind == "frist_offen":
        return (f"Höchstdauer (5 Jahre ab öffentlicher Auflage) endet um den {day}; Beginn der "
                "Auflage nicht angegeben")
    if kind == "befristet":
        return f"laut Publikation befristet bis {day}"
    if kind == "ab":
        return f"wirksam erst ab der öffentlichen Auflage am {day}"
    if kind == "offen":
        return ("geltende Vorschriften (OEREBlex) nicht verfügbar — ob die gesicherte "
                "Nutzungsplanung inzwischen gilt, ist offen")
    event = {
        "in_kraft": f"seit {day} in Kraft",
        "oereb_unbestaetigt": f"im ÖREB-Auszug vom {day} ist ein gleichnamiger Plan verzeichnet, "
                              "Zuordnung nicht belegt",
        "genehmigung": f"Genehmigung publiziert am {day}, Inkrafttreten nicht belegt",
        "inkraft": f"Inkraftsetzung publiziert am {day}, Datum nicht erkannt",
        "ausgabe": f"seit {day} gilt eine neue Ausgabe",
    }.get(kind, "")
    return f"{event}: {title}; {_ZONE_LINK.get(link, '')}".rstrip("; ")


def _zone_limit(revision):
    """The latest day a zone in force can hold: the term its notice states,
    or five years from its display."""
    limit = PL._years_later(revision.zone_start, PL.PLANUNGSZONE_YEARS)
    if revision.term_end and revision.term_end < limit:
        return f"längstens bis {revision.term_end:%d.%m.%Y} (laut Publikation)"
    if revision.steps[0].period_start:
        return f"längstens bis {limit:%d.%m.%Y} (§ 29 Abs. 2 BauG)"
    return (f"längstens bis etwa {limit:%d.%m.%Y} (§ 29 Abs. 2 BauG; Beginn der Auflage "
            "nicht angegeben)")


#: How the extract states a listing.
_LAWSTATUS = {"inForce": "in Kraft", "changeWithPreEffect": "mit Vorwirkung"}


def _oereb_note(revision):
    """What the parcel's extract lists under this revision's name — with the
    day the extract was read, and whether the listing is shown to be this
    revision."""
    listing = revision.oereb
    day = f"{listing.checked_on:%d.%m.%Y}" if listing.checked_on else "unbekanntem Datum"
    number = f" (Nr. {listing.number})" if listing.number else ""
    state = _LAWSTATUS.get(listing.lawstatus)
    listed = (f"im ÖREB-Auszug vom {day} ist ein Plan dieses Namens {state} verzeichnet{number}"
              if state else
              f"im ÖREB-Auszug vom {day} ist ein Plan dieses Namens verzeichnet{number}, "
              "Rechtsstatus im gespeicherten Auszug nicht enthalten")
    if revision.stage == "verfahren":
        return f"{listed} — diese Revision ist im Amtsblatt nur als Entwurf publiziert"
    if listing.checked_on and listing.checked_on < revision.published_on:
        return f"{listed} — der Auszug ist älter als die letzte Publikation"
    return f"{listed} — ob es diese Revision ist, ist nicht belegt"


def _revision_row(revision, today, egrid=""):
    """One plan at its latest step, from the Amtsblatt, with the evidence its
    badge rests on — or the reason the badge claims no more."""
    details = [f"Amtsblatt · {revision.history}"]
    latest = revision.latest
    status = revision.status
    if latest.period_start and latest.period_end:
        note = f"Frist {latest.period_start:%d.%m.%Y}–{latest.period_end:%d.%m.%Y}"
        if latest.period_end < today:
            details.append(note + " (abgelaufen)")
            # Why a draft reads "Stand unbestätigt" — not a zone, which holds
            # past its display.
            if status == "unbestaetigt" and revision.step != "planungszone":
                details.append("kein späterer Schritt im Verzeichnis")
        elif latest.period_start > today:
            details.append(note + " (noch nicht begonnen)")
        else:
            details.append(note)
    elif revision.stage == "verfahren":
        details.append("Zeitraum in der Publikation nicht erkannt")
    if revision.step == "inkraft" and not revision.effective_on:
        details.append("Datum des Inkrafttretens nicht erkannt")
    if revision.effective_on:
        verb = {"in_kraft": "in Kraft seit", "genehmigt": "in Kraft ab",
                "inkraft_festgelegt": "in Kraft ab"}.get(status, "Inkrafttreten laut Publikation am")
        details.append(f"{verb} {revision.effective_on:%d.%m.%Y}")
    elif revision.planned_on:
        # What a decision announced: the approval may still say otherwise.
        details.append(f"Inkrafttreten vorgesehen ab {revision.planned_on:%d.%m.%Y} (laut Beschluss)")
    if status == "genehmigt" and revision.appeal_until and not (
            revision.effective_on and revision.effective_on > today):
        details.append(f"Beschwerdefrist bis {revision.appeal_until:%d.%m.%Y}")
    elif status == "genehmigt_unbelegt":
        details.append("Inkrafttreten in den Quellen nicht belegt")
    elif status == "beschlossen":
        details.append("Genehmigung im Verzeichnis nicht gefunden")
    elif revision.step == "planungszone" and status == "planungszone":
        details.append(_zone_limit(revision))
    elif revision.step == "planungszone" and revision.zone_end:
        details.append(_zone_note(revision, today))
    html = [escape(d) for d in details]
    if revision.oereb:
        note = escape(_oereb_note(revision))
        # A listing not shown to be this revision: the current extract is one
        # click away ("ÖREB prüfen" refreshes a stored extract without legal
        # status once — `screening.oereb_targets`).
        href = (_safe_href(f"https://api.geo.ag.ch/v2/oereb/extract/pdf/?EGRID={egrid}")
                if egrid and revision.stage != "verfahren" else "")
        html.append(note + (f' — <a href="{href}" target="_blank" rel="noopener noreferrer">'
                            'aktuellen ÖREB-Auszug öffnen ↗</a>' if href else ""))
    elif revision.scope == "gemeinde":
        html.append(escape("ganze Gemeinde"))
    elif revision.scope != "kanton":
        html.append(escape(revision.scope_label))
    return (revision.published_on, _regulation_row(
        f"{revision.published_on:%d.%m.%Y}", revision.title, html, status, revision.url,
        "publiziert"))


def _amtsblatt_links(place):
    links = (
        (AB.municipal_planning_link(place), f"Amtsblatt-Suche: {place}, Bau- und Nutzungsordnung"),
        (AB.canton_approvals_link(place), f"Amtsblatt-Suche: Genehmigungen des Kantons für {place}"),
        (AB.canton_consultations_link(), "Amtsblatt-Suche: Anhörungen und Mitwirkungen des Kantons"),
    )
    return '<ul class="detail-regulation-links">' + "".join(
        f'<li><a href="{_safe_href(href)}" target="_blank" rel="noopener noreferrer">'
        f'{escape(label)} ↗</a></li>' for href, label in links) + "</ul>"


def _note(text, error=False):
    return (f'<div class="detail-reference-empty{" detail-reference-empty--error" if error else ""}">'
            f'{text}</div>')


def _regulation_card_html(row, edicts, own, own_note, *, loading=False, error="",
                          planning=None, preview=None, today=None):
    """Block E: the parcel's municipality and the canton, nothing else.

    Version 1 (the default): OEREBlex's regulations as one group, then the
    Amtsblatt publications as printed — date, title, the badge "Publikation",
    the arrow to the original — no step read, none merged, no status of
    today, no ÖREB match (`_publication_card_rows`). With `SCOPE_PLANNING_INFERENCE`
    set, the inference path instead: revisions with status badges and the
    parcel's ÖREB extract as a hint (`planning.py`). Another municipality's
    regulation is never shown, and a canton notice counts only for the
    municipality it names.

    Without a publication store the Amtsblatt rows are missing. The card says
    so — a temporary fallback, with the Amtsblatt search filtered to the
    municipality — instead of reading as "nothing is changing".
    """
    place = _text(row.get("municipality"))
    municipality = escape(place)
    planning = planning or PL.View(status="off")
    today = today or PP.swiss_today()
    preview = preview or PP.View()
    preview_enabled = preview.status != "off"
    production_ids = {pub.pub_nr for pub in planning.publications + planning.canton_publications}
    for revision in planning.in_force + planning.pending() + planning.ended:
        production_ids.update(pub.pub_nr for pub in revision.steps)
    preview_items = [item for item in preview.items if not (
        item.candidate["source_id"]["namespace"] == PP.NS_AMTSBLATT
        and item.candidate["source_id"]["value"] in production_ids)]
    local = [] if loading or error else R.in_municipality(edicts, place)
    in_force = [e for e in local if e.in_force <= today]
    upcoming = [e for e in local if e.in_force > today]
    pending = planning.pending()

    if loading:
        meta = "wird geladen"
    else:
        meta = ("OEREBlex nicht erreichbar" if error else
                f"{len(in_force)} {'Vorschrift' if len(in_force) == 1 else 'Vorschriften'} in Kraft"
                + (f" · {len(upcoming)} künftig in Kraft" if upcoming else ""))
        if planning.status == "ok" and not planning.inference:
            count = len(planning.publications) + len(planning.canton_publications)
            parts = ([f"{count} {'Publikation' if count == 1 else 'Publikationen'} im "
                      "Amtsblatt-Verzeichnis"] if count else []) + (
                [f"{planning.unattributed} nicht zuordenbar"] if planning.unattributed else [])
            meta += " · " + (" · ".join(parts) or "keine Publikationen im Amtsblatt-Verzeichnis")
            _, warnings = _store_state(planning, today)
            meta += "".join(" · " + flag for flag, hit in (
                ("Verzeichnis veraltet", any(w.startswith("Verzeichnis veraltet") for w in warnings)),
                ("Aktualisierung fehlgeschlagen", bool(planning.failed_at))) if hit)
        elif planning.status == "ok":
            count = (len(planning.in_force) + len(pending) + len(planning.ended)
                     + len(planning.approvals))
            parts = ([f"{count} Verfahren im Amtsblatt-Verzeichnis"] if count else []) + (
                [f"{planning.unattributed} nicht zuordenbar"] if planning.unattributed else [])
            meta += " · " + (" · ".join(parts) or "keine Verfahren im Amtsblatt-Verzeichnis")
        elif planning.status == "error":
            meta += " · Verfahren nicht lesbar"
        elif not preview_enabled:
            meta += " · Verfahren: Übergangslösung"
        if preview_enabled:
            meta += (f" · lokale Vorschau: {len(preview_items)} ausgewählte Publikationen" if preview.status == "ok"
                     else " · lokale Vorschau nicht lesbar")
            if any(source["error"] for source in preview.sources):
                meta += " · Vorschau-Abfrage fehlgeschlagen"
            if any(source["stale"] for source in preview.sources):
                meta += " · Vorschau veraltet"

    preview_rows = _preview_card_rows(preview, preview_items, place, today)
    if not planning.inference:
        return _regulation_card_shell(municipality, meta, _publication_card_rows(
            planning, place, local, in_force, today, loading, error, preview_enabled=preview_enabled) + preview_rows, own_note, (
            "Quellen: oereblex.ag.ch für die Vorschriften, amtsblatt.ag.ch für die "
            "Publikationen — Titel und Datum wie publiziert; ein Verfahrensschritt nur, wenn "
            "geprüft und belegt."))
    dated = [_edict_row(e, planning.approvals.get(e), today) for e in local]
    egrid = _text(row.get("egrid"))
    dated += [_revision_row(r, today, egrid) for r in planning.in_force + pending + planning.ended]
    rows = [f'<div class="detail-regulation-group">Gemeinde {municipality}</div>']
    if loading:
        rows.append(_note("Geltende Vorschriften werden abgefragt …"))
    elif error:
        rows.append(_note(f"Geltende Vorschriften nicht abrufbar: {escape(error)}. Die "
                          "Rechtsgrundlagen aus dem ÖREB-Auszug sind davon nicht betroffen.",
                          error=True))
    rows += [html for _, html in sorted(dated, key=lambda item: item[0], reverse=True)]
    if not loading and not error and not in_force:
        rows.append(_note(f"Für {municipality} ist in OEREBlex keine gültige "
                          "Rechtsvorschrift verzeichnet."))

    if planning.status == "ok":
        if planning.unattributed:
            count = planning.unattributed
            rows.append(_note(
                f'{count} {"Publikation nennt" if count == 1 else "Publikationen nennen"} '
                f'{municipality}, ohne sich einem Plan zuordnen zu lassen — im Amtsblatt '
                'prüfen.', error=True))
        elif not pending:
            # "None" is only worth saying with the period it covers.
            covered = ", ".join(filter(None, [
                f"erfasst seit {planning.since:%d.%m.%Y}" if planning.since
                else "Erfassungsbeginn nicht angegeben",
                f"Stand {planning.stand:%d.%m.%Y}" if planning.stand else "",
            ]))
            rows.append(_note(f"Keine laufenden Verfahren für {municipality} im "
                              f"Amtsblatt-Verzeichnis ({covered})."))
        if planning.canton:
            rows.append('<div class="detail-regulation-group">Kanton Aargau</div>')
            canton = [_revision_row(r, today, egrid) for r in planning.canton]
            rows += [html for _, html in sorted(canton, key=lambda item: item[0], reverse=True)]
    elif not (preview_enabled and planning.status == "off"):
        rows += _fallback_rows(planning, place, municipality)
    rows += preview_rows

    return _regulation_card_shell(municipality, meta, rows, own_note, (
        "Quellen: oereblex.ag.ch für geltende Vorschriften, amtsblatt.ag.ch für Verfahren, der "
        "ÖREB-Auszug der Parzelle für Gebietspläne. Ein Gebietsplan gilt nur für sein Gebiet; ob "
        "die Parzelle darin liegt, zeigt der Plan."))


def _regulation_card_shell(municipality, meta, rows, own_note, sources):
    note = f'<div class="detail-regulation-note">{escape(own_note)}</div>' if own_note else ""
    return (
        '<details class="detail-reference-card detail-reference-card--regulations">'
        '<summary><span class="detail-reference-summary-copy">'
        f'<span class="detail-reference-summary-title">Regulatorische Änderungen · {municipality}</span>'
        f'<span class="detail-reference-summary-meta">{escape(meta)}</span>'
        '</span><span class="detail-reference-sign" aria-hidden="true"></span></summary>'
        '<div class="detail-reference-body">'
        + "".join(rows)
        + note
        + f'<div class="detail-regulation-note">{escape(sources)}</div>'
          '</div></details>'
    )


def _fallback_rows(planning, place, municipality):
    """No publications to show: say why, and give the Amtsblatt search."""
    if planning.status == "error":
        rows = [_note(f"Verfahrensliste nicht lesbar: {escape(planning.error)}. Ob "
                      "etwas läuft, ist damit offen — direkt im Amtsblatt prüfen:", error=True)]
    elif planning.status == "uncovered":
        rows = [_note(f"Für {municipality} noch nicht erfasst — das "
                      "Amtsblatt-Verzeichnis deckt diese Gemeinde nicht ab. "
                      "Übergangslösung: direkt im Amtsblatt prüfen:")]
    else:
        rows = [_note(
            "Übergangslösung: Entwürfe, Beschlüsse und Genehmigungen aus dem Amtsblatt "
            "sind noch nicht angebunden. Bis dahin direkt im Amtsblatt prüfen — die "
            f"Suche ist auf {municipality} und den Kanton gefiltert:")]
    return rows + [_amtsblatt_links(place)]


def _publication_card_rows(planning, place, local, in_force, today, loading, error, preview_enabled=False):
    """Version 1: OEREBlex's regulations in force, then the publications as
    printed — the municipality's, then the canton-wide ones. No chain, no
    status of today, nothing matched by name."""
    municipality = escape(place)
    rows = [f'<div class="detail-regulation-group">Gemeinde {municipality} · Vorschriften '
            '(OEREBlex)</div>']
    if loading:
        rows.append(_note("Geltende Vorschriften werden abgefragt …"))
    elif error:
        rows.append(_note(f"Geltende Vorschriften nicht abrufbar: {escape(error)}. Die "
                          "Rechtsgrundlagen aus dem ÖREB-Auszug sind davon nicht betroffen.",
                          error=True))
    dated = [_edict_row(e, None, today) for e in local]
    rows += [html for _, html in sorted(dated, key=lambda item: item[0], reverse=True)]
    if not loading and not error and not in_force:
        rows.append(_note(f"Für {municipality} ist in OEREBlex keine gültige "
                          "Rechtsvorschrift verzeichnet."))
    if planning.status != "ok":
        return rows if preview_enabled and planning.status == "off" else rows + _fallback_rows(planning, place, municipality)
    rows.append(f'<div class="detail-regulation-group">Gemeinde {municipality} · Publikationen im '
                'Amtsblatt</div>')
    covered, warnings = _store_state(planning, today)
    rows += [_note(escape(warning), error=True) for warning in warnings]
    if planning.publications or planning.canton_publications:
        rows.append(_note(escape(f"Verzeichnis: {covered}.") + " " + _PUBLICATIONS_NOTE))
    rows += [_publication_row(p, today)[1] for p in planning.publications]
    if planning.unattributed:
        count = planning.unattributed
        rows.append(_note(
            f'{count} {"Publikation nennt" if count == 1 else "Publikationen nennen"} '
            f'{municipality}, ohne sich zuordnen zu lassen — im Amtsblatt prüfen.', error=True))
    elif not planning.publications:
        covered = ", ".join(filter(None, [
            f"erfasst seit {planning.since:%d.%m.%Y}" if planning.since
            else "Erfassungsbeginn nicht angegeben",
            f"Stand {planning.stand:%d.%m.%Y}" if planning.stand else ""]))
        rows.append(_note(f"Keine Publikationen für {municipality} im Amtsblatt-Verzeichnis "
                          f"({covered})."))
    if planning.canton_publications:
        rows.append('<div class="detail-regulation-group">Kanton Aargau · Publikationen im '
                    'Amtsblatt</div>')
        rows += [_publication_row(p, today)[1] for p in planning.canton_publications]
    return rows


def _preview_card_rows(preview, items, place, today=None):
    """Local review selections, with no legal-stage inference or PDF promotion."""
    if preview.status == "off":
        return []
    today = today or PP.swiss_today()
    rows = ['<div class="detail-regulation-group">Lokale Vorschau · ausgewählte Publikationen</div>']
    rows.append(_note("Nach Relevanz und Bezug zur Gemeinde ausgewählt. Diese Suchauswahl ist keine "
                      "vollständige Rechtsgeschichte. Einen bestätigten heutigen Verfahrensstand zeigt sie "
                      "nur mit einem ausdrücklich geprüften, für diese Gemeinde und diesen Tag gültigen Quellenbeleg. "
                      "Ob ein Gebietsplan diese Parzelle betrifft, muss am Original geprüft werden."))
    if preview.status == "error":
        return rows + [_note(escape(preview.error), error=True)]
    if not preview.municipality_queried:
        rows.append(_note(f"Für {escape(place)} wurde die Gemeindesuche noch nicht erfolgreich abgefragt. "
                          "Allfällige kantonale Publikationen sind separat ausgewählt."))
    for source in preview.sources:
        selection = source["selection"]
        if selection["provider"] == "amtsblatt.ag.ch":
            label = ("Gemeindepublikationen" if selection["kind"] == "municipal" else "Kantonsgenehmigungen") + f" ({selection['municipality']})"
        else:
            label = "Anhörungen Kanton Aargau" + (" (Archiv)" if selection["mode"] == "archive" else "")
        success = source["last_success_at"]
        text = f"{label}: zuletzt erfolgreich abgefragt {success:%d.%m.%Y %H:%M} UTC." if success else f"{label}: bisher keine erfolgreiche Abfrage."
        if source["empty"]:
            text += " Die letzte Suchauswahl lieferte keine Treffer; daraus folgt kein Rechtsstatus."
        if source["stale"]:
            text += " Suchauswahl älter als sieben Tage; neuere Publikationen können fehlen."
        if source["error"]:
            text += f" Letzte Abfrage am {source['last_attempt_at']:%d.%m.%Y %H:%M} UTC fehlgeschlagen; die letzten gültigen Daten bleiben erhalten."
        rows.append(_note(escape(text), error=bool(source["error"] or source["stale"])))
    if preview.pending or preview.changed:
        rows.append(_note(escape(f"Noch zu prüfen: {preview.pending} Publikationen. "
                                 f"Nach Quellenänderung erneut zu prüfen: {preview.changed}. "
                                 "Diese Einträge werden nicht als relevante Publikationen gezeigt.")))
    for item in items:
        candidate = item.candidate
        canton = candidate["source_id"]["namespace"] == PP.NS_CANTON
        published = datetime.fromisoformat(candidate["publication_date"].replace("Z", "+00:00")).date() if canton else date.fromisoformat(candidate["published_on"])
        label = "ag.ch · Anhörung" if canton else "Amtsblatt"
        details = [escape(f"{label} · publiziert {published:%d.%m.%Y}"), escape(candidate["authority"])]
        if candidate.get("publication_version_label"):
            details.append(escape("Quellenhinweis: " + candidate["publication_version_label"]))
        if item.absent:
            details.append(escape("In einer zuletzt erfolgreich abgefragten Suchauswahl nicht mehr enthalten — kein Nachweis einer Genehmigung oder Aufhebung."))
        href = _safe_href(PP.source_href(candidate))
        source_label = f"Originale Anhörung auf ag.ch öffnen: {candidate['title']}" if canton else f"Amtsblatt-Publikation öffnen: {candidate['title']}"
        link = (f'<a class="detail-regulation-open{" detail-regulation-open--search" if canton else ""}" '
                f'href="{href}" target="_blank" rel="noopener noreferrer" '
                f'title="{escape(source_label, quote=True)}" aria-label="{escape(source_label, quote=True)}">'
                f'{"Link ↗" if canton else "↗"}</a>') if href else ""
        badge = '<span class="detail-regulation-status detail-regulation-status--step" title="Relevanz und Gemeinde-Bezug geprüft; kein bestätigter Rechtsstatus">Publikation</span>'
        if item.verified is not None:
            try:
                proof = item.verified
                raw = {"stage": proof.stage, "source": proof.source, "verified_on": proof.verified_on.isoformat(), "method": proof.method}
                raw.update({name: getattr(proof, name).isoformat() for name in PL._STAGE_DATES if getattr(proof, name) is not None})
                PP._proof(raw, candidate)
                status, note = PL.stage_badge(item, today)
                if status:
                    badge = _status_badge(status)
                if note:
                    details.append(escape(note))
                proof_href = _safe_href(proof.source)
                if proof_href:
                    details.append(f'<a href="{proof_href}" target="_blank" rel="noopener noreferrer">Geprüfter Quellenbeleg ↗</a>')
            except (PP.C.CollectionError, AttributeError, TypeError, ValueError):
                pass  # An unsupported direct-view proof earns no legal badge.
        rows.append(_regulation_row(f"{published:%d.%m.%Y}", candidate["title"], details, "", "", "publiziert", badge=badge, source_link=link))
    if not items:
        rows.append(_note("Keine freigegebenen Publikationen für diese Gemeinde in der lokalen Vorschau. "
                          "Ungeprüfte und zurückgestellte Treffer bleiben in der Prüfung; dies bedeutet nicht, dass keine Änderungen geplant sind."))
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


def _planning_rows(planning, municipality, today=None):
    """What the sheet says about procedures under way — in each of the three
    states, because "not tracked", "could not read" and "none" are different
    answers on paper too."""
    link = AB.municipal_planning_link(municipality) if municipality else AB.BASE
    if planning is None or planning.status == "off":
        return [("Verfahren (Amtsblatt)", "nicht automatisch erfasst (Übergangslösung) – "
                                       f"im Amtsblatt prüfen: {link}")]
    if planning.status == "uncovered":
        return [("Verfahren (Amtsblatt)",
                 f"für diese Gemeinde nicht erfasst – im Amtsblatt prüfen: {link}")]
    if planning.status == "error":
        return [("Verfahren (Amtsblatt)",
                 f"Verfahrensliste nicht lesbar ({planning.error}) – im Amtsblatt prüfen: {link}")]

    if not planning.inference:
        # Use the card's Swiss calendar date even when UTC is still yesterday.
        today = today or PP.swiss_today()

        def printed(pubs, label):
            # A row each, so that a long list runs over pages: reportlab cannot
            # split one row, and sixty in one cell are taller than a page.
            return [(label if i == 0 else "", _printed_publication(p, today)) for i, p in enumerate(pubs)]

        covered, warnings = _store_state(planning, today)
        rows = [("Verzeichnis (Amtsblatt)", " ".join([covered + "."] + warnings))]
        if planning.publications:
            rows += printed(planning.publications, "Publikationen (Amtsblatt)")
        if planning.unattributed:
            rows.append(("Nicht zuordenbar" if planning.publications else "Publikationen (Amtsblatt)",
                         f"{planning.unattributed} nicht zuordenbare Publikation(en) – "
                         f"im Amtsblatt prüfen: {link}"))
        elif not planning.publications:
            covered = ", ".join(filter(None, [
                f"erfasst seit {planning.since:%d.%m.%Y}" if planning.since
                else "Erfassungsbeginn nicht angegeben",
                f"Stand {planning.stand:%d.%m.%Y}" if planning.stand else ""]))
            rows.append(("Publikationen (Amtsblatt)", f"keine im Amtsblatt-Verzeichnis ({covered})"))
        if planning.canton_publications:
            rows += printed(planning.canton_publications, "Kanton (Amtsblatt)")
        if planning.publications or planning.canton_publications:
            rows.append(("Hinweis zu den Publikationen",
                         "Titel und Datum wie publiziert — nicht der heutige Stand des "
                         "Verfahrens; ein Schritt nur, wenn an einer Quelle geprüft und am "
                         "Druckdatum belegt"))
        return rows

    def dates(r):
        return ", ".join(filter(None, [
            f"Entscheid vom {r.decided_on:%d.%m.%Y}" if r.decided_on else "",
            f"publiziert {r.published_on:%d.%m.%Y}",
            (f"in Kraft {'seit' if r.status == 'in_kraft' else 'ab'} {r.effective_on:%d.%m.%Y}"
             if r.effective_on and r.status in ("in_kraft", "genehmigt", "inkraft_festgelegt")
             else ""),
            f"Inkrafttreten vorgesehen ab {r.planned_on:%d.%m.%Y}" if r.planned_on else "",
            (f"ÖREB-Auszug vom {r.oereb.checked_on:%d.%m.%Y}"
             if r.oereb and r.oereb.checked_on else "")]))

    def listed(revisions):
        # Plain text: these values reach the PDF's markup, and a title from
        # a data file must not turn into a link there.
        return "; ".join(
            re.sub(r"[\[\]*]", "", f"{r.status_label}: {r.title} ({r.scope_label}, {dates(r)})")
            for r in revisions)

    rows = []
    if planning.in_force:
        # In force by a date the Amtsblatt gives, possibly before OEREBlex
        # lists it: on paper, too, the BNO above is not the whole of what is
        # in force. The parcel's ÖREB extract never puts a plan here.
        rows.append(("In Kraft getreten (Amtsblatt)", listed(planning.in_force)))
    pending = planning.pending()
    if pending:
        rows.append(("Verfahren (Amtsblatt)", listed(pending)))
    if planning.unattributed:
        # Beside the list too: without it, the list reads as complete.
        rows.append(("Nicht zuordenbar" if pending else "Verfahren (Amtsblatt)",
                     f"{planning.unattributed} nicht zuordenbare Publikation(en) – "
                     f"im Amtsblatt prüfen: {link}"))
    elif not pending:
        covered = ", ".join(filter(None, [
            f"erfasst seit {planning.since:%d.%m.%Y}" if planning.since
            else "Erfassungsbeginn nicht angegeben",
            f"Stand {planning.stand:%d.%m.%Y}" if planning.stand else ""]))
        rows.append(("Verfahren (Amtsblatt)", f"keine im Amtsblatt-Verzeichnis ({covered})"))
    if planning.ended:
        rows.append(("Beendet (Amtsblatt)", listed(planning.ended)))
    if planning.canton:
        rows.append(("Kanton (Amtsblatt)", listed(planning.canton)))
    return rows


def _regulation_block(own, own_note, news_error, *, planning=None, municipality="",
                      edicts=(), today=None):
    """The part of block E that belongs on paper.

    What the card shows, as text: the parcel's own regulation and an edition
    approved for later, its municipality's procedures and the canton-wide
    changes — never another municipality's. A printed
    analysis that does not say which edition of the building regulation it
    assumed — and whether a revision was under way — cannot be checked a year
    later, which is why the not-found and could-not-ask cases print a line of
    their own instead of the block quietly disappearing.
    """
    if news_error:
        rows = [("Nicht abrufbar", f"OEREBlex antwortete nicht: {news_error}")]
    elif not own:
        rows = [("Rechtsvorschrift", "in OEREBlex keine gültige Vorschrift für "
                                     "diese Gemeinde verzeichnet")]
    else:
        approval = planning.approvals.get(own) if planning is not None and planning.inference else None
        rows = [(own.label or "Rechtsvorschrift", " ".join(filter(None, [
            f"in Kraft seit {own.when}",
            f"· Genehmigung publiziert {approval.published_on:%d.%m.%Y}" if approval else "",
            own.document])))]
        if own_note:
            rows.append(("Hinweis", own_note))
    # An edition OEREBlex lists with its date ahead: on the card, so on paper.
    # The sheet and card must use the same Swiss calendar date.
    today = today or PP.swiss_today()
    approvals = planning.approvals if planning is not None and planning.inference else {}
    rows += [(edict.label or "Rechtsvorschrift", " ".join(filter(None, [
                 f"in Kraft ab {edict.when} (OEREBlex)",
                 f"· Genehmigung publiziert {approvals[edict].published_on:%d.%m.%Y}"
                 if edict in approvals else "",
                 edict.document])))
             for edict in edicts if edict.in_force > today]
    return ("Stand der Rechtsvorschrift", rows + _planning_rows(planning, municipality, today))


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
OVERRIDE_STORE = "calculation_overrides"  # {parcel: {rule_key: CHF magnitude}}

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


def _team_load(pid, db):
    import organisation
    import scope_auth
    import shared_calculations as shared
    if not scope_auth.enabled() or not organisation.load_profile(db)["shared_calculations"]:
        return False
    try:
        saved = shared.load(db, pid)
        revision_key = f"team_revision::{pid}"
        st.session_state.setdefault(revision_key, saved["revision"] if saved else 0)
        st.caption("Team-Kalkulation · Änderungen bleiben ein persönlicher Entwurf, bis Sie speichern.")
        if saved:
            st.caption(f"Team-Version {saved['revision']} · gespeichert {saved['updated_at']} UTC")
            if st.button("Team-Version laden", key=f"team_load::{pid}"):
                for name, value in saved["payload"]["inputs"].items():
                    _remember(pid, name, value)
                    st.session_state[_widget_key(pid, name)] = value
                st.session_state.setdefault(OVERRIDE_STORE, {})[pid] = saved["payload"]["overrides"]
                st.session_state[revision_key] = saved["revision"]
                st.success("Team-Version geladen. Ihre bisherigen Annahmen wurden ersetzt.")
        return True
    except (scope_auth.AuthError, ValueError) as error:
        st.error(str(error))
        return False


def _team_save(pid, db):
    import scope_auth
    import shared_calculations as shared
    member = scope_auth.current(db)
    if st.button("Für das Team speichern", key=f"team_save::{pid}",
                 disabled=member["role"] not in ("Inhaber", "Bearbeiter")):
        payload = {"inputs": {name: _recall(pid, name, None)
                              for name in (*OWN, "gf", "demolish")},
                   "overrides": st.session_state.get(OVERRIDE_STORE, {}).get(pid, {})}
        try:
            version = shared.save(db, pid, payload, st.session_state[f"team_revision::{pid}"])
            st.session_state[f"team_revision::{pid}"] = version
            st.success(f"Team-Version {version} gespeichert.")
        except (scope_auth.AuthError, ValueError) as error:
            st.error(str(error))


def forget(pid):
    """Reset C's benchmarks and this parcel's overrides, keeping B's inputs.

    Rates are shared across the session, as before. Potential, unit size and
    the parcel's replacement decision are independent of a cost-rate reset.
    """
    rates = set(OWN) - {"unit"}
    for name in rates:
        st.session_state.get(OWN_STORE, {}).pop(name, None)
        st.session_state.pop(_widget_key(pid, name), None)
    st.session_state.get(OVERRIDE_STORE, {}).pop(pid, None)


def apply_calculation_event(event, pid, state=None):
    """Validate a single edit against the open parcel, before changing state."""
    target = st.session_state if state is None else state
    if not isinstance(event, dict) or event.get("parcel") != pid:
        return False
    if event.get("type") != "override":
        return False
    key = event.get("field")
    if not isinstance(key, str) or key not in E.OVERRIDE_KEYS:
        raise ValueError("Diese Position kann nicht überschrieben werden.")
    value = E.parse_override(event.get("value"))
    manual = dict(target.get(OVERRIDE_STORE, {}).get(pid, {}))
    if value is None:
        manual.pop(key, None)
    else:
        manual[key] = value
    target.setdefault(OVERRIDE_STORE, {})[pid] = manual
    return True


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
  .st-key-detail_page {
      max-width:1124px; margin-inline:auto; padding-top:6px;
      padding-bottom:20px; }
  .st-key-detail_breadcrumb { min-height:14px; height:14px; margin:0 0 2px;
      align-items:center; gap:8px; }
  .st-key-detail_breadcrumb > [data-testid="stElementContainer"],
  .st-key-detail_breadcrumb [data-testid="stButton"],
  .st-key-detail_breadcrumb [data-testid="stHtml"] {
      min-height:14px; height:14px; line-height:14px; }
  .st-key-detail_breadcrumb [data-testid="stButton"] button { min-height:0;
      height:14px; padding:0; border:0; background:transparent; color:#1c4e4a;
      font-size:11.5px; line-height:14px; }
  .st-key-detail_breadcrumb [data-testid="stButton"]
      [data-testid="stMarkdownContainer"],
  .st-key-detail_breadcrumb [data-testid="stButton"] p {
      font-size:11.5px; line-height:14px; }
  .detail-breadcrumb-trail { height:14px; display:flex; align-items:center;
      gap:8px; color:#9a9aa6; font-size:11.5px; line-height:14px; }
  .detail-breadcrumb-current { color:#4a4a54; }
  .st-key-detail_header { margin:0 0 8px; }
  .st-key-detail_header > [data-testid="stHorizontalBlock"] {
      align-items:flex-start; gap:24px; }
  .st-key-detail_header [data-testid="stColumn"]:first-child
      [data-testid="stVerticalBlock"] { gap:0; }
  .st-key-detail_header [data-testid="stHeadingWithActionElements"] {
      min-height:26px; height:26px; margin:0; padding:0; }
  .st-key-detail_header [data-testid="stHeading"]
      [data-testid="stMarkdownContainer"] {
      height:26px; margin:0 !important; }
  .st-key-detail_header [data-testid="stHeadingWithActionElements"] h1 {
      margin:0; padding:0; font-size:21px; line-height:26px;
      font-weight:600; letter-spacing:-.015em; }
  .detail-header-meta { min-height:18px; margin-top:7px; display:flex;
      align-items:center; gap:9px; flex-wrap:wrap; }
  .detail-header-updated { color:#9a9aa6; font-size:11.5px; line-height:14px; }
  .detail-status-pill { display:inline-flex; align-items:center; padding:4px 8px;
      border-radius:20px; font-size:10.5px; font-weight:500; line-height:1;
      white-space:nowrap; }
  .detail-status-pill.clear { background:#eef6f0; color:#2f6b45; }
  .detail-status-pill.dev { background:#eef2fb; color:#33538f; }
  .detail-status-pill.heritage { background:#f2f0fb; color:#4e4899; }
  .detail-status-pill.warning { background:#fdf5e7; color:#8a5a12; }
  .st-key-detail_actions [data-testid="stHorizontalBlock"] {
      align-items:flex-start; justify-content:flex-end; gap:8px; }
  .st-key-detail_actions [data-testid="stColumn"] {
      flex:0 0 auto !important; width:auto !important; min-width:0 !important; }
  .st-key-detail_actions button { min-height:30px; height:30px; padding:0 12px;
      border-radius:6px; font-size:12px; font-weight:500; line-height:14px;
      white-space:nowrap; }

  .st-key-result_bar { padding:0; margin-bottom:10px; }
  .detail-result-grid { border:1px solid #dde9e7; border-radius:10px;
      background:#fbfdfd; padding:22px 24px; display:grid;
      grid-template-columns:minmax(0,1.35fr) minmax(0,1fr) minmax(0,1fr);
      gap:28px; align-items:end; }
  .detail-result-cell + .detail-result-cell {
      border-left:1px solid #eaeaee; padding-left:22px; }
  .detail-result-label { margin-bottom:9px; color:#8a8a94; font-size:10px;
      font-weight:600; line-height:12px; letter-spacing:.1em;
      text-transform:uppercase; }
  .detail-result-primary-value { display:flex; align-items:baseline; gap:9px; }
  .detail-result-currency { color:#77777f; font-family:"IBM Plex Mono",monospace;
      font-size:15px; font-weight:500; line-height:1; }
  .detail-result-hero { font-family:"IBM Plex Mono",monospace; font-size:42px;
      font-weight:600; line-height:1; letter-spacing:-.02em;
      font-variant-numeric:tabular-nums; }
  .detail-result-value { font-family:"IBM Plex Mono",monospace; font-size:22px;
      font-weight:600; line-height:1; font-variant-numeric:tabular-nums; }
  .detail-result-value.reference { color:#4a4a54; }
  .detail-result-note { margin-top:9px; color:#9a9aa6; font-size:11.5px;
      line-height:14px; }
  .detail-result-comparison { margin-top:9px; display:flex; align-items:center;
      gap:7px; }
  .detail-result-comparison .detail-result-note { margin-top:0; }
  .detail-result-delta { display:inline-flex; align-items:center; padding:3px 8px;
      border-radius:20px; font-size:10.5px; font-weight:500; line-height:1; }
  .detail-result-delta.positive { background:#eef6f0; color:#2f6b45; }
  .detail-result-delta.negative { background:#fbf0ef; color:#8a4a44; }

  .st-key-inputs_b { gap:0; overflow:hidden; margin-bottom:20px; padding:0;
      border:1px solid #eaeaee; border-radius:9px; background:#fff; }
  .detail-potential-head { display:flex; align-items:center;
      justify-content:space-between; padding:12px 16px;
      border-bottom:1px solid #f0f0f3; }
  .detail-potential-title { color:#8a8a94; font-size:10px; font-weight:600;
      line-height:12px; letter-spacing:.1em; text-transform:uppercase; }
  .st-key-inputs_b_body { gap:0 !important; padding:16px !important; }
  .st-key-inputs_b_body > [data-testid="stHorizontalBlock"] { gap:14px; }
  .st-key-inputs_b_body [data-testid="stColumn"]
      > [data-testid="stVerticalBlock"] { gap:6px; }
  .st-key-inputs_b [data-testid="stNumberInput"] { gap:6px; }
  .st-key-inputs_b [data-testid="stWidgetLabel"] { height:auto; margin:0; }
  .st-key-inputs_b [data-testid="stWidgetLabel"] p { color:#8a8a94;
      font-size:10px; font-weight:600; line-height:12px; letter-spacing:.07em;
      text-transform:uppercase; }
  .st-key-inputs_b [data-testid="stNumberInputContainer"] { height:32px;
      min-height:32px; border:1px solid #dcdce4; border-radius:6px;
      background:#fff; box-shadow:none; }
  .st-key-inputs_b [data-testid="stNumberInputContainer"]:focus-within {
      border-color:#1c4e4a; box-shadow:0 0 0 3px #e2eceb; }
  .st-key-inputs_b [data-testid="stNumberInputContainer"] > div:has(
      > [data-testid="stNumberInputStepDown"]),
  .st-key-inputs_b [data-testid="stNumberInputContainer"] > div:has(
      > [data-testid="stNumberInputStepUp"]),
  .st-key-inputs_b [data-testid="stNumberInputContainer"] button {
      display:none; }
  .st-key-inputs_b [data-testid="stNumberInput"] input { height:30px;
      min-height:30px; padding:0 10px; border:0; background:#fff;
      font-family:"IBM Plex Mono",monospace; font-size:13px; line-height:30px;
      text-align:right; font-variant-numeric:tabular-nums; }
  .detail-input-hint { color:#b0b0b8; font-size:10.5px; line-height:14px; }
  .detail-potential-result { display:flex; align-items:flex-end;
      justify-content:space-between; gap:16px; margin-top:16px; padding-top:14px;
      border-top:1px solid #f0f0f3; }
  .detail-potential-result-label { color:#8a8a94; font-size:10px;
      font-weight:600; line-height:12px; letter-spacing:.07em;
      text-transform:uppercase; }
  .detail-potential-formula { margin-top:6px; color:#9a9aa6;
      font-size:11.5px; line-height:14px; }
  .detail-potential-units { color:#17171b; font-family:"IBM Plex Mono",monospace;
      font-size:26px; font-weight:600; line-height:1;
      font-variant-numeric:tabular-nums; }
  .detail-replacement-note { margin-top:16px; padding:11px 12px;
      border:1px solid #f2ebdc; border-radius:7px; background:#fdfaf3;
      color:#7a6533; font-size:11.5px; line-height:16px; text-wrap:pretty; }
  .st-key-inputs_c { gap:0; overflow:hidden; margin:0 0 20px; padding:0;
      border:1px solid #eaeaee; border-radius:9px; background:#fff; }
  .st-key-inputs_c_header { gap:10px !important; padding:12px 16px !important;
      border-bottom:1px solid #f0f0f3; align-items:center; flex-wrap:nowrap !important; }
  .st-key-inputs_c_header > [data-testid="stElementContainer"] {
      width:auto !important; flex:0 0 auto !important; }
  .st-key-inputs_c_header > [data-testid="stElementContainer"]:first-child {
      flex:1 1 0 !important; margin-right:auto; min-width:180px; }
  .detail-calculation-title { color:#8a8a94; font-size:10px; font-weight:600;
      line-height:12px; letter-spacing:.1em; text-transform:uppercase; }
  .detail-calculation-meta { display:flex; align-items:center;
      justify-content:flex-end; gap:10px; white-space:nowrap; }
  .detail-calculation-hint { color:#b0b0b8; font-size:11px; line-height:14px; }
  .st-key-inputs_c_header [data-testid="stButton"] button { min-height:24px;
      height:24px; padding:0; border:0; background:none; box-shadow:none;
      color:#77777f; font-size:11.5px; font-weight:400; white-space:nowrap; }
  .st-key-inputs_c_header [data-testid="stButton"] button:hover {
      border:0; background:none; color:#17171b; }
  .st-key-inputs_c_header [class*="st-key-"][class*="overrides-reset"] button {
      padding:0 9px; border:1px solid #cfe0de; border-radius:20px;
      background:#f2f7f6; color:#143a37; font-size:10.5px; }
  .st-key-inputs_c_body { gap:14px !important; padding:16px !important;
      border-bottom:1px solid #f0f0f3; }
  .st-key-inputs_c_body > [data-testid="stHorizontalBlock"] { gap:16px; }
  .st-key-inputs_c_body [data-testid="stColumn"]
      > [data-testid="stVerticalBlock"] { gap:6px; }
  .st-key-inputs_c_body [data-testid="stNumberInput"] { gap:6px; }
  .st-key-inputs_c_body [data-testid="stNumberInput"]
      [data-testid="stWidgetLabel"] { height:auto; margin:0; }
  .st-key-inputs_c_body [data-testid="stNumberInput"]
      [data-testid="stWidgetLabel"] p { color:#8a8a94; font-size:10px;
      font-weight:600; line-height:12px; letter-spacing:.07em;
      text-transform:uppercase; }
  .st-key-inputs_c_body [data-testid="stWidgetLabel"] button,
  .st-key-inputs_c_body [data-testid="stNumberInputContainer"] > div:has(
      > [data-testid="stNumberInputStepDown"]),
  .st-key-inputs_c_body [data-testid="stNumberInputContainer"] > div:has(
      > [data-testid="stNumberInputStepUp"]),
  .st-key-inputs_c_body [data-testid="stNumberInputContainer"] button {
      display:none; }
  .st-key-inputs_c_body [data-testid="stNumberInputContainer"] { height:32px;
      min-height:32px; border:1px solid #dcdce4; border-radius:6px;
      background:#fff; box-shadow:none; }
  .st-key-inputs_c_body [data-testid="stNumberInputContainer"]:focus-within {
      border-color:#1c4e4a; box-shadow:0 0 0 3px #e2eceb; }
  .st-key-inputs_c_body [data-testid="stNumberInput"] input { height:30px;
      min-height:30px; padding:0 10px; border:0; background:#fff;
      font-family:"IBM Plex Mono",monospace; font-size:13px; line-height:30px;
      text-align:right; font-variant-numeric:tabular-nums; }
  .st-key-inputs_c_body [data-testid="stCheckbox"] { padding-bottom:5px; }
  .st-key-inputs_c_body [data-testid="stCheckbox"] p { color:#77777f;
      font-size:10.5px; line-height:14px; }
  .detail-edit-pill { display:inline-flex; align-items:center; padding:3px 9px;
      border-radius:20px; background:#e8f0ef; color:#143a37; font-size:10.5px; }
  .detail-edit-pill.readonly { background:#f4f4f6; color:#8a8a94; }
  [data-testid="stHorizontalBlock"]:has(.st-key-facts_a) {
      align-items:flex-start; gap:20px; }
  .st-key-facts_a { margin-bottom:20px; padding:0; border:0; background:transparent; }
  .detail-facts-card { overflow:hidden; border:1px solid #eaeaee;
      border-radius:9px; background:#fff; }
  .detail-facts-head { display:flex; align-items:center; justify-content:space-between;
      padding:12px 16px; border-bottom:1px solid #f0f0f3; }
  .detail-facts-title { color:#8a8a94; font-size:10px; font-weight:600;
      line-height:12px; letter-spacing:.1em; text-transform:uppercase; }
  .detail-facts-body { padding:4px 16px 12px; }
  .detail-fact-row { display:flex; align-items:baseline; justify-content:space-between;
      gap:16px; padding:8px 0; border-bottom:1px solid #f5f5f7; }
  .detail-fact-label { flex:none; color:#8a8a94; font-size:12px;
      line-height:16px; }
  .detail-fact-value { max-width:65%; color:#17171b; font-size:12.5px;
      line-height:17px; text-align:right; overflow-wrap:anywhere; }
  .detail-fact-value.mono { font-family:"IBM Plex Mono",monospace;
      font-variant-numeric:tabular-nums; }
  .detail-fact-row.warning .detail-fact-label,
  .detail-fact-row.warning .detail-fact-value { color:#8a5a12; }
  .detail-facts-source { padding-top:11px; color:#b0b0b8; font-size:11px;
      line-height:15px; text-wrap:pretty; }
  [class*="st-key-facts_"] table thead { display:none; }

  [class*="st-key-facts_"] table { width:100%; }
  [class*="st-key-facts_"] table td, [class*="st-key-facts_"] table th {
      border-left:0; border-right:0; }
  [class*="st-key-facts_"] td, [class*="st-key-facts_"] th {
      overflow-wrap:break-word; }
  [class*="st-key-facts_"] [data-testid="stMarkdownContainer"] {
      overflow-x:auto; }

  .st-key-inputs_c .calc__scroll { margin:0; }
  .st-key-inputs_c table.calc { width:100%; min-width:760px; }

  .detail-reference-card { margin:0 0 12px; border:1px solid #eaeaee;
      border-radius:9px; background:#fff; overflow:hidden; }
  .detail-reference-stack { display:block; }
  .st-key-detail_references { gap:0 !important; }
  .st-key-detail_legal { position:relative; gap:0 !important; }
  .st-key-detail_legal .detail-reference-card > summary { padding-right:164px; }
  .st-key-detail_sources_download { position:absolute; top:8px; right:16px;
      width:max-content !important; }
  .st-key-detail_sources_download button { min-height:27px; height:27px;
      padding:0 10px; border-radius:5px; font-size:11.5px; }
  .st-key-detail_sources_download button p { font-size:11.5px; }
  .detail-reference-card--regulations { margin-bottom:0; }
  .detail-reference-card > summary { display:flex; align-items:center;
      justify-content:space-between; gap:12px; min-height:44px; padding:0 16px;
      color:#17171b; cursor:pointer; list-style:none; }
  .detail-reference-card > summary::-webkit-details-marker { display:none; }
  .detail-reference-summary-copy { display:flex; align-items:baseline; gap:10px;
      min-width:0; flex-wrap:wrap; }
  .detail-reference-summary-title { font-size:12.5px; font-weight:500;
      line-height:16px; }
  .detail-reference-summary-meta { color:#b0b0b8; font-size:11.5px;
      line-height:15px; }
  .detail-reference-sign::before { content:'+'; color:#9a9aa6;
      font-family:"IBM Plex Mono",monospace; font-size:11px; }
  .detail-reference-card[open] > summary .detail-reference-sign::before {
      content:'−'; }
  .detail-reference-body { padding:2px 16px 16px;
      border-top:1px solid #f0f0f3; }
  .detail-reference-row { display:grid;
      grid-template-columns:minmax(140px,200px) minmax(0,1fr) minmax(80px,auto);
      gap:16px; align-items:center; padding:10px 0;
      border-bottom:1px solid #f5f5f7; }
  .detail-reference-type, .detail-reference-title { color:#17171b;
      font-size:12px; font-weight:500; line-height:16px; }
  .detail-reference-detail { margin-top:3px; color:#77777f;
      font-size:12px; line-height:16px; text-wrap:pretty; }
  .detail-reference-actions { display:flex; justify-content:flex-end; gap:6px;
      color:#b0b0b8; font-size:11px; }
  .detail-reference-action { display:inline-flex; align-items:center; height:25px;
      padding:0 9px; border:1px solid #e2e2e8; border-radius:5px; color:#3a3a44;
      font-size:11px; font-weight:500; line-height:1; text-decoration:none; }
  .detail-reference-action:hover { border-color:#c9c9d2; color:#17171b; }
  .detail-reference-empty { padding:13px 0; color:#9a9aa6;
      font-size:11.5px; line-height:16px; }
  .detail-reference-empty--error { color:#8a4a44; }
  .detail-assumptions { padding-top:13px; }
  .detail-assumptions-title { color:#8a8a94; font-size:10px; font-weight:600;
      line-height:12px; letter-spacing:.08em; text-transform:uppercase; }
  .detail-assumptions ul { display:grid; grid-template-columns:1fr 1fr; gap:5px 24px;
      margin:9px 0 0; padding-left:18px; color:#77777f; font-size:11px;
      line-height:15px; }
  .detail-regulation-row { display:grid;
      grid-template-columns:96px minmax(0,1fr) 172px 64px; gap:16px;
      align-items:center; padding:11px 0; border-bottom:1px solid #f5f5f7; }
  .detail-regulation-date { color:#9a9aa6; font-family:"IBM Plex Mono",monospace;
      font-size:11.5px; font-variant-numeric:tabular-nums; }
  .detail-regulation-status--step { background:#ffffff; color:#55555f;
      box-shadow:inset 0 0 0 1px #d8d8de; font-style:normal; }
  .detail-regulation-date-kind { display:block; font-family:inherit; font-size:10px;
      letter-spacing:.02em; color:#b4b4be; }
  .detail-regulation-row .detail-reference-detail a { color:inherit; }
  /* Philipp's status colours (2026-10-05): a draft amber, an approval yellow,
     what is in force grey; a decision awaiting approval and anything
     unverified stay uncoloured. */
  .detail-regulation-status { justify-self:end; max-width:100%; padding:2px 8px;
      border-radius:20px; font-size:10.5px; font-weight:500; line-height:15px;
      text-align:right; }
  .detail-regulation-status--amber { background:#fdecd3; color:#8a4f0b; }
  .detail-regulation-status--yellow { background:#fbf3c6; color:#6b5a06; }
  .detail-regulation-status--grey { background:#ededf0; color:#55555f; }
  .detail-regulation-status--neutral { background:#ffffff; color:#4a4a54;
      box-shadow:inset 0 0 0 1px #dcdce3; }
  .detail-regulation-status--open { background:#ffffff; color:#77777f;
      outline:1px dashed #c4c4cd; outline-offset:-1px; }
  .detail-regulation-arrow { display:flex; justify-content:flex-end; }
  .detail-regulation-open { display:inline-flex; align-items:center;
      justify-content:center; width:26px; height:26px; border:1px solid #e2e2e8;
      border-radius:5px; color:#3a3a44; font-size:13px; line-height:1;
      text-decoration:none; }
  .detail-regulation-open:hover { border-color:#c9c9d2; color:#17171b; }
  /* A search is not the publication: it says so, in words. */
  .detail-regulation-open--search { width:auto; padding:0 7px; font-size:11px;
      white-space:nowrap; }
  .detail-regulation-note { margin-top:12px; max-width:90ch; color:#b0b0b8;
      font-size:11px; line-height:15px; text-wrap:pretty; }
  .detail-regulation-group { margin-top:16px; color:#8a8a94; font-size:10px;
      font-weight:600; line-height:12px; letter-spacing:.08em;
      text-transform:uppercase; }
  .detail-regulation-group:first-child { margin-top:12px; }
  .detail-regulation-links { margin:0; padding:0 0 4px 18px; color:#77777f;
      font-size:11.5px; line-height:20px; }
  .detail-regulation-links a { color:#3a3a44; }
  .detail-final-note { margin:20px 0 0; max-width:80ch; color:#b0b0b8;
      font-size:11px; line-height:15px; text-wrap:pretty; }

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
    .detail-result-grid { grid-template-columns:1fr; gap:18px; }
    .detail-result-cell + .detail-result-cell { border-left:0; padding-left:0;
        border-top:1px solid #eaeaee; padding-top:18px; }
    [data-testid="stHorizontalBlock"]:has(.st-key-facts_a) {
        flex-wrap:wrap; }
    [data-testid="stHorizontalBlock"]:has(.st-key-facts_a)
        > [data-testid="stColumn"] { min-width:100%; }
    .st-key-inputs_c_header { flex-wrap:wrap !important; }
    .st-key-inputs_c_header > [data-testid="stElementContainer"]:first-child {
        flex-basis:100% !important; }
    .st-key-inputs_c_body > [data-testid="stHorizontalBlock"] { flex-wrap:wrap; }
    .st-key-inputs_c_body > [data-testid="stHorizontalBlock"]
        > [data-testid="stColumn"] { min-width:calc(50% - 8px); }
    .detail-reference-row { grid-template-columns:1fr; gap:5px; }
    .detail-reference-actions { justify-content:flex-start; }
    .detail-regulation-row { grid-template-columns:82px minmax(0,1fr) auto;
        gap:8px 12px; }
    .detail-regulation-row > .detail-regulation-arrow { grid-column:3; grid-row:1; }
    .detail-regulation-row > .detail-regulation-status { grid-column:2; grid-row:2;
        justify-self:start; text-align:left; }
    .detail-assumptions ul { grid-template-columns:1fr; }
  }
</style>
"""

CALC_CSS = """
<style>
  div.calc__scroll { overflow-x:auto; margin:0; }
  table.calc { width:100%; min-width:760px; margin:0; border-collapse:collapse; }
  table.calc th, table.calc td { border:0; border-bottom:1px solid #f4f4f7;
      text-align:left; }
  table.calc thead th { padding:9px 16px; border-bottom:1px solid #ebebef;
      background:#fafafb; color:#8a8a94; font-size:9.5px; font-weight:600;
      line-height:12px; letter-spacing:.08em; text-transform:uppercase; }
  table.calc tbody th { padding:9px 16px; color:#17171b; font-size:12.5px;
      font-weight:500; line-height:16px; }
  table.calc td.calc__formula { padding:9px 16px; color:#8a8a94;
      font-family:"IBM Plex Mono",monospace; font-size:11.5px; line-height:16px;
      font-variant-numeric:tabular-nums; }
  table.calc .calc__amount { width:190px; padding:9px 16px; text-align:right;
      font-family:"IBM Plex Mono",monospace; font-size:12.5px; line-height:16px;
      font-variant-numeric:tabular-nums; white-space:nowrap; }
  table.calc tr.calc__result th, table.calc tr.calc__result td {
      padding-top:13px; padding-bottom:13px; border-top:1px solid #e4e4ea;
      border-bottom:0; background:#fafafb; font-weight:600; }
  table.calc tr.calc__result th { font-size:13px; }
  table.calc tr.calc__result td.calc__amount { font-size:15px; }
  table.calc tr.calc__sqm th, table.calc tr.calc__sqm td { padding-top:9px;
      padding-bottom:9px; border-bottom:0; }
  table.calc tr.calc__sqm th { color:#77777f; font-size:12px; font-weight:400; }
  table.calc tr.calc__sqm td.calc__formula { color:#b0b0b8; }
  table.calc tr.calc__sqm td.calc__amount { color:#4a4a54; font-size:12px; }
  .calc__edit { display:inline-block; padding:2px 6px; margin:-2px -6px;
      border:0; border-bottom:1px dashed #d8d8de; border-radius:4px;
      background:transparent; color:inherit; font:inherit; cursor:text; }
  .calc__edit--overridden { background:#e8f0ef;
      box-shadow:inset 0 -1px 0 #1c4e4a; border-bottom-color:transparent; }
  .calc__edit:focus-visible { outline:2px solid #1c4e4a; outline-offset:2px; }
  .calc__cost .calc__amount { color:#8a4a44; }

  span.calc__name { position:relative; border-bottom:1px dotted #b0b0b8;
      cursor:help; outline-offset:3px; }
  span.calc__tip { position:absolute; left:0; top:calc(100% + .45rem); z-index:9999;
      display:none; white-space:pre-wrap; width:max-content;
      max-width:min(680px,calc(100vw - 32px)); overflow-wrap:anywhere;
      padding:.6rem .75rem; border-radius:4px;
      font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;
      font-size:12.5px; line-height:1.5; letter-spacing:0; font-weight:400;
      background:#101014; color:#e9e6df; border:1px solid rgba(255,255,255,.18);
      box-shadow:0 8px 24px rgba(0,0,0,.45); }
  span.calc__name:hover span.calc__tip,
  span.calc__name:focus span.calc__tip,
  span.calc__name:focus-within span.calc__tip { display:block; }
  span.calc__name[data-tip-dismissed] span.calc__tip { display:none; }
  span.calc__tip b { color:#ff8a7a; font-weight:600; }
  table.calc tr:nth-last-child(-n+4) span.calc__tip {
      top:auto; bottom:calc(100% + .45rem); }
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
        + (f"  = {escape(step.expr)}\n" if step.expr else "")
        + f"  = {escape(step.formula)}\n"
        f"  = {escape(amount)}"
    )


def _calculation_table(steps, parcel_area=None, per_m2=None, *, editable=False):
    """Render the formula path in the compact table used by the prototype."""
    rows = []
    result = next((step for step in steps if step.kind == "result"), None)
    for step in (step for step in steps if step.kind != "result"):
        amount = (
            f"{E.chf(step.value)} m²"
            if step.unit == "m²"
            else f"CHF {E.chf(step.value)}"
        ).replace("-", "−")
        label = step.label.lstrip("−= ").strip()
        amount_markup = escape(amount)
        if editable and step.key in E.OVERRIDE_KEYS:
            modifier = " calc__edit--overridden" if step.overridden else ""
            amount_markup = (
                f'<button type="button" class="calc__edit{modifier}" '
                f'data-field="{escape(step.key)}" data-value="{abs(step.value):.15g}" '
                f'aria-label="{escape(label)}: Betrag bearbeiten" '
                'title="Klicken zum Überschreiben">'
                f'{escape(amount.removeprefix("CHF "))}</button>'
            )
        rows.append(
            f'<tr class="calc__row calc__{escape(step.kind)}">'
            f'<th scope="row"><span class="calc__name" tabindex="0">'
            f'{escape(label)}'
            f'<span class="calc__tip">{_tooltip(step)}</span></span></th>'
            f'<td class="calc__formula">{escape(step.formula)}</td>'
            f'<td class="calc__amount">{amount_markup}</td></tr>'
        )

    if result is not None:
        result_amount = f"CHF {E.chf(result.value)}".replace("-", "−")
        rows.append(
            '<tr class="calc__result"><th scope="row">Residualer Landwert</th>'
            '<td class="calc__formula">Erlös − Kosten − Reserve</td>'
            f'<td class="calc__amount">{escape(result_amount)}</td></tr>'
        )
    if parcel_area is not None:
        sqm_amount = "—" if per_m2 is None else f"CHF {E.chf(per_m2)}"
        rows.append(
            '<tr class="calc__sqm"><th scope="row">Davon pro m² Grundstück</th>'
            f'<td class="calc__formula">÷ {escape(E.chf(parcel_area))} m²</td>'
            f'<td class="calc__amount">{escape(sqm_amount)}</td></tr>'
        )

    return (
        CALC_CSS
        + '<div class="calc__scroll"><table class="calc"><thead><tr>'
        '<th>Position</th><th>Berechnung</th>'
        '<th class="calc__amount">Betrag CHF</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></div>"
    )


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
    "Bei aktuellen Annahmen · vor Steuern und Transaktionskosten"
)

#: The same line when the number comes out negative. One line either way, so
#: bar keeps its height: a warning box here added 72px to it, and did it in
#: exactly the case where the inputs underneath most need the room.
NEGATIVE_CAVEAT = (
    "Negativer Residualwert — das zusätzliche Potenzial trägt die "
    "Erstellungskosten nicht."
)

FINAL_NOTE = (
    "Berechnung ohne Gewähr. Amtliche Daten aus öffentlichen Registern des "
    "Kantons Aargau; Referenzpreise gemäss der jeweils ausgewiesenen Quelle. "
    "Vor Erwerb ist eine rechtliche Prüfung der Bau- und Zonenordnung durch "
    "die zuständige Baubehörde erforderlich."
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
    st.html(PAGE_CSS)

    address = _text(row.get("address")) or f"Parzelle {row['parcel']}"
    with st.container(key="detail_breadcrumb", horizontal=True):
        if st.button("Screening", key="detail_back"):
            close()
            navigation.go_back()
            st.rerun()
        st.html(
            '<div class="detail-breadcrumb-trail">'
            '<span>/</span>'
            f'<span>{escape(str(row["municipality"]))}</span>'
            '<span>/</span>'
            '<span class="detail-breadcrumb-current">'
            f'Parzelle {escape(str(row["parcel"]))}</span></div>'
        )

    with st.container(key="detail_header"):
        heading, action_column = st.columns([5, 3], vertical_alignment="top")
        with heading:
            badge_html = "".join(
                '<span class="detail-status-pill '
                f'{escape(tone)}">{escape(label)}</span>'
                for label, tone in _detail_header_badges(row, cache)
            )
            st.title(address)
            st.html(
                '<div class="detail-header-meta">'
                f'{badge_html}'
                '<span class="detail-header-updated">'
                f'{escape(_detail_calculated_at(row.get("calculated_at")))}'
                '</span></div>'
            )
        with action_column.container(key="detail_actions"):
            saved_action, pdf_action = st.columns([1.6, 1.2])
            if _on_merkliste(db_path, *key):
                if saved_action.button(
                    "Merkliste — entfernen", width="content"
                ):
                    WF.set_saved([key], False, db_path)
                    st.toast("Von der Merkliste entfernt.")
                    st.rerun()
            elif saved_action.button(
                "Auf Merkliste", width="content"
            ):
                WF.set_saved([key], True, db_path)
                st.toast("Auf die Merkliste gesetzt.")
                st.rerun()

    team_enabled = _team_load(pid, db_path)
    price_ref = price_of(row)
    extract = extract_of(row, cache)
    zone_rows = _zone_rows(extract) if extract else []

    # Claimed here and written at the end: the result needs the inputs below to
    # exist before it can be computed, but it belongs above them on the page.
    result_bar = st.container(key="result_bar")

    facts, potential_panel = st.columns(2, gap="medium")

    # ── Block A ─────────────────────────────────────────────────────────────
    with facts.container(key="facts_a"):
        st.html(_detail_facts_html(row, cache, price_ref, extract))

    # ── Block B ─────────────────────────────────────────────────────────────
    with potential_panel.container(key="inputs_b"):
        st.html(
            '<div class="detail-potential-head">'
            '<span class="detail-potential-title">B · Potenzial-Annahmen</span>'
            '<span class="detail-edit-pill">Editierbar</span></div>'
        )
        with st.container(key="inputs_b_body"):
            b1, b2 = st.columns(2, gap="small")
            potential = _number(
                b1, "Ausnutzungsreserve aBGF m²", pid, "gf",
                float(row["delta"]), step=10.0,
            )
            b1.html(
                '<div class="detail-input-hint">Vorschlag aus BZO: '
                f'{E.chf(float(row["delta"]))} m²</div>'
            )
            unit_size = _number(
                b2, "Ø Wohnungsgrösse m²", pid, "unit",
                float(E.SQM_PER_UNIT), step=5.0, minimum=10.0,
            )
            b2.html(
                '<div class="detail-input-hint">'
                'Marktübliche 3.5-Zi-Wohnung</div>'
            )
            possible = E.units(potential, unit_size)
            potential_result = st.empty()

    # ── Block C ─────────────────────────────────────────────────────────────
    calculation_panel = st.container(key="inputs_c")
    with calculation_panel:
        with st.container(key="inputs_c_header", horizontal=True,
                          vertical_alignment="center", gap="small"):
            st.html(
                '<span class="detail-calculation-title">'
                'C · Residualwert-Rechnung</span>'
            )
            st.html(
                '<div class="detail-calculation-meta">'
                '<span class="detail-edit-pill">Editierbar</span>'
                '<span class="detail-calculation-hint">'
                'Beträge direkt anklickbar</span></div>'
            )
            manual = st.session_state.get(OVERRIDE_STORE, {}).get(pid, {})
            if manual:
                if st.button(
                    f"{len(manual)} überschrieben · zurücksetzen",
                    key=f"{pid}::overrides-reset",
                    width="content",
                ):
                    st.session_state.get(OVERRIDE_STORE, {}).pop(pid, None)
                    st.rerun()
            if st.button(
                "Standardwerte", key=f"{pid}::calculation-defaults"
            ):
                forget(pid)
                st.rerun()

        with st.container(key="inputs_c_body"):
            c1, c2, c3, c4 = st.columns(4)
            sale_price = _number(
                c1, "Verkaufspreis CHF/m²", pid, "sale",
                E.BENCHMARKS["sale_price_chf_m2"].value, step=100.0,
                help=_benchmark_help("sale_price_chf_m2", name="sale"),
            )
            sale_share = _number(
                c2, "Verkaufsfläche % der aBGF", pid, "share",
                E.BENCHMARKS["sale_area_pct"].value, step=1.0,
                minimum=10.0, maximum=100.0,
                help=_benchmark_help(
                    "sale_area_pct",
                    "Wirkt nur auf den Erlös; die Baukosten rechnen auf der ganzen "
                    "Geschossfläche.",
                    name="share",
                ),
            )
            construction = _number(
                c3, "Baukosten CHF/m² aBGF", pid, "build",
                E.BENCHMARKS["construction_chf_m2"].value, step=50.0,
                help=_benchmark_help("construction_chf_m2", name="build"),
            )
            ancillary = _number(
                c4, "Nebenkosten % Baukosten", pid, "ancillary",
                E.BENCHMARKS["ancillary_pct"].value, step=1.0, maximum=100.0,
                help=_benchmark_help("ancillary_pct", name="ancillary"),
            )

            d1, d2, d3, d4 = st.columns(4, vertical_alignment="bottom")
            has_building = bool(row["buildings"]) and row["existing"] > 0
            demolition = _number(
                d1, "Abbruch CHF/m² best. GF", pid, "demolition",
                E.BENCHMARKS["demolition_chf_m2"].value, step=10.0,
                help=_benchmark_help("demolition_chf_m2", name="demolition"),
            )
            financing = _number(
                d2, "Finanzierung % der Kosten", pid, "financing",
                E.BENCHMARKS["financing_pct"].value, step=0.5,
                maximum=100.0, fmt="%.1f",
                help=_benchmark_help("financing_pct", name="financing"),
            )
            reserve = _number(
                d3, "Reserve % der Kosten", pid, "reserve",
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

    if team_enabled:
        _team_save(pid, db_path)

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
        overrides=st.session_state.get(OVERRIDE_STORE, {}).get(pid, {}),
    )
    land = E.land_value(steps)
    per_m2 = E.per_square_metre(steps, float(row["area"]))
    potential_result.html(_potential_result_html(
        row, potential, unit_size, possible, demolish=bool(demolish),
        demolition_step=next(step for step in steps if step.key == "abbruchkosten"),
    ))

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
    with calculation_panel:
        error_key = f"_calculation_error_{pid}"
        if message := st.session_state.pop(error_key, None):
            st.error(message)
        event = UI.consume_event(
            UI.calculation_table(
                _calculation_table(steps, float(row["area"]), per_m2, editable=True),
                parcel=pid, key=f"{pid}::calculation-table",
            ),
            f"calculation:{pid}",
        )
        if event:
            try:
                apply_calculation_event(event, pid)
            except ValueError as error:
                st.session_state[error_key] = str(error)
            # Acknowledge rejected/ignored intents too, so the frontend queue
            # cannot get stuck behind an invalid edit. The consumed id prevents
            # the retained component value from being applied twice.
            st.rerun()

    # ── The result bar ──────────────────────────────────────────────────────
    # Written last, drawn first. The warning belongs here rather than beside the
    # table: it explains the number, and this is where the number is read.
    with result_bar:
        st.html(_result_bar_html(row, land, per_m2, price_ref))

    # ── Block D ─────────────────────────────────────────────────────────────
    # The regulations themselves, straight out of the same extract. Before this,
    # the answer to "what may I build here" ended at a number; now the document
    # that sets the number is one click away, and it is the one the cadastre
    # names for this parcel rather than one found by matching municipality names.
    #
    # The prototype keeps legal references and the assumptions that qualify
    # them in one compact native disclosure card. There is no standalone
    # disclaimer paragraph or second "Annahmen und Quellen" expander.
    legal_card = _reference_card_html(extract, zone_rows, notes)

    # ── Block E ─────────────────────────────────────────────────────────────
    # What block D cannot say. The ÖREB extract names the documents that govern
    # this parcel and marks them `inForce`, and stops — it carries no date. That
    # date is the whole question behind "is this analysis still on the current
    # rules", and OEREBlex answers it for the canton in one request.
    #
    # The request itself runs in a background thread (`regulations.
    # ensure_news_started`), started here rather than awaited here: block E is
    # the only thing in this view that depends on a third-party server, and
    # this function has no business making the other four blocks wait on it.
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
    place = _text(row["municipality"])
    # Capture one Swiss date for the governing edition, procedure status and PDF.
    regulation_today = PP.swiss_today()
    own, own_note = (
        (None, "") if not have_news or news_error
        else R.for_municipality(edicts, place, int(row["bfs"]),
                                today=regulation_today)
    )
    # Procedures under way come from a local file, not the network, so they
    # are read on the render path. Off unless a store is configured; see
    # `planning.load_store` for why "off", "unreadable" and "none" differ.
    # None, not empty: without OEREBlex the editions in force are unknown,
    # and a Planungszone cannot be shown in force (`planning._zone_verdict`).
    planning_view = PL.state_for(
        place, R.in_municipality(edicts, place) if have_news and not news_error else None,
        today=regulation_today, parcel=extract)
    regulation_card = _regulation_card_html(
        row,
        edicts,
        own,
        own_note,
        loading=not have_news,
        error=news_error if have_news else "",
        planning=planning_view,
        preview=PP.state_for(place, today=regulation_today),
        today=regulation_today,
    )
    with st.container(key="detail_references"):
        with st.container(key="detail_legal"):
            st.html(legal_card)
            sources = source_downloads.references(extract)
            st.download_button(
                "Alle herunterladen",
                data=partial(source_downloads.build_archive, sources),
                file_name="rechtsgrundlagen.zip",
                mime="application/zip",
                key="detail_sources_download",
                on_click="ignore",
                disabled=not sources,
                help=("ZIP mit abrufbaren PDFs und Quellenverzeichnis. Webseiten "
                      "und nicht abrufbare PDFs werden darin gekennzeichnet."),
            )
        st.html(
            '<div class="detail-reference-stack">'
            + regulation_card
            + f'<p class="detail-final-note">{escape(FINAL_NOTE)}</p>'
            + '</div>'
        )
    if not have_news or news_status != "done":
        # The compact card keeps the same non-blocking refresh behaviour as the
        # old open list: it fills itself without requiring an unrelated click.
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
    # Block E on paper: the edition of the building regulation the sheet was
    # computed under, and whether a revision was under way at the time.
    blocks.append(_regulation_block(
        own, own_note, news_error, planning=planning_view, municipality=place,
        edicts=R.in_municipality(edicts, place) if have_news and not news_error else (),
        today=regulation_today))

    try:
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
        failure = ""
    except Exception as exc:  # the sheet fails, not the analysis drawn above it
        document, failure = b"", f"PDF konnte nicht erstellt werden ({type(exc).__name__})."
    pdf_action.download_button(
        "Als PDF exportieren",
        data=document,
        file_name=f"parzelle-{row['municipality']}-{row['parcel']}.pdf".replace(" ", "-"),
        mime="application/pdf",
        type="primary",
        width="content",
        disabled=bool(failure),
        help=failure or "Alle drei Blöcke samt vollständigem Rechenweg und Quellen.",
    )
