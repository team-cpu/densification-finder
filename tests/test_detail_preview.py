"""Preview rendering stays neutral, scoped, escaped, and outside PDF exports."""
from datetime import date, datetime, timedelta, timezone
from html import escape
from unittest.mock import patch
import pytest

import detail
import planning as PL
import planning_collect as C
import planning_preview as P

NOW = datetime(2026, 10, 6, 5, 0, tzinfo=timezone.utc)


def candidates():
    return [
        {"source_id": {"namespace": P.NS_AMTSBLATT, "value": "00.999.701"}, "title": "Gestaltungsplan PreviewOnly <img src=x onerror=alert(1)>",
         "url": "https://amtsblatt.ag.ch/ekab/00.999.701/publikation/", "published_on": "2026-09-10",
         "authority": "Gemeinde Seon", "rubric": "Gemeinden / Bau- und Nutzungsordnung", "publication_version_label": "Korrektur"},
        {"source_id": {"namespace": P.NS_CANTON, "value": "29605fc7-c5d2-46c2-89d4-abd25f8e74fb_de"},
         "title": "PreviewOnly EG Umweltrecht", "url": C.AG_ORIGIN + C.CONSULTATION_PATHS["current"] + "?dc=29605fc7-c5d2-46c2-89d4-abd25f8e74fb_de",
         "authority": "Departement Bau, Verkehr und Umwelt", "publication_date": "2026-09-04T06:55:00Z",
         "cms_valid_from": "2026-09-04T06:55:00Z", "cms_valid_until": None},
    ]


def view(*, absent=False):
    return P.View(status="ok", municipality_queried=True, items=[P.PreviewItem(candidate, absent) for candidate in candidates()], sources=[{
        "selection": {"provider": "amtsblatt.ag.ch", "kind": "municipal", "municipality": "Seon"},
        "last_success_at": NOW, "last_attempt_at": NOW, "error": "", "stale": False, "empty": False}])


def render(preview=None, planning=None):
    return detail._regulation_card_html({"municipality": "Seon"}, [], None, "",
                                        preview=preview, planning=planning or PL.View(status="off"), today=NOW.date())


def auto_view(*, items=None, municipality_queried=True, status="ok", sources=None):
    return P.View(status=status, auto=True, municipality_queried=municipality_queried,
                 items=[] if items is None else items,
                 sources=[{"selection": {"provider": "amtsblatt.ag.ch", "kind": "municipal", "municipality": "Seon"},
                          "last_success_at": NOW, "last_attempt_at": NOW, "error": "", "stale": False, "empty": False}]
                 if sources is None else sources)


def test_auto_mode_heading_intro_badge_link_and_meta_wording():
    candidate = candidates()[0]
    html = render(auto_view(items=[P.PreviewItem(candidate, auto=True)]))
    assert "Gemeinde Seon · Publikationen im Amtsblatt" in html
    assert "automatisch übernommen: Titel und Datum wie publiziert" in html
    assert "Lokale Vorschau" not in html and "ausgewählte Publikationen" not in html
    assert ('title="Automatisch übernommen — Titel und Datum wie publiziert, kein geprüfter '
            'Verfahrensstand">Publikation</span>') in html
    assert f'href="{candidate["url"]}" target="_blank" rel="noopener noreferrer"' in html
    assert ">↗</a>" in html
    assert "· 1 Publikation im Amtsblatt" in html
    assert "ausgewählte Publikationen" not in html


def test_auto_mode_empty_case_uses_municipal_last_success_date():
    html = render(auto_view(items=[]))
    assert ("Keine Publikationen von Seon in der Amtsblatt-Rubrik «Bau- und Nutzungsordnung» "
            "(zuletzt abgefragt 06.10.2026).") in html


def test_auto_mode_meta_uses_amtsblatt_wording_for_failed_or_stale_sources():
    sources = [{"selection": {"provider": "amtsblatt.ag.ch", "kind": "municipal", "municipality": "Seon"},
               "last_success_at": NOW, "last_attempt_at": NOW, "error": "boom", "stale": True, "empty": False}]
    html = render(auto_view(sources=sources))
    assert "Amtsblatt-Abfrage fehlgeschlagen" in html and "Amtsblatt-Stand veraltet" in html
    assert "Vorschau-Abfrage fehlgeschlagen" not in html and "Vorschau veraltet" not in html


def test_fallback_links_appear_when_municipality_not_queried_in_both_modes():
    html_auto = render(auto_view(items=[], municipality_queried=False, sources=[]))
    assert "Für Seon noch nicht automatisch abgefragt" in html_auto
    assert "Amtsblatt-Suche: Seon, Bau- und Nutzungsordnung" in html_auto
    html_manual = render(P.View(status="ok", municipality_queried=False))
    assert "Für Seon noch nicht automatisch abgefragt" in html_manual
    assert "Amtsblatt-Suche: Seon, Bau- und Nutzungsordnung" in html_manual


def test_fallback_links_appear_on_preview_error_in_both_modes():
    html_manual = render(P.View(status="error", error="Lokale Vorschau nicht lesbar."))
    assert "Amtsblatt-Abfrage nicht lesbar" in html_manual
    assert "Amtsblatt-Suche: Seon, Bau- und Nutzungsordnung" in html_manual
    html_auto = render(P.View(status="error", error="x", auto=True))
    assert "Amtsblatt-Abfrage nicht lesbar" in html_auto
    assert "Amtsblatt-Suche: Seon, Bau- und Nutzungsordnung" in html_auto


def test_auto_row_title_and_url_are_escaped():
    candidate = {**candidates()[0], "title": '<script>"\'&'}
    html = render(auto_view(items=[P.PreviewItem(candidate, auto=True)]))
    assert "<script>" not in html
    assert escape(candidate["title"]) in html


def test_preview_off_preserves_existing_fallback_html_exactly():
    assert render() == render(P.View())
    assert "sind noch nicht angebunden" in render()
    assert "Lokale Vorschau" not in render()


def test_preview_source_rows_are_neutral_escaped_and_accessible():
    html = render(view())
    assert "sind noch nicht angebunden" not in html
    assert html.count(">Publikation</span>") == 2
    assert "Entwurf in Auflage" not in html and "Genehmigt, Rechtskraft ausstehend" not in html
    assert "Quellenhinweis: Korrektur" in html
    assert escape(candidates()[0]["title"]) in html
    assert "<img src=x onerror=" not in html
    assert 'aria-label="Originale Anhörung auf ag.ch öffnen: PreviewOnly EG Umweltrecht"' in html
    assert 'title="Originale Anhörung auf ag.ch öffnen: PreviewOnly EG Umweltrecht"' in html
    assert "Link ↗" in html and "keine Einzelpublikation" not in html
    assert candidates()[1]["url"] in html
    assert "keine vollständige Rechtsgeschichte" in html
    assert "bestätigten heutigen Verfahrensstand" in html
    assert "fingerprint" not in html and "scope.planning" not in html and "preview.json" not in html


def test_unsafe_direct_view_url_cannot_be_rendered_as_source_link():
    preview = view()
    preview.items[0].candidate["url"] = 'javascript:alert(1)'
    html = render(preview)
    assert "javascript:" not in html and 'href="javascript:' not in html
    assert escape(preview.items[0].candidate["title"]) in html


def test_preview_duplicate_amtsblatt_id_yields_to_existing_production_row():
    candidate = candidates()[0]
    records, _ = PL.publications([{**{k: v for k, v in candidate.items() if k not in ("source_id", "publication_version_label")},
                                  "pub_nr": candidate["source_id"]["value"], "municipality": "Seon"}])
    production = PL.listing(records, "Seon")
    production.status = "ok"
    production.since, production.stand = date(2026, 1, 1), NOW.date()
    html = render(view(), production)
    assert html.count(candidate["url"]) == 1
    assert html.count(escape(candidate["title"])) == 1
    assert "PreviewOnly EG Umweltrecht" in html
    assert "lokale Vorschau: 1 ausgewählte Publikationen" in html


def test_absence_fetch_failure_and_staleness_preserve_rows_and_data_dates():
    preview = view(absent=True)
    preview.sources[0].update(error="Technical failure with secret path /tmp/hidden", stale=True,
                              last_attempt_at=NOW + timedelta(days=10))
    html = render(preview)
    assert "zuletzt erfolgreich abgefragt 06.10.2026" in html
    assert "Letzte Abfrage am 16.10.2026" in html
    assert "Abfrage fehlgeschlagen" in html and "Vorschau veraltet" in html
    assert "die letzten gültigen Daten bleiben erhalten" in html
    assert "In einer zuletzt erfolgreich abgefragten Suchauswahl nicht mehr enthalten" in html
    assert "kein Nachweis einer Genehmigung oder Aufhebung" in html
    assert html.count(">Publikation</span>") == 2
    assert "/tmp/hidden" not in html


def test_pending_changed_unqueried_and_error_have_distinct_visible_messages():
    preview = P.View(status="ok", pending=3, changed=2)
    html = render(preview)
    assert "Gemeindesuche noch nicht erfolgreich abgefragt" in html
    assert "Noch zu prüfen: 3 Publikationen" in html
    assert "erneut zu prüfen: 2" in html
    assert "Keine freigegebenen Publikationen" in html
    assert "dies bedeutet nicht, dass keine Änderungen geplant sind" in html
    assert "sind noch nicht angebunden" not in html
    error = render(P.View(status="error", error="Lokale Vorschau nicht lesbar."))
    assert "Lokale Vorschau nicht lesbar." in error
    assert "Keine freigegebenen Publikationen" not in error


def test_preview_never_enters_printed_regulation_block_even_when_env_is_enabled(monkeypatch):
    monkeypatch.setenv(P.ENV, "/tmp/preview-path-never-read-on-pdf.json")
    assert "PreviewOnly" in render(view())
    with patch.object(P, "state_for", side_effect=AssertionError("PDF must not load preview")):
        _, printed = detail._regulation_block(None, "", "", municipality="Seon", planning=PL.View(status="off"), today=NOW.date())
    assert "PreviewOnly" not in str(printed) and "Korrektur" not in str(printed)


@pytest.mark.parametrize("stage,dates,label", [
    ("auflage", {"auflage_from": date(2026, 10, 1), "auflage_to": date(2026, 10, 10)}, "Entwurf in Auflage"),
    ("genehmigt", {"approved_on": date(2026, 10, 1), "appeal_until": date(2026, 10, 10)}, "Genehmigt, Rechtskraft ausstehend"),
    ("in_kraft", {"effective_on": date(2026, 10, 1)}, "In Kraft getreten"),
    ("genehmigt", {"approved_on": date(2026, 10, 1)}, "Publikation"),
    ("auflage", {"auflage_from": date(2026, 9, 1), "auflage_to": date(2026, 9, 10)}, "Publikation"),
])
def test_preview_badges_use_existing_verified_stage_windows(stage, dates, label):
    preview = view()
    candidate = preview.items[0].candidate
    preview.items[0].verified = PL.Verified(stage, candidate["url"].replace("/publikation/", "/pdf/"), NOW.date(), "Original checked <script>", **dates)
    html = render(preview)
    assert f">{label}</span>" in html
    assert "Geprüfter Quellenbeleg ↗" in html
    assert "Original checked &lt;script&gt;" in html and "<script>" not in html
    assert preview.items[0].verified.source in html
    assert html.count(">Publikation</span>") == (2 if label == "Publikation" else 1)


def test_unproven_direct_view_cannot_supply_a_legal_badge_or_proof_link():
    preview = view()
    preview.items[0].verified = PL.Verified("in_kraft", "https://evil.invalid/proof.pdf", NOW.date(), "Unrelated", effective_on=NOW.date())
    html = render(preview)
    assert html.count(">Publikation</span>") == 2
    assert "evil.invalid" not in html and "Geprüfter Quellenbeleg" not in html


def test_current_mitwirkung_is_neutral_without_explicit_legal_proof():
    preview = view()
    preview.items[0].candidate["title"] = "Öffentliche Mitwirkung 01.10.2026–20.10.2026"
    assert "Entwurf in Auflage" not in render(preview)
    assert render(preview).count(">Publikation</span>") == 2


def test_regulation_card_defaults_to_swiss_calendar_after_utc_midnight_boundary(monkeypatch):
    next_day = P.swiss_today(datetime(2026, 7, 1, 22, 30, tzinfo=timezone.utc))
    assert next_day == date(2026, 7, 2)
    monkeypatch.setattr(P, "swiss_today", lambda: next_day)
    preview = view()
    preview.items[0].verified = PL.Verified("auflage", preview.items[0].candidate["url"], date(2026, 7, 1), "Original checked", auflage_from=date(2026, 6, 1), auflage_to=date(2026, 7, 1))
    html = detail._regulation_card_html({"municipality": "Seon"}, [], None, "", preview=preview, planning=PL.View(status="off"))
    assert "beendet — heutiger Stand nicht geprüft" in html
    assert "Entwurf in Auflage" not in html


def test_preview_unchecked_coverage_renders_no_scope_tag():
    assert "detail-regulation-scope" not in render(view())


@pytest.mark.parametrize("kind,area,label", [
    ("gebiet", "Heidegrabe", "Nur Gebiet «Heidegrabe»"),
    ("gemeinde", "", "Ganze Gemeinde"),
])
def test_preview_coverage_renders_the_scope_tag(kind, area, label):
    preview = view()
    candidate = preview.items[0].candidate
    preview.items[0].coverage = PL.Coverage(kind, area, candidate["url"], NOW.date(), "Original checked <script>")
    html = render(preview)
    assert f'<span class="detail-regulation-scope">{escape(label)}</span>' in html
    assert "Geltungsbereich geprüft am 06.10.2026: Original checked &lt;script&gt;" in html
    assert "<script>" not in html
    assert "Nachweis ↗" in html


def test_preview_coverage_area_containing_markup_and_quotes_is_escaped():
    preview = view()
    candidate = preview.items[0].candidate
    preview.items[0].coverage = PL.Coverage("gebiet", '<script>"\'&', candidate["url"], NOW.date(), "checked")
    html = render(preview)
    assert "<script>" not in html
    assert escape("Nur Gebiet «<script>\"'&»") in html


def test_preview_future_coverage_check_has_no_tag_but_the_note():
    preview = view()
    candidate = preview.items[0].candidate
    preview.items[0].coverage = PL.Coverage("gemeinde", "", candidate["url"], NOW.date() + timedelta(days=1), "checked")
    html = render(preview)
    assert "detail-regulation-scope" not in html
    assert "datiert nach dem" in html


def test_preview_coverage_evidence_of_another_item_cannot_supply_a_scope_tag():
    preview = view()
    preview.items[0].coverage = PL.Coverage("gemeinde", "", "https://evil.invalid/proof.pdf", NOW.date(), "Unrelated")
    html = render(preview)
    assert "detail-regulation-scope" not in html
    assert "evil.invalid" not in html


def test_auto_mode_hides_operator_notes_from_readers():
    # Nobody reviews the canton consultations in production: their queue,
    # the store's own error text and a second "not queried" note are the
    # operator's business, not the investor's.
    html = render(auto_view(items=[], municipality_queried=False, sources=[]).__class__(
        status="ok", auto=True, municipality_queried=False, pending=3, changed=1))
    assert "Noch zu prüfen" not in html
    assert "Gemeindesuche noch nicht erfolgreich abgefragt" not in html
    assert html.count("noch nicht automatisch abgefragt") == 1
    error = render(P.View(status="error", error="Lokale Vorschau nicht lesbar.", auto=True))
    assert "Lokale Vorschau nicht lesbar." not in error
    assert "Amtsblatt-Abfrage nicht lesbar" in error


def test_auto_meta_counts_publications_in_german_plural():
    one = P.PreviewItem(dict(candidates()[0], title="Eins"), auto=True)
    two = P.PreviewItem(dict(candidates()[0], title="Zwei", source_id={"namespace": P.NS_AMTSBLATT, "value": "00.999.702"},
                             url="https://amtsblatt.ag.ch/ekab/00.999.702/publikation/"), auto=True)
    assert "· 1 Publikation im Amtsblatt" in render(auto_view(items=[one]))
    assert "· 2 Publikationen im Amtsblatt" in render(auto_view(items=[one, two]))
    assert "Amtsblatt-Abfrage nicht lesbar" in render(P.View(status="error", error="x", auto=True))


def test_auto_row_authority_is_escaped():
    item = P.PreviewItem(dict(candidates()[0], authority='Gemeinde <b>Seon</b> "&'), auto=True)
    html = render(auto_view(items=[item]))
    assert "<b>Seon</b>" not in html
    assert escape('Gemeinde <b>Seon</b> "&') in html


def test_auto_mode_never_counts_zero_for_a_gemeinde_nobody_queried():
    html = render(auto_view(items=[], municipality_queried=False, sources=[]))
    assert "0 Publikationen" not in html and "Verfahren: Übergangslösung" in html
    assert "automatisch übernommen" not in html
    assert "Amtsblatt-Suche: Seon, Bau- und Nutzungsordnung" in html
    queried = render(auto_view(items=[]))
    assert "· 0 Publikationen im Amtsblatt" in queried and "automatisch übernommen" in queried


def test_auto_card_before_the_first_refresh_and_on_a_broken_store_through_state_for(tmp_path):
    # Through the real state_for, not a hand-made View: production runs with a
    # configured path whose file the first refresh has not written yet.
    missing = P.state_for("Seon", tmp_path / "planning-preview.json", today=NOW.date(), auto=True)
    assert missing.status == "ok" and missing.auto and not missing.municipality_queried
    html = render(missing)
    assert "Lokale Vorschau" not in html and "nicht lesbar" not in html
    assert "Verfahren: Übergangslösung" in html and "Amtsblatt-Suche: Seon, Bau- und Nutzungsordnung" in html
    assert P.state_for("Seon", tmp_path / "planning-preview.json", today=NOW.date(), auto=False).status == "error"
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    view = P.state_for("Seon", broken, today=NOW.date(), auto=True)
    assert view.status == "error" and view.auto
    html = render(view)
    assert "Lokale Vorschau" not in html and "Amtsblatt-Abfrage nicht lesbar" in html
    assert "Amtsblatt-Suche: Seon, Bau- und Nutzungsordnung" in html
