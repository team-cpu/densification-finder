import datetime
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from html import escape
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest

import functools

import amtsblatt
import detail
import economics as E
import navigation
import paths
import planning
import regulations
import report
import workflow


#: A trimmed OEREBlex payload. The tests must not reach the network: a suite
#: that needs oereblex.ag.ch to be up is a suite that fails for reasons that
#: have nothing to do with this code, and it took the run from 5s to 44s.
EDICTS_FIXTURE = [
    {"name": "Möhlin", "edicts": [
        {"id": 1, "syst_nr": "4254", "title": "Bau- und Nutzungsordnung",
         "abbreviation": "BNO", "inaction_date": "2023-12-13",
         "outaction_date": None, "is_active": True,
         "main_document": {"document_path": "/api/attachments/1"}},
        {"id": 2, "syst_nr": "4254", "title": "Bau- und Nutzungsordnung",
         "abbreviation": "BNO", "inaction_date": "2001-01-01",
         "outaction_date": "2023-12-12", "is_active": False,
         "main_document": {"document_path": "/api/attachments/0"}},
    ]},
    {"name": "Gipf-Oberfrick", "edicts": [
        {"id": 3, "syst_nr": "4165", "title": "Bau- und Nutzungsordnung",
         "abbreviation": "BNO", "inaction_date": "2026-06-03",
         "outaction_date": None, "is_active": True,
         "main_document": {"document_path": "/api/attachments/3"}},
    ]},
    # OEREBlex numbers this one 4196; the building register says BFS 4194.
    {"name": "Dintikon", "edicts": [
        {"id": 4, "syst_nr": "4196", "title": "Bau- und Nutzungsordnung",
         "abbreviation": "BNO", "inaction_date": "2022-05-18",
         "outaction_date": None, "is_active": True, "main_document": {}},
    ]},
    {"name": "Ohnedatum", "edicts": [
        {"id": 5, "syst_nr": "4999", "title": "Bau- und Nutzungsordnung",
         "abbreviation": "BNO", "inaction_date": None,
         "outaction_date": None, "is_active": True, "main_document": {}},
    ]},
]


def stub_regulations(case, municipality=None, bfs=None):
    """Point `regulations.load` at the fixture for the duration of one test.

    `municipality` adds an entry for the parcel under test, so the panel's own
    first line — "this parcel's regulation, in force since" — is exercised
    rather than falling through to the not-listed branch. Left out, that branch
    is what renders, which is also worth being able to reach.
    """
    towns = list(EDICTS_FIXTURE)
    if municipality:
        towns = towns + [{"name": municipality, "edicts": [
            {"id": 99, "syst_nr": str(bfs or ""), "title": "Bau- und Nutzungsordnung",
             "abbreviation": "BNO", "inaction_date": "2024-09-01",
             "outaction_date": None, "is_active": True,
             "main_document": {"document_path": "/api/attachments/99"}},
        ]}]
    real = regulations.load
    regulations.load = lambda timeout=30: (regulations.parse(towns), "")
    case.addCleanup(lambda: setattr(regulations, "load", real))


def wait_for_news(timeout=5):
    """Block until the background edict fetch settles.

    Block E's whole point is that the page does not wait for this — so a test
    that wants to see the *result* has to wait for it deliberately instead,
    the way the fragment's own `run_every` tick would once the browser is the
    one driving it. Polls rather than sleeping a fixed amount: the fixture
    fetch settles in well under a millisecond, and a fixed sleep long enough
    to be safe for the slow-fetch tests would make every other test pay for it.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if regulations.news_state()[0] == "done":
            return
        time.sleep(0.01)
    raise AssertionError("regulation news fetch did not settle in time")


def result_markup(app):
    """Return the dedicated result strip rather than coupling tests to widgets."""
    return next(
        str(getattr(element.proto, "body", ""))
        for element in app.main
        if getattr(element, "type", "") == "html"
        and 'class="detail-result-grid"' in str(getattr(element.proto, "body", ""))
    )


def calculation_component(app):
    return next(e for e in app.get("component_instance")
                if e.proto.component_name.endswith("scope_calculation_table"))


def calculation_markup(app):
    return json.loads(calculation_component(app).proto.json_args)["html"]


def facts_markup(app):
    return next(
        str(getattr(element.proto, "body", ""))
        for element in app.main
        if getattr(element, "type", "") == "html"
        and 'class="detail-facts-card"' in str(getattr(element.proto, "body", ""))
    )


def potential_markup(app):
    return next(
        str(getattr(element.proto, "body", ""))
        for element in app.main
        if getattr(element, "type", "") == "html"
        and 'class="detail-potential-result"' in str(
            getattr(element.proto, "body", "")
        )
    )


def potential_units(app):
    match = re.search(r'data-units="([^"]*)"', potential_markup(app))
    if match is None:
        raise AssertionError("potential result has no data-units attribute")
    return match.group(1)


def calculation_header_markup(app):
    return next(
        str(getattr(element.proto, "body", ""))
        for element in app.main
        if getattr(element, "type", "") == "html"
        and 'class="detail-calculation-title"' in str(
            getattr(element.proto, "body", "")
        )
    )


def reference_card_markup(app):
    return next(
        str(getattr(element.proto, "body", ""))
        for element in app.main
        if getattr(element, "type", "") == "html"
        and 'class="detail-reference-card detail-reference-card--legal"' in str(
            getattr(element.proto, "body", "")
        )
    )


#: The inference path (chains, statuses, zone verdicts) is OFF by default;
#: the tests written for it switch it on.
inferred_view = functools.partial(planning.view, inference=True)


def pdf_links(pdf):
    """Every link a PDF's pages carry, in order."""
    from pypdf import PdfReader
    links = []
    for page in PdfReader(io.BytesIO(pdf)).pages:
        for annotation in page.get("/Annots") or []:
            action = annotation.get_object().get("/A") or {}
            if action.get("/URI"):
                links.append(str(action["/URI"]))
    return links


def printed_list(rows, label):
    """What block E prints one row per item: the labelled row and the
    unlabelled ones after it."""
    start = next(i for i, (key, _) in enumerate(rows) if key == label)
    values = [rows[start][1]]
    for key, value in rows[start + 1:]:
        if key:
            break
        values.append(value)
    return values


def regulation_card_markup(app):
    return next(
        str(getattr(element.proto, "body", ""))
        for element in app.main
        if getattr(element, "type", "") == "html"
        and 'class="detail-reference-card detail-reference-card--regulations"' in str(
            getattr(element.proto, "body", "")
        )
    )


def final_note_markup(app):
    return next(
        str(getattr(element.proto, "body", ""))
        for element in app.main
        if getattr(element, "type", "") == "html"
        and 'class="detail-final-note"' in str(getattr(element.proto, "body", ""))
    )


def result_attribute(app, name):
    match = re.search(
        rf'{re.escape(name)}="([^"]*)"',
        result_markup(app),
    )
    if match is None:
        raise AssertionError(f"result strip has no {name!r} attribute")
    return match.group(1)


class DetailViewTest(unittest.TestCase):
    """The single-parcel analysis view, driven the way the interface drives it."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.tempdir.name, "results.sqlite")
        shutil.copy2(paths.SEED_DB, self.database)
        self.original_database = paths.DB
        paths.DB = self.database
        st.cache_data.clear()
        # The edict fetch cache is a module-level singleton (canton-wide, keyed
        # by nothing) rather than per-session state, so without this reset a
        # test here would see whatever an earlier, unrelated test's stub left
        # behind instead of its own.
        regulations.reset_news_cache()
        # A publication store configured in the developer's shell would change
        # what block E says; every test starts from "not configured" and sets
        # its own.
        saved = os.environ.pop(planning.ENV, None)
        self.addCleanup(lambda: os.environ.__setitem__(planning.ENV, saved)
                        if saved is not None else os.environ.pop(planning.ENV, None))
        saved_preview = os.environ.pop(detail.PP.ENV, None)
        self.addCleanup(lambda: os.environ.__setitem__(detail.PP.ENV, saved_preview)
                        if saved_preview is not None else os.environ.pop(detail.PP.ENV, None))
        saved_inference = os.environ.pop(planning.ENV_INFERENCE, None)
        self.addCleanup(lambda: os.environ.__setitem__(planning.ENV_INFERENCE, saved_inference)
                        if saved_inference is not None
                        else os.environ.pop(planning.ENV_INFERENCE, None))
        planning.reset_cache()
        self.addCleanup(planning.reset_cache)

        with sqlite3.connect(self.database) as connection:
            (self.bfs, self.parcel, self.delta, self.area,
             self.municipality) = connection.execute(
                "SELECT bfs, parcel, delta, area, municipality FROM parcel_results "
                "ORDER BY delta DESC LIMIT 1"
            ).fetchone()
        self.pid = f"{self.bfs}:{self.parcel}"

    def test_local_preview_is_loaded_in_the_actual_parcel_page_without_collecting(self):
        candidate = {"source_id": {"namespace": detail.PP.NS_AMTSBLATT, "value": "00.999.798"},
                     "title": "Lokale Vorschau im tatsächlichen Detail", "authority": "Gemeinde " + self.municipality,
                     "rubric": "Gemeinden / Bau- und Nutzungsordnung", "published_on": "2026-09-10",
                     "url": "https://amtsblatt.ag.ch/ekab/00.999.798/publikation/"}
        preview = detail.PP.View(status="ok", municipality_queried=True,
                                 items=[detail.PP.PreviewItem(candidate)])
        with patch.object(detail.PP, "state_for", return_value=preview) as load_preview, \
                patch.object(detail.PP.C, "collect_amtsblatt", side_effect=AssertionError("Render must not collect")), \
                patch.object(detail.PP.C, "collect_consultations", side_effect=AssertionError("Render must not collect")):
            app = self.open_detail()
        self.assertFalse(app.exception)
        self.assertIn(candidate["title"], regulation_card_markup(app))
        self.assertIn(">Publikation</span>", regulation_card_markup(app))
        self.assertTrue(any(call.args[0] == self.municipality for call in load_preview.call_args_list))

    def test_block_e_is_computed_on_the_swiss_date_at_the_utc_boundary(self):
        """22:30 UTC the server still says the 7th while Zurich is on the 8th.
        The page must govern and print the parcel's regulation by the Swiss
        date: a BNO taking force on the Swiss day is current on the card and
        governs the sheet — not a future duplicate the way the server-dated
        PDF helpers used to print it."""
        swiss = datetime.date(2026, 10, 8)
        towns = [{"name": self.municipality, "edicts": [
            {"id": 2, "syst_nr": str(self.bfs), "title": "Bau- und Nutzungsordnung",
             "abbreviation": "BNO", "inaction_date": swiss.isoformat(),
             "outaction_date": None, "is_active": True,
             "main_document": {"document_path": "/api/attachments/2"}},
            {"id": 1, "syst_nr": str(self.bfs), "title": "Bau- und Nutzungsordnung",
             "abbreviation": "BNO", "inaction_date": "2024-09-01",
             "outaction_date": None, "is_active": True,
             "main_document": {"document_path": "/api/attachments/1"}},
        ]}]
        # Not `open_detail`'s stub: this fixture needs two editions of the
        # parcel's BNO, the newer one in force only on the Swiss date.
        real_load = regulations.load
        regulations.load = lambda timeout=30: (regulations.parse(towns), "")
        self.addCleanup(lambda: setattr(regulations, "load", real_load))
        regulations.reset_news_cache()
        self.addCleanup(regulations.reset_news_cache)

        class FakeUTCDate(datetime.date):
            @classmethod
            def today(cls):
                return datetime.date(2026, 10, 7)

        sheet_blocks = []
        real_block = detail._regulation_block

        def capture_block(*args, **kwargs):
            block = real_block(*args, **kwargs)
            sheet_blocks.append(block)
            return block

        with patch.object(detail, "date", FakeUTCDate), \
                patch.object(detail.PP, "swiss_today", return_value=swiss), \
                patch.object(detail, "_regulation_block",
                             side_effect=capture_block) as pdf_block, \
                patch.object(detail.R, "for_municipality",
                             wraps=detail.R.for_municipality) as governing:
            app = AppTest.from_file(os.path.join(paths.HERE, "app.py"),
                                    default_timeout=30)
            app.session_state[detail.SELECTED] = self.pid
            app.session_state[navigation.PAGE] = "Analyse"
            app.run()
            self.assertFalse(app.exception)
            wait_for_news()
            app.run()
        self.assertFalse(app.exception)
        # Governing lookup and the PDF block both ran on the Swiss date.
        self.assertEqual(governing.call_args.kwargs.get("today"), swiss)
        self.assertEqual(pdf_block.call_args.kwargs.get("today"), swiss)
        # The actually-rendered sheet block: the same-day edition governs, and
        # is not printed a second time as a future edition.
        _, sheet_rows = sheet_blocks[-1]
        self.assertEqual(sheet_rows[0][0], "BNO")
        self.assertIn("in Kraft seit 08.10.2026", sheet_rows[0][1])
        printed = " ".join(f"{label}: {value}" for label, value in sheet_rows)
        self.assertNotIn("in Kraft ab 08.10.2026", printed)
        # The card agrees: both editions are in force, none upcoming.
        card = regulation_card_markup(app)
        self.assertIn("in Kraft seit 08.10.2026", card)
        self.assertNotIn("Künftig in Kraft", card)
        self.assertIn("2 Vorschriften in Kraft", card.partition("</summary>")[0])

    def tearDown(self):
        paths.DB = self.original_database
        self.tempdir.cleanup()

    def open_detail(self):
        stub_regulations(self, getattr(self, "municipality", None),
                         getattr(self, "bfs", None))
        app = AppTest.from_file(os.path.join(paths.HERE, "app.py"), default_timeout=30)
        app.session_state[detail.SELECTED] = self.pid
        # Analyse is a page now, not the whole script: without this the router
        # draws Screening (the default) and the parcel never opens.
        app.session_state[navigation.PAGE] = "Analyse"
        app.run()
        self.assertFalse(app.exception)
        return app

    def test_all_four_blocks_render_for_a_selected_parcel(self):
        app = self.open_detail()
        self.assertEqual([s.value for s in app.subheader], [])
        self.assertIn("A · Amtliche Grunddaten", facts_markup(app))
        self.assertIn("Resultierende Wohneinheiten", potential_markup(app))
        self.assertIn("C · Residualwert-Rechnung", calculation_header_markup(app))
        self.assertIn("Rechtliche Grundlagen &amp; Quellen", reference_card_markup(app))
        self.assertIn('detail-reference-card--legal" open', reference_card_markup(app))
        self.assertIn("Regulatorische Änderungen", regulation_card_markup(app))
        labels = {n.label for n in app.number_input}
        self.assertIn("Ausnutzungsreserve aBGF m²", labels)
        self.assertIn("Verkaufspreis CHF/m²", labels)
        self.assertIn("Reserve % der Kosten", labels)

        # Block B is pre-filled from the pipeline, not typed in again.
        potential = next(
            n for n in app.number_input
            if n.label == "Ausnutzungsreserve aBGF m²"
        )
        self.assertAlmostEqual(potential.value, self.delta, places=6)


    def test_a_stale_refresh_keeps_the_list_it_already_had(self):
        """`news_state` holding on to the previous result is not enough by
        itself. The render that *starts* the refetch decides what to show from
        its own return value, and an earlier version hardcoded "nothing" there
        — so block E blanked for a frame every twelve hours, which is exactly
        what keeping the old result was written to prevent. Asserting at the
        module level missed it; this asserts on the page."""
        app = self.open_detail()
        wait_for_news()
        app.run()
        self.assertIn("In Kraft getreten", regulation_card_markup(app))

        # Age the cache past its TTL and make the replacement fetch hang, so
        # the render under test is the one that re-arms.
        regulations._FETCHED_AT -= regulations.NEWS_TTL_SECONDS + 1
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def slow(timeout=30):
            started.set()
            release.wait(5)
            return (regulations.parse(EDICTS_FIXTURE), "")

        regulations.load = slow
        app.run()

        self.assertFalse(app.exception)
        self.assertTrue(started.wait(5), "the refetch never started")
        self.assertIn("In Kraft getreten", regulation_card_markup(app))
        self.assertIn("in Kraft seit", regulation_card_markup(app))
        self.assertNotIn("wird geladen", regulation_card_markup(app))

    def test_the_reference_cards_are_native_folded_disclosures(self):
        """The HTML reference uses compact details cards, closed by default."""
        app = self.open_detail()
        legal = reference_card_markup(app)
        regulations = regulation_card_markup(app)
        self.assertIn('<details class="detail-reference-card', legal)
        self.assertIn('detail-reference-card--regulations', regulations)
        self.assertNotIn("<details open", legal)
        self.assertNotIn("<details open", regulations)

    def test_the_result_is_written_above_the_form_that_produces_it(self):
        """The bar has to be drawn before the inputs to sit above them: the
        container is claimed early and filled at the end, once the numbers it
        shows exist. This is the check that the claim did not move — it is the
        one thing keeping the total off the foot of the page now that the bar
        no longer sticks."""
        app = self.open_detail()
        order = list(app.main)
        result = next(
            i for i, element in enumerate(order)
            if element.type == "html"
            and 'class="detail-result-grid"' in str(element.proto.body)
        )
        first_input = min(
            i for i, element in enumerate(order) if element.type == "number_input"
        )
        self.assertLess(result, first_input)

    def test_the_reference_cards_replace_the_legacy_disclaimer(self):
        """The prototype has two compact cards and one small final note."""
        app = self.open_detail()
        self.assertIn(detail.RESULT_CAVEAT, result_markup(app))
        self.assertIn(detail.FINAL_NOTE, final_note_markup(app))
        self.assertNotIn("Der Residualwert bewertet nur", " ".join(
            str(getattr(element.proto, "body", "")) for element in app.main
        ))

    def test_a_negative_result_does_not_grow_the_result_bar(self):
        """The warning used to be its own block inside the bar, which added 72px
        to it in the one case where the inputs underneath most need the room. It
        is the same single caption line now, whatever the sign."""
        app = self.open_detail()
        self.assertEqual(len(app.warning), 0)
        self.assertIn(detail.RESULT_CAVEAT, result_markup(app))

        cost = next(n for n in app.number_input
                    if n.label == "Baukosten CHF/m² aBGF")
        cost.set_value(99999.0).run()
        self.assertFalse(app.exception)
        self.assertLess(float(result_attribute(app, "data-residual")), 0)
        # The strip gained no block: same one line, different words.
        self.assertEqual(len(app.warning), 0)
        markup = result_markup(app)
        self.assertIn(detail.NEGATIVE_CAVEAT, markup)
        self.assertNotIn(detail.RESULT_CAVEAT, markup)

    def test_the_export_is_at_the_top(self):
        """Philipp asked for it in the top right corner. It is built at the end
        of the run — the column it lands in is claimed at the start."""
        app = self.open_detail()
        kinds = [e.type for e in app.main]
        export = next(i for i, e in enumerate(app.main)
                      if e.type == "download_button")
        first_input = min(i for i, k in enumerate(kinds) if k == "number_input")
        self.assertLess(export, first_input)

    def test_a_sheet_that_cannot_be_built_leaves_the_analysis_up(self):
        """The data sheet is built while the page draws. Should reportlab fail
        on it, the export says so; the analysis above it stays."""
        from reportlab.platypus.doctemplate import LayoutError
        with patch.object(report, "build", side_effect=LayoutError("Flowable too large")):
            app = self.open_detail()
        export = next(e for e in app.main if e.type == "download_button")
        self.assertTrue(export.proto.disabled)
        self.assertIn("PDF konnte nicht erstellt werden (LayoutError)", export.proto.help)

    def test_the_calculation_is_not_confined_to_a_column(self):
        """Option C. The split is for the two things read against each other —
        the registers and the assumptions. The calculation is read on its own,
        and it is the one thing on the page that wants width: in half a column
        its longest line, about seventy characters of arithmetic, wrapped the
        step names and pushed the table into its own sideways scroll."""
        app = self.open_detail()
        boxed = [e for column in app.columns for e in column.get("component_instance")
                 if e.proto.component_name.endswith("scope_calculation_table")]
        self.assertEqual(boxed, [], "the calculation is back inside a column")
        # …and it is still on the page at all.
        self.assertIn('class="calc"', calculation_markup(app))
        # Assumptions are preserved inside the prototype's legal/source card.
        self.assertIn("Annahmen &amp; Benchmarks", reference_card_markup(app))

    def test_the_page_does_not_wait_on_a_slow_oereblex(self):
        """The bug this change fixes: block E's fetch used to run on the render
        path, so a slow OEREBlex held up every other block on the page for as
        long as its eight-second timeout. Proven by making the fetch itself
        slow and timing how long `app.run()` actually takes — not by reading a
        docstring and trusting that it still describes what the code does.
        """
        sleep_seconds = 3.0

        def slow_load(timeout=8):
            time.sleep(sleep_seconds)
            return regulations.parse(EDICTS_FIXTURE), ""

        real = regulations.load
        regulations.load = slow_load
        self.addCleanup(lambda: setattr(regulations, "load", real))

        app = AppTest.from_file(os.path.join(paths.HERE, "app.py"), default_timeout=30)
        app.session_state[detail.SELECTED] = self.pid
        app.session_state[navigation.PAGE] = "Analyse"

        started = time.monotonic()
        app.run()
        elapsed = time.monotonic() - started

        self.assertFalse(app.exception)
        self.assertLess(
            elapsed, sleep_seconds / 2,
            f"render took {elapsed:.2f}s while the fetch sleeps {sleep_seconds:.0f}s "
            "— the fetch is back on the render path",
        )
        # Fast because the page actually rendered, not because it gave up early.
        self.assertEqual([s.value for s in app.subheader], [])
        self.assertIn("A · Amtliche Grunddaten", facts_markup(app))
        self.assertIn('class="detail-result-grid"', result_markup(app))
        self.assertIn("wird geladen", regulation_card_markup(app))

        # The fetch is still running in the background when the test's own
        # assertions finish; let it settle before `tearDown`'s next `setUp`
        # tries to reset the cache, rather than leaving a stray thread that
        # would race the next test's reset.
        wait_for_news()

    def test_no_other_municipality_appears_in_block_e(self):
        """The feed is canton-wide. Philipp asked for the parcel's municipality
        and the canton, nothing else: a neighbour's BNO is not a change to this
        parcel's law, not on top and not in a fold underneath either."""
        app = self.open_detail()
        wait_for_news()
        app.run()

        body = regulation_card_markup(app)
        self.assertIn(self.municipality, body)
        for stranger in ("Gipf-Oberfrick", "Dintikon", "Möhlin"):
            if stranger == self.municipality:
                continue
            self.assertNotIn(stranger, body, f"{stranger} appears on this parcel")
        self.assertNotIn("im Kanton", body)
        # One regulation in force here, so one row — a leak would not always
        # carry a stranger's name, but it would always add a row.
        self.assertEqual(body.count('class="detail-regulation-row'), 1)

    def test_block_e_counts_rules_in_force_not_changes(self):
        """227 regulations in force across the canton were headed "227
        Änderungen". A count of rules in force is not a count of changes."""
        app = self.open_detail()
        wait_for_news()
        app.run()

        summary = regulation_card_markup(app).partition("</summary>")[0]
        self.assertIn("1 Vorschrift in Kraft", summary)
        self.assertNotIn("Änderungen ·", summary.replace("Regulatorische Änderungen ·", ""))

    def test_untracked_procedures_point_to_the_filtered_amtsblatt(self):
        """Without a publication store the panel cannot know what is under way —
        and must say so, with the Amtsblatt search already filtered to this
        municipality, rather than look like "nothing is changing"."""
        app = self.open_detail()
        wait_for_news()
        app.run()

        body = regulation_card_markup(app)
        self.assertIn("Übergangslösung", body)
        self.assertIn(escape(amtsblatt.municipal_planning_link(self.municipality)), body)
        self.assertIn(escape(amtsblatt.canton_approvals_link(self.municipality)), body)
        self.assertNotIn("Keine laufenden Verfahren", body)

    def write_store(self, records, stand="2026-10-05", inference=True, **coverage):
        path = os.path.join(self.tempdir.name, "publications.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(dict({"stand": stand, "records": records}, **coverage), handle)
        os.environ[planning.ENV] = path
        if inference:
            os.environ[planning.ENV_INFERENCE] = "1"
        planning.reset_cache()
        return path

    def store_record(self, published_on, authority, title, text="", pub_nr="00.000.001"):
        return {"pub_nr": pub_nr, "published_on": published_on, "authority": authority,
                "rubric": "Gemeinden / Bau- und Nutzungsordnung", "title": title,
                "text": text, "url": f"https://amtsblatt.ag.ch/ekab/{pub_nr}/pdf/"}

    def test_by_default_a_long_store_list_still_opens_with_its_data_sheet(self):
        """Sixty valid publications for the municipality pass the store check;
        the Analyse page draws them and builds its data sheet, over pages."""
        office = f"Gemeinde {self.municipality}"
        path = self.write_store([
            self.store_record(f"2026-0{1 + i % 9}-{10 + i % 18:02d}", office,
                              f"Gestaltungsplan «Areal {i:02d}»; öffentliche Auflage der Teiländerung "
                              "mit Sondervorschriften", pub_nr=f"00.900.{500 + i}")
            for i in range(60)], since="2020-01-01", inference=False)
        self.assertTrue(planning.check(path, today=datetime.date(2026, 10, 5))[0])
        with patch.object(report, "build", wraps=report.build) as build_pdf:
            app = self.open_detail()
            wait_for_news()
            app.run()
            self.assertFalse(app.exception)
            pdf = report.build(**build_pdf.call_args.kwargs)
        export = next(e for e in app.main if e.type == "download_button")
        self.assertFalse(export.proto.disabled)
        from pypdf import PdfReader
        pages = PdfReader(io.BytesIO(pdf)).pages
        self.assertIn("Areal 59", " ".join(p.extract_text() or "" for p in pages))
        self.assertGreater(len(pages), 2)

    def test_by_default_each_publication_is_an_item_of_its_own(self):
        """Version 1: what the Amtsblatt printed, one publication per row — its
        date, its title, the original. No step read from it, no chain of
        publications, no status of today, OEREBlex apart."""
        office = f"Gemeinde {self.municipality}"
        self.write_store([
            self.store_record("2024-06-27", office, 'Gestaltungsplan "Rain"; öffentliche Auflage',
                              "Die Akten liegen vom 28. Juni 2024 bis 29. Juli 2024 auf.",
                              pub_nr="00.900.201"),
            self.store_record("2024-09-05", office, 'Gestaltungsplan "Rain"; Beschluss',
                              "Der Gemeinderat hat am 2. September 2024 den Plan beschlossen.",
                              pub_nr="00.900.202"),
        ], since="2020-01-01", inference=False)
        app = self.open_detail()
        wait_for_news()
        app.run()
        body = regulation_card_markup(app)
        summary = body.partition("</summary>")[0]
        self.assertIn("2 Publikationen im Amtsblatt-Verzeichnis", summary)
        self.assertIn(f"Gemeinde {self.municipality} · Vorschriften (OEREBlex)", body)
        self.assertIn(f"Gemeinde {self.municipality} · Publikationen im Amtsblatt", body)
        self.assertIn("Gestaltungsplan &quot;Rain&quot;; öffentliche Auflage", body)
        self.assertIn("Gestaltungsplan &quot;Rain&quot;; Beschluss", body)
        self.assertIn('detail-regulation-status--step" title="Publikation im Amtsblatt, wie publiziert — kein '
                      'geprüfter Verfahrensschritt, nicht der heutige Stand">Publikation<', body)
        self.assertEqual(re.findall(r'detail-regulation-status--step"[^>]*>([^<]+)<', body),
                         ["Publikation", "Publikation"])
        self.assertIn("https://amtsblatt.ag.ch/ekab/00.900.201/pdf/", body)
        self.assertIn("https://amtsblatt.ag.ch/ekab/00.900.202/pdf/", body)
        self.assertIn("nicht der heutige Stand des Verfahrens", body)
        for inferred in ("Entwurf in Auflage", "Beschlossen", "Stand unbestätigt", "Genehmigt,",
                         "Planungszone in Kraft", "vorgesehen", "Beschluss vom", "(abgelaufen)",
                         "Frist "):
            self.assertNotIn(inferred, body, inferred)

    def test_by_default_no_step_is_read_from_a_title(self):
        office = f"Gemeinde {self.municipality}"
        self.write_store([
            self.store_record("2026-09-01", office, "Öffentliche Auflage Gestaltungsplan «Giessi»",
                              "Die erste Fassung wurde 2023 nicht genehmigt.", pub_nr="00.900.211"),
            self.store_record("2026-09-02", office, "Teiländerung Dorf; Beschluss und Weiterleitung zur "
                              "Genehmigung an den Kanton", pub_nr="00.900.212"),
            self.store_record("2026-09-03", office, "Gestaltungsplan «Heidegrabe»; Genehmigung",
                              pub_nr="00.900.213"),
        ], since="2020-01-01", inference=False)
        app = self.open_detail()
        wait_for_news()
        app.run()
        body = regulation_card_markup(app)
        chips = re.findall(r'detail-regulation-status--step"[^>]*>([^<]+)<', body)
        self.assertEqual(chips, ["Publikation", "Publikation", "Publikation"])

    def test_by_default_an_approval_is_not_merged_into_an_edition(self):
        self.write_store([{
            # Within the merge window of the stubbed edition (in force 01.09.2024):
            # the inference path would fold it into that row.
            "pub_nr": "00.900.203", "published_on": "2024-09-10", "authority": "Regierungsrat",
            "rubric": "Kanton / Raumplanung", "title": "Genehmigung von Nutzungsplänen",
            "text": f"Gemeinde {self.municipality}; Allgemeine Nutzungsplanung, Gesamtrevision; "
                    "Genehmigung",
            "url": "https://amtsblatt.ag.ch/ekab/00.900.203/pdf/"}], since="2020-01-01",
            inference=False)
        app = self.open_detail()
        wait_for_news()
        app.run()
        body = regulation_card_markup(app)
        self.assertNotIn("Genehmigung publiziert", body)
        self.assertIn("Allgemeine Nutzungsplanung, Gesamtrevision", body)
        self.assertIn("aus: Genehmigung von Nutzungsplänen", body)

    def test_by_default_a_canton_wide_change_is_an_item_in_its_own_group(self):
        self.write_store([{
            "pub_nr": "00.900.204", "published_on": "2026-09-15",
            "authority": "Departement Bau, Verkehr und Umwelt",
            "rubric": "Kanton / Anhörungs- und Mitwirkungsverfahren",
            "title": "Teilrevision Baugesetz (BauG); Anhörung", "text": "",
            "url": "https://amtsblatt.ag.ch/ekab/00.900.204/pdf/"}], since="2020-01-01",
            inference=False)
        app = self.open_detail()
        wait_for_news()
        app.run()
        body = regulation_card_markup(app)
        canton = body[body.index("Kanton Aargau · Publikationen im Amtsblatt"):]
        self.assertIn("Teilrevision Baugesetz (BauG); Anhörung", canton)
        self.assertIn(">Publikation</span>", canton)
        self.assertNotIn(">Anhörung</span>", canton)
        self.assertNotIn("Entwurf in Anhörung", body)

    def test_with_a_store_each_stage_is_shown_apart(self):
        """In force, decided but not yet in force, and in Mitwirkung or Auflage
        are three different answers to "can I build on the current rules", and
        an area plan says which area it covers."""
        office = f"Gemeinde {self.municipality}"
        self.write_store([
            self.store_record("2026-09-01", office,
                              "Öffentliche Mitwirkung zum Gestaltungsplan «Testareal»",
                              "Mitwirkung vom 2. September 2026 bis 30. Oktober 2026.",
                              pub_nr="00.900.001"),
            self.store_record("2026-06-01", office, 'Gestaltungsplan "Probefeld"; Beschluss',
                              pub_nr="00.900.002"),
            self.store_record("2026-07-01", "Gemeinde Möhlin",
                              "Öffentliche Auflage Gestaltungsplan «Nachbarfeld»",
                              pub_nr="00.900.003"),
        ])
        app = self.open_detail()
        wait_for_news()
        app.run()

        body = regulation_card_markup(app)
        self.assertIn("Entwurf in Mitwirkung", body)
        self.assertIn(">Beschlossen</span>", body)
        self.assertIn("Gestaltungsplan «Testareal»", body)
        self.assertIn("Gebiet «Testareal»", body)
        self.assertIn("01.09.2026", body)
        self.assertIn("https://amtsblatt.ag.ch/ekab/00.900.001/pdf/", body)
        self.assertNotIn("Nachbarfeld", body)
        self.assertNotIn("Übergangslösung", body)

    def test_old_procedures_stay_listed_with_what_is_known(self):
        """Nothing is hidden for its age. A decision from 2024 is a decision;
        a display that ended in 2019 is not "in Auflage" — its state is left
        unverified rather than guessed."""
        office = f"Gemeinde {self.municipality}"
        self.write_store([
            self.store_record("2024-01-15", office, 'Gestaltungsplan "Altfeld"; Beschluss',
                              pub_nr="00.900.020"),
            self.store_record("2019-05-02", office, "Öffentliche Auflage Beitragsplan Ringweg",
                              "Der Beitragsplan liegt vom 3. Mai 2019 bis 3. Juni 2019 auf.",
                              pub_nr="00.900.021"),
        ], since="2019-01-01")
        app = self.open_detail()
        wait_for_news()
        app.run()

        body = regulation_card_markup(app)
        self.assertIn("Gestaltungsplan «Altfeld»", body)
        self.assertIn(">Beschlossen</span>", body)
        ringweg = body[body.index("Beitragsplan Ringweg"):][:700]
        self.assertIn("Stand unbestätigt", ringweg)
        self.assertIn("Frist 03.05.2019–03.06.2019 (abgelaufen)", ringweg)
        self.assertNotIn("Entwurf in Auflage", body)

    def test_each_row_shows_title_status_and_an_arrow_to_the_source(self):
        """Philipp's choice (2026-10-05): the publication's title, a status
        badge, and an arrow that opens the original — no popup, no copy."""
        self.write_store([self.store_record(
            "2026-09-01", f"Gemeinde {self.municipality}",
            "Öffentliche Auflage Gestaltungsplan «Testareal»",
            "Die Akten liegen vom 2. September 2026 bis 30. Oktober 2026 auf.",
            pub_nr="00.900.070")], since="2026-01-01")
        app = self.open_detail()
        wait_for_news()
        app.run()

        body = regulation_card_markup(app)
        self.assertIn('<div class="detail-reference-title">Gestaltungsplan «Testareal»</div>', body)
        self.assertIn('detail-regulation-status--amber">Entwurf in Auflage</span>', body)
        self.assertIn('<a class="detail-regulation-open" '
                      'href="https://amtsblatt.ag.ch/ekab/00.900.070/pdf/" target="_blank"', body)
        self.assertIn('aria-label="Publikation öffnen">↗</a>', body)
        self.assertNotIn("Relevant", body)

    def test_a_search_link_is_labelled_as_a_search(self):
        """A record whose source is a search page gets a link that says so —
        the arrow is reserved for the publication itself."""
        search = amtsblatt.canton_approvals_link(self.municipality)
        record = self.store_record("2026-09-01", f"Gemeinde {self.municipality}",
                                   'Gestaltungsplan "Suchfeld"; Beschluss', pub_nr="00.900.071")
        record["url"] = search
        self.write_store([record], since="2026-01-01")
        app = self.open_detail()
        wait_for_news()
        app.run()

        body = regulation_card_markup(app)
        row = body[body.index("Gestaltungsplan «Suchfeld»"):][:900]
        self.assertIn('class="detail-regulation-open detail-regulation-open--search"', row)
        self.assertIn(">Suche ↗</a>", row)
        self.assertNotIn('aria-label="Publikation öffnen"', row)

    def test_a_link_to_another_site_says_it_is_not_the_publication(self):
        record = self.store_record("2026-09-01", f"Gemeinde {self.municipality}",
                                   'Gestaltungsplan "Webfeld"; Beschluss', pub_nr="00.900.072")
        record["url"] = "https://www.example-gemeinde.ch/planung/webfeld"
        self.write_store([record], since="2026-01-01")
        app = self.open_detail()
        wait_for_news()
        app.run()

        row = regulation_card_markup(app)
        row = row[row.index("Gestaltungsplan «Webfeld»"):][:900]
        self.assertIn(">Link ↗</a>", row)
        self.assertNotIn(">↗</a>", row)

    def test_publications_that_could_not_be_attributed_change_the_summary(self):
        """The collapsed card must not read "keine" when a publication names
        this municipality and could not be attributed."""
        self.write_store([{
            "pub_nr": "00.900.090", "published_on": "2026-09-01",
            "authority": "Abteilung Raumentwicklung", "rubric": "Kanton / Raumplanung",
            "title": "Genehmigung von Sondernutzungsplänen",
            "text": f"Genehmigt wurde ein Plan im Gebiet der Gemeinden {self.municipality} und Möhlin.",
            "url": "https://amtsblatt.ag.ch/ekab/00.900.090/pdf/"}], since="2026-01-01")
        app = self.open_detail()
        wait_for_news()
        app.run()

        summary = regulation_card_markup(app).partition("</summary>")[0]
        self.assertIn("1 nicht zuordenbar", summary)
        self.assertNotIn("keine", summary)

    def test_a_canton_wide_change_reaches_a_parcel_it_does_not_name(self):
        """A Baugesetz revision under consultation names no municipality and
        applies to this parcel all the same — in its own section."""
        self.write_store([{
            "pub_nr": "00.900.080", "published_on": "2026-09-15",
            "authority": "Departement Bau, Verkehr und Umwelt",
            "rubric": "Kanton / Anhörungs- und Mitwirkungsverfahren",
            "title": "Teilrevision Baugesetz (BauG); Anhörung",
            "text": "Anhörung zur Teilrevision des Baugesetzes vom 15. September 2026 bis "
                    "15. Dezember 2026.",
            "url": "https://amtsblatt.ag.ch/ekab/00.900.080/pdf/"}], since="2026-01-01")
        app = self.open_detail()
        wait_for_news()
        app.run()

        body = regulation_card_markup(app)
        canton = body[body.index('<div class="detail-regulation-group">Kanton Aargau</div>'):]
        self.assertIn("Teilrevision Baugesetz (BauG)", canton)
        self.assertIn(">Entwurf in Anhörung</span>", canton)
        self.assertIn('href="https://amtsblatt.ag.ch/ekab/00.900.080/pdf/"', canton)

    def test_the_regulation_in_force_opens_its_document(self):
        app = self.open_detail()
        wait_for_news()
        app.run()

        body = regulation_card_markup(app)
        self.assertIn('detail-regulation-status--grey">In Kraft getreten</span>', body)
        self.assertIn('<a class="detail-regulation-open" '
                      'href="https://oereblex.ag.ch/api/attachments/99" target="_blank"', body)

    def test_an_unreadable_store_is_not_shown_as_no_procedures(self):
        os.environ[planning.ENV] = os.path.join(self.tempdir.name, "missing.json")
        planning.reset_cache()
        app = self.open_detail()
        wait_for_news()
        app.run()

        body = regulation_card_markup(app)
        self.assertIn("nicht lesbar", body)
        self.assertNotIn("Keine laufenden Verfahren", body)

    def test_a_store_with_nothing_here_says_none_and_since_when(self):
        """"None" is only worth saying with the period it covers: a store fed
        by a daily subscription knows nothing from before its first day."""
        self.write_store([
            self.store_record("2026-07-01", "Gemeinde Möhlin",
                              "Öffentliche Auflage Gestaltungsplan «Nachbarfeld»"),
        ], since="2026-06-01")
        app = self.open_detail()
        wait_for_news()
        app.run()

        body = regulation_card_markup(app)
        self.assertIn("Keine laufenden Verfahren", body)
        self.assertIn("erfasst seit 01.06.2026", body)
        self.assertIn("Stand 05.10.2026", body)
        self.assertNotIn("Nachbarfeld", body)

    def test_a_municipality_the_store_does_not_cover_is_not_tracked(self):
        self.write_store([], since="2026-06-01", municipalities=["Seon"])
        app = self.open_detail()
        wait_for_news()
        app.run()

        body = regulation_card_markup(app)
        self.assertIn("noch nicht erfasst", body)
        self.assertIn(escape(amtsblatt.municipal_planning_link(self.municipality)), body)
        self.assertNotIn("Keine laufenden Verfahren", body)

    def test_an_unclear_step_is_not_listed_as_under_way(self):
        self.write_store([self.store_record(
            "2026-09-01", f"Gemeinde {self.municipality}",
            "Mitteilung zum Gestaltungsplan «Randfeld»")], since="2026-01-01")
        app = self.open_detail()
        wait_for_news()
        app.run()

        body = regulation_card_markup(app)
        self.assertIn(">Stand unbestätigt</span>", body)
        self.assertNotIn("Entwurf in", body)

    def test_block_e_shows_a_loading_state_then_the_edicts(self):
        """The other half of the fix, not just its speed: the placeholder has
        to actually say something ("Wird geladen …"), and the real content has
        to actually arrive once the fetch answers — a page that is merely fast
        because block E silently never fills in would pass the timing test
        above and still be broken."""
        def slow_load(timeout=8):
            time.sleep(0.3)
            return regulations.parse(EDICTS_FIXTURE), ""

        real = regulations.load
        regulations.load = slow_load
        self.addCleanup(lambda: setattr(regulations, "load", real))

        app = AppTest.from_file(os.path.join(paths.HERE, "app.py"), default_timeout=30)
        app.session_state[detail.SELECTED] = self.pid
        app.session_state[navigation.PAGE] = "Analyse"
        app.run()
        self.assertFalse(app.exception)

        self.assertIn("wird geladen", regulation_card_markup(app))
        self.assertNotIn("keine gültige Rechtsvorschrift", regulation_card_markup(app))

        wait_for_news()
        app.run()
        self.assertFalse(app.exception)

        body = regulation_card_markup(app)
        # The fixture carries no entry for this parcel's municipality, so the
        # settled answer is "none listed" — and still only about this one.
        self.assertIn("keine gültige Rechtsvorschrift", body)
        self.assertNotIn("Gipf-Oberfrick", body)
        self.assertNotIn("wird geladen", body)

    def test_the_change_list_says_when_it_could_not_be_fetched(self):
        """The dangerous failure mode for this panel is the silent one: an empty
        change list reads as "nothing has changed lately", which is the opposite
        of "the canton did not answer". Block D has to be unaffected — it comes
        out of the parcel's own ÖREB extract, not this request.

        The fetch is asynchronous now — the first render always shows the
        loading placeholder rather than the outcome, on purpose, which is the
        entire point of this fix. So this test waits for the background fetch
        the way a browser's `run_every` tick would, then reruns once to pick
        up the settled state, before checking the panel says what it says.
        """
        real = regulations.load
        regulations.load = lambda timeout=30: ([], "URLError: [Errno 8] nodename nor servname provided")
        self.addCleanup(lambda: setattr(regulations, "load", real))
        st.cache_data.clear()

        app = AppTest.from_file(os.path.join(paths.HERE, "app.py"), default_timeout=30)
        app.session_state[detail.SELECTED] = self.pid
        app.session_state[navigation.PAGE] = "Analyse"
        app.run()
        self.assertFalse(app.exception)

        wait_for_news()
        app.run()
        self.assertFalse(app.exception)

        body = regulation_card_markup(app)
        self.assertIn("nicht abrufbar", body)
        self.assertIn("nodename nor servname", body)
        # …and the page is otherwise whole.
        self.assertIn("Regulatorische Änderungen", body)
        self.assertIn("Rechtliche Grundlagen", reference_card_markup(app))
        self.assertIn('class="detail-result-grid"', result_markup(app))

    def test_the_parcel_carries_its_own_regulation_date(self):
        """What block D cannot say: since when. It is the first line of E, above
        the canton-wide list, because it is the only line about this parcel.

        Waits for the background fetch and reruns once, same reason as above:
        the first render is always the loading placeholder now."""
        app = self.open_detail()
        wait_for_news()
        app.run()
        self.assertFalse(app.exception)
        body = regulation_card_markup(app)
        self.assertIn(self.municipality, body)
        self.assertIn("In Kraft getreten", body)
        self.assertIn("in Kraft seit 01.09.2024", body)
        # Philipp, 2026-10-05: no "Relevant" marker — the municipality filter
        # already says which regulations are this parcel's.
        self.assertNotIn("Relevant", body)
        self.assertNotIn("relevant für diese Parzelle", body)

    def test_each_step_carries_the_formula_that_produced_it(self):
        """Philipp asked for the reasoning to be visible on hover, and for the
        tooltip not to be a second copy that can go stale. It is rendered from
        the rule that computed the number."""
        app = self.open_detail()
        body = calculation_markup(app)
        self.assertIn("verkaufsflaeche * verkaufspreis", body)
        self.assertIn("potenzial_gf * baukosten_pro_m2", body)
        self.assertIn('class="calc__name"', body)
        # The help on the input says where its number goes, read off the formulas.
        price = next(n for n in app.number_input if n.label == "Verkaufspreis CHF/m²")
        self.assertIn("{verkaufspreis}", price.help)
        self.assertIn("Verkaufserlös", price.help)

    def test_the_list_is_not_drawn_while_a_parcel_is_open(self):
        """A conditional view, not a second page — and not both at once."""
        app = self.open_detail()
        self.assertEqual(len(app.dataframe), 0)
        # The app-level `st.title` is gone: `shell.header` draws the same
        # "Verdichtungspotenzial" identity as HTML in the sticky bar instead,
        # so it no longer shows up as an `st.title` widget at all — only
        # detail's own title for the open parcel does.
        titles = [t.value for t in app.title]
        self.assertEqual(titles, ["Parzelle 574"])

    def test_manual_amount_event_updates_screen_pdf_and_reset_once(self):
        app = self.open_detail()
        before = float(result_attribute(app, "data-residual"))
        price = next(n for n in app.number_input if n.label == "Verkaufspreis CHF/m²")
        price.set_value(9000).run()
        calculated = float(result_attribute(app, "data-residual"))
        event = {"eventId": "manual-cost-1", "type": "override", "parcel": self.pid,
                 "field": "baukosten", "value": "CHF 1’000’000"}
        render_component = detail.UI.calculation_table

        def render_with_event(html, **kwargs):
            render_component(html, **kwargs)
            return event

        with patch.object(detail.UI, "calculation_table", side_effect=render_with_event):
            with patch.object(report, "build", wraps=report.build) as build_pdf:
                app.run()
                self.assertFalse(app.exception)
                screen_value = float(result_attribute(app, "data-residual"))
                steps = build_pdf.call_args.kwargs["steps"]
                self.assertEqual(screen_value, E.land_value(steps))
                self.assertGreater(screen_value, calculated)
                self.assertGreater(calculated, before)
                self.assertEqual(next(s.value for s in steps if s.key == "baukosten"),
                                 -1_000_000)
                self.assertIn("Manuell überschrieben", calculation_markup(app))
                # Read actual generated PDF bytes, not only the report arguments.
                from pypdf import PdfReader
                pdf = report.build(**build_pdf.call_args.kwargs)
                text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
                self.assertIn("Manuell überschrieben", text)
                self.assertIn(E.chf(screen_value), text)
            # Components retain their last event. Reset must not apply it again.
            reset = next(b for b in app.button if b.label == "1 überschrieben · zurücksetzen")
            reset.click().run()
            self.assertFalse(app.exception)
            self.assertEqual(float(result_attribute(app, "data-residual")), calculated)
            self.assertEqual(app.session_state[detail.OWN_STORE]["sale"], 9000)
            self.assertNotIn(self.pid, app.session_state[detail.OVERRIDE_STORE])

    def test_manual_overrides_stay_with_the_parcel_and_standardwerte_clears_them(self):
        app = self.open_detail()
        baseline = result_attribute(app, "data-residual")
        app.session_state[detail.OVERRIDE_STORE] = {
            self.pid: {"reserve": 0}, "another-parcel": {"baukosten": 123},
        }
        app.run()
        self.assertNotEqual(result_attribute(app, "data-residual"), baseline)
        next(b for b in app.button if b.label == "Standardwerte").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(result_attribute(app, "data-residual"), baseline)
        self.assertEqual(app.session_state[detail.OVERRIDE_STORE],
                         {"another-parcel": {"baukosten": 123}})

    def test_back_returns_to_the_list(self):
        app = self.open_detail()
        back = next(b for b in app.button if b.label == "Screening")
        back.click().run()
        self.assertFalse(app.exception)
        self.assertNotIn(detail.SELECTED, app.session_state)
        # Dropping the parcel key is no longer enough now that Analyse is a
        # page of its own: without the navigation the reader would still be
        # standing on it, looking at its empty state.
        self.assertEqual(app.session_state[navigation.PAGE], "Screening")
        # The hotlist is back. Counting tables would be counting the wrong
        # thing: a second one appears as soon as the cadastre has excluded a
        # parcel from the shortlist, which is data, not behaviour.
        self.assertIn("Adresse", app.dataframe[0].value.columns)

    def test_editing_an_assumption_recalculates_live(self):
        """No recalculate button: the residual value has to follow the input on
        the same rerun, which is the whole interaction the brief describes."""
        app = self.open_detail()
        before = float(result_attribute(app, "data-residual"))

        price = next(n for n in app.number_input if n.label == "Verkaufspreis CHF/m²")
        price.set_value(price.value * 2).run()
        self.assertFalse(app.exception)
        after = float(result_attribute(app, "data-residual"))
        self.assertNotEqual(before, after)
        self.assertGreater(after, before)

    def test_unit_count_follows_the_assumed_unit_size(self):
        app = self.open_detail()
        size = next(n for n in app.number_input if n.label == "Ø Wohnungsgrösse m²")
        size.set_value(180.0).run()
        self.assertFalse(app.exception)
        self.assertAlmostEqual(float(potential_units(app)), self.delta / 180.0, places=1)

    def test_own_numbers_follow_the_user_to_the_next_parcel(self):
        """A developer's construction cost does not change because they clicked
        a different row. Keeping these per parcel would mean retyping seven
        numbers on every lead."""
        app = self.open_detail()
        price = next(n for n in app.number_input if n.label == "Verkaufspreis CHF/m²")
        price.set_value(9999.0).run()

        with sqlite3.connect(self.database) as con:
            other = con.execute(
                "SELECT bfs, parcel, delta FROM parcel_results "
                "WHERE bfs || ':' || parcel <> ? ORDER BY delta DESC LIMIT 1",
                (self.pid,),
            ).fetchone()
        next(b for b in app.button if b.label == "Screening").click().run()
        # Back now lands on Screening, so choosing the next parcel means both
        # the key and the page — the parcel key alone stopped being the whole
        # navigation when Analyse became a page.
        app.session_state[detail.SELECTED] = f"{other[0]}:{other[1]}"
        app.session_state[navigation.PAGE] = "Analyse"
        app.run()
        self.assertFalse(app.exception)

        price = next(n for n in app.number_input if n.label == "Verkaufspreis CHF/m²")
        self.assertEqual(price.value, 9999.0)
        # …but the parcel's own figures do not follow it.
        potential = next(
            n for n in app.number_input
            if n.label == "Ausnutzungsreserve aBGF m²"
        )
        self.assertAlmostEqual(potential.value, other[2], places=6)
        self.assertNotAlmostEqual(potential.value, self.delta, places=6)

    def test_reset_restores_the_published_benchmarks(self):
        app = self.open_detail()
        price = next(n for n in app.number_input if n.label == "Verkaufspreis CHF/m²")
        price.set_value(9999.0).run()
        next(b for b in app.button if b.label == "Standardwerte").click().run()
        self.assertFalse(app.exception)
        price = next(n for n in app.number_input if n.label == "Verkaufspreis CHF/m²")
        self.assertEqual(price.value, E.BENCHMARKS["sale_price_chf_m2"].value)

    def test_c_reset_keeps_b_inputs_and_other_parcel_state(self):
        app = self.open_detail()
        next(n for n in app.number_input if n.label == "Ausnutzungsreserve aBGF m²").set_value(1200.0).run()
        next(n for n in app.number_input if n.label == "Ø Wohnungsgrösse m²").set_value(100.0).run()
        next(n for n in app.number_input if n.label == "Verkaufspreis CHF/m²").set_value(9999.0).run()
        app.session_state[detail.STORE]["other"] = {"gf": 789, "demolish": False}
        app.session_state[detail.OVERRIDE_STORE] = {self.pid: {"reserve": 0}, "other": {"baukosten": 123}}
        app.run()
        next(b for b in app.button if b.label == "Standardwerte").click().run()
        self.assertFalse(app.exception)
        values = {n.label: n.value for n in app.number_input}
        self.assertEqual(values["Ausnutzungsreserve aBGF m²"], 1200.0)
        self.assertEqual(values["Ø Wohnungsgrösse m²"], 100.0)
        self.assertEqual(values["Verkaufspreis CHF/m²"], E.BENCHMARKS["sale_price_chf_m2"].value)
        self.assertEqual(app.session_state[detail.STORE]["other"], {"gf": 789, "demolish": False})
        self.assertEqual(app.session_state[detail.OVERRIDE_STORE], {"other": {"baukosten": 123}})

    def test_demolition_note_follows_checkbox_and_manual_cost(self):
        with sqlite3.connect(self.database) as con:
            con.execute("UPDATE parcel_results SET existing=107, buildings=1 WHERE bfs=? AND parcel=?", (self.bfs, self.parcel))
        app = self.open_detail()
        self.assertIn("wird ersetzt", potential_markup(app))
        self.assertIn("Abbruchkosten in Block C berücksichtigt", potential_markup(app))
        next(c for c in app.checkbox if c.label == "Bestehendes Gebäude abbrechen").uncheck().run()
        self.assertFalse(app.exception)
        self.assertIn("bleibt erhalten", potential_markup(app))
        self.assertIn("Keine Abbruchkosten", potential_markup(app))
        self.assertNotIn("wird ersetzt", potential_markup(app))
        app.session_state[detail.OVERRIDE_STORE] = {self.pid: {"abbruchkosten": 2500}}
        app.run()
        self.assertIn("manuell auf CHF 2’500 gesetzt", potential_markup(app))
        self.assertNotIn("Keine Abbruchkosten", potential_markup(app))
        app.session_state[detail.OVERRIDE_STORE] = {self.pid: {"abbruchkosten": 0}}
        app.run()
        self.assertIn("manuell auf CHF 0 gesetzt", potential_markup(app))
        next(b for b in app.button if b.label == "Standardwerte").click().run()
        self.assertIn("bleibt erhalten", potential_markup(app))
        self.assertIn("Keine Abbruchkosten", potential_markup(app))

    def test_source_archive_is_lazy_and_available_beside_legal_sources(self):
        sources = (detail.source_downloads.Source(
            "Bau- und Nutzungsordnung", "https://oereblex.ag.ch/api/attachments/1038",
        ),)
        with patch.object(detail.source_downloads, "build_archive") as build, \
             patch.object(detail.source_downloads, "references", return_value=sources) as refs:
            app = self.open_detail()
            self.assertFalse(app.exception)
            build.assert_not_called()
            downloads = [e for e in app.main if e.type == "download_button"]
            archive = next(e for e in downloads if e.proto.label == "Alle herunterladen")
            self.assertTrue(archive.proto.deferred_file_id)
            self.assertFalse(archive.proto.disabled)
            refs.return_value = ()
            app.run()
            archive = next(e for e in app.main
                           if e.type == "download_button" and e.proto.label == "Alle herunterladen")
            self.assertTrue(archive.proto.disabled)
            build.assert_not_called()

    def test_invalid_amount_is_reported_and_acknowledged_once(self):
        app = self.open_detail()
        before = result_attribute(app, "data-residual")
        event = {"eventId": "invalid-amount", "type": "override", "parcel": self.pid,
                 "field": "baukosten", "value": "not-a-number"}
        render_component = detail.UI.calculation_table

        def render_with_event(html, **kwargs):
            render_component(html, **kwargs)
            return event

        with patch.object(detail.UI, "calculation_table", side_effect=render_with_event):
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(result_attribute(app, "data-residual"), before)
            self.assertEqual(len(app.error), 1)
            args = json.loads(calculation_component(app).proto.json_args)
            self.assertEqual(args["acknowledged_event_id"], "invalid-amount")

    def test_auf_merkliste_saves_the_open_parcel(self):
        """The button this test protects: reading a parcel's analysis and
        deciding it is worth pursuing used to mean navigating back to
        Screening, finding the row again, and ticking it there. Asserted
        against the workflow table, not the widget — a widget assertion would
        pass even if the click wrote nothing at all."""
        app = self.open_detail()
        next(b for b in app.button if b.label == "Auf Merkliste").click().run()
        self.assertFalse(app.exception)
        saved = workflow.load(self.database)
        hit = saved[(saved["bfs"] == self.bfs) & (saved["parcel"] == self.parcel)]
        self.assertEqual(len(hit), 1)
        self.assertTrue(bool(hit.iloc[0]["saved"]))

    def test_an_already_saved_parcel_shows_the_removal_state(self):
        """A button that still offers to add something already added is a lie
        the user finds out about by clicking it — the label has to change,
        not merely the state underneath it."""
        workflow.set_saved([(self.bfs, self.parcel)], True, self.database)
        app = self.open_detail()
        labels = [b.label for b in app.button]
        self.assertNotIn("Auf Merkliste", labels)
        self.assertTrue(
            any("entfernen" in label.lower() for label in labels),
            f"no removal control among {labels!r}",
        )

    def test_clicking_the_removal_state_takes_it_off_the_shortlist(self):
        workflow.set_saved([(self.bfs, self.parcel)], True, self.database)
        app = self.open_detail()
        removal = next(b for b in app.button if "entfernen" in b.label.lower())
        removal.click().run()
        self.assertFalse(app.exception)
        saved = workflow.load(self.database)
        hit = saved[(saved["bfs"] == self.bfs) & (saved["parcel"] == self.parcel)]
        self.assertEqual(len(hit), 1)
        self.assertFalse(bool(hit.iloc[0]["saved"]))

    def test_the_merkliste_action_does_not_disturb_the_rest_of_the_page(self):
        """Toggling the shortlist is a side action taken from the page, not a
        navigation away from it — the parcel has to still be open and every
        block still on screen after the rerun the click causes."""
        app = self.open_detail()
        next(b for b in app.button if b.label == "Auf Merkliste").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state[detail.SELECTED], self.pid)
        self.assertEqual([s.value for s in app.subheader], [])
        self.assertIn("Regulatorische Änderungen", regulation_card_markup(app))
        self.assertIn("A · Amtliche Grunddaten", facts_markup(app))

    def test_a_selection_that_no_longer_exists_says_so(self):
        """A recompute can drop a parcel out of the table while it is open."""
        stub_regulations(self)
        app = AppTest.from_file(os.path.join(paths.HERE, "app.py"), default_timeout=30)
        app.session_state[detail.SELECTED] = "9999:12345"
        app.session_state[navigation.PAGE] = "Analyse"
        app.run()
        self.assertFalse(app.exception)
        self.assertTrue(any("steht nicht mehr" in w.value for w in app.warning))

class DetailEdgeCaseTest(unittest.TestCase):
    """Rows the current canton-wide result set happens not to contain. The code
    paths exist and will be hit the first time the cadastre answers badly, so
    they are exercised against a database doctored to contain them rather than
    left to be discovered in front of Philipp."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.tempdir.name, "results.sqlite")
        shutil.copy2(paths.SEED_DB, self.database)
        self.original_database = paths.DB
        paths.DB = self.database
        # `load()` is cached by Streamlit with no arguments, so its key does not
        # change when the test points `paths.DB` at a different file: without
        # this, a doctored database is read as the previous test's data and the
        # test passes or fails for the wrong reason.
        st.cache_data.clear()
        # Same reasoning as `st.cache_data.clear()` above, for the edict fetch:
        # it is a module-level singleton, not per-session state, so a leftover
        # result from an earlier test would otherwise leak in here.
        regulations.reset_news_cache()
        with sqlite3.connect(self.database) as con:
            self.bfs, self.parcel, self.egrid = con.execute(
                "SELECT bfs, parcel, egrid FROM parcel_results ORDER BY delta DESC LIMIT 1"
            ).fetchone()
        self.pid = f"{self.bfs}:{self.parcel}"

    def tearDown(self):
        paths.DB = self.original_database
        self.tempdir.cleanup()

    def edit(self, sql, *args):
        with sqlite3.connect(self.database) as con:
            con.execute(sql, args)

    def open_detail(self):
        stub_regulations(self, getattr(self, "municipality", None),
                         getattr(self, "bfs", None))
        app = AppTest.from_file(os.path.join(paths.HERE, "app.py"), default_timeout=30)
        app.session_state[detail.SELECTED] = self.pid
        # Analyse is a page now, not the whole script: without this the router
        # draws Screening (the default) and the parcel never opens.
        app.session_state[navigation.PAGE] = "Analyse"
        app.run()
        self.assertFalse(app.exception)
        return app

    def text(self, app):
        markdown = " ".join(m.value for m in app.markdown)
        html = " ".join(
            str(getattr(element.proto, "body", ""))
            for element in app.main
            if getattr(element, "type", "") == "html"
        )
        return f"{markdown} {html}"

    def test_parcel_without_an_egrid_says_it_cannot_be_asked(self):
        self.edit("UPDATE parcel_results SET egrid='' WHERE bfs=? AND parcel=?",
                  self.bfs, self.parcel)
        body = self.text(self.open_detail())
        self.assertIn("kein EGRID", body)
        # …and offers no cadastre link that would 404 on an empty identifier.
        self.assertNotIn("oereb/extract/pdf/?EGRID=)", body)
        self.assertNotIn("EGRID=&", body)

    #: The shape the cadastre returns, trimmed to what `oereb.details` keeps.
    EXTRACT = {
        "municipality": "Egliswil", "bfs": 4195, "parcel": "229",
        "land_registry_area": 5533,
        "zones": [{"text": "Einfamilienhauszone [E]", "area": 5406, "percent": 97.7}],
        "provisions": [{
            "title": "Bau- und Nutzungsordnung", "abbr": "", "number": "4195",
            "urls": ["https://oereblex.ag.ch/api/attachments/1289"], "index": 0,
        }],
        "laws": [{
            "title": "Bauverordnung", "abbr": "BauV", "number": "SAR 713.121",
            "urls": ["https://gesetzessammlungen.ag.ch/api/de/versions/3985/pdf_file_with_annexes"],
            "index": 930,
        }],
        "office": {"name": "Egliswil", "url": "http://www.egliswil.ch"},
        "created": "2026-08-18T10:33:46",
    }

    def store_extract(self, extract=None):
        import json
        self.edit(
            "INSERT OR REPLACE INTO oereb_cache "
            "(egrid, hard, notable, error, checked_at, details) "
            "VALUES (?,?,?,?,datetime('now'),?)",
            self.egrid, "", "", "",
            json.dumps(self.EXTRACT if extract is None else extract),
        )

    def test_a_plan_the_cadastre_lists_is_marked_for_this_parcel(self):
        """Block E reads the same extract block D shows: a decided plan the
        cadastre lists on this parcel is in force here, and says so as the
        cadastre's statement; a plan elsewhere in the municipality stays an
        area to check."""
        with sqlite3.connect(self.database) as con:
            municipality = con.execute(
                "SELECT municipality FROM parcel_results WHERE bfs=? AND parcel=?",
                (self.bfs, self.parcel)).fetchone()[0]
        self.store_extract(dict(self.EXTRACT, provisions=self.EXTRACT["provisions"] + [
            {"title": "Gestaltungsplan Testareal", "abbr": "", "number": "99.001",
             "urls": [], "index": 1, "lawstatus": "inForce"}]))
        path = os.path.join(self.tempdir.name, "publications.json")
        office = f"Gemeinde {municipality}"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"stand": "2026-10-05", "records": [
                {"pub_nr": "00.900.030", "published_on": "2025-03-01", "authority": office,
                 "title": 'Gestaltungsplan "Testareal"; Beschluss',
                 "url": "https://amtsblatt.ag.ch/ekab/00.900.030/pdf/"},
                {"pub_nr": "00.900.031", "published_on": "2026-09-01", "authority": office,
                 "title": "Öffentliche Mitwirkung zum Gestaltungsplan «Nebenfeld»",
                 "url": "https://amtsblatt.ag.ch/ekab/00.900.031/pdf/"},
            ]}, handle)
        saved = os.environ.get(planning.ENV)
        os.environ[planning.ENV] = path
        os.environ[planning.ENV_INFERENCE] = "1"
        self.addCleanup(os.environ.pop, planning.ENV_INFERENCE, None)
        self.addCleanup(lambda: os.environ.__setitem__(planning.ENV, saved)
                        if saved is not None else os.environ.pop(planning.ENV, None))
        planning.reset_cache()
        self.addCleanup(planning.reset_cache)

        app = self.open_detail()
        wait_for_news()
        app.run()
        body = regulation_card_markup(app)
        testareal = body[body.index("Gestaltungsplan «Testareal»"):][:900]
        # The same name is not the same plan: the listing is reported, the
        # status stays open.
        self.assertIn("im ÖREB-Auszug vom 18.08.2026 ist ein Plan dieses Namens in Kraft "
                      "verzeichnet (Nr. 99.001) — ob es diese Revision ist, ist nicht belegt",
                      testareal)
        self.assertIn("Stand unbestätigt", testareal)
        self.assertNotIn("In Kraft getreten", testareal)
        self.assertIn("Gebiet «Nebenfeld»", body)
        self.assertNotIn("relevant für diese Parzelle", body)

    def test_a_cited_number_does_not_put_the_plan_in_force(self):
        with sqlite3.connect(self.database) as con:
            municipality = con.execute(
                "SELECT municipality FROM parcel_results WHERE bfs=? AND parcel=?",
                (self.bfs, self.parcel)).fetchone()[0]
        self.store_extract(dict(self.EXTRACT, provisions=self.EXTRACT["provisions"] + [
            {"title": "Gestaltungsplan Testareal", "abbr": "", "number": "99.001",
             "urls": [], "index": 1, "lawstatus": "inForce"}]))
        path = os.path.join(self.tempdir.name, "publications.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"stand": "2026-10-05", "records": [
                {"pub_nr": "00.900.032", "published_on": "2025-03-01",
                 "authority": f"Gemeinde {municipality}",
                 "title": 'Gestaltungsplan "Testareal"; Beschluss',
                 "text": "Der Gemeinderat hat den Gestaltungsplan Testareal, Nr. 99.001, beschlossen.",
                 "url": "https://amtsblatt.ag.ch/ekab/00.900.032/pdf/"}]}, handle)
        saved = os.environ.get(planning.ENV)
        os.environ[planning.ENV] = path
        os.environ[planning.ENV_INFERENCE] = "1"
        self.addCleanup(os.environ.pop, planning.ENV_INFERENCE, None)
        self.addCleanup(lambda: os.environ.__setitem__(planning.ENV, saved)
                        if saved is not None else os.environ.pop(planning.ENV, None))
        planning.reset_cache()
        self.addCleanup(planning.reset_cache)

        app = self.open_detail()
        wait_for_news()
        app.run()
        body = regulation_card_markup(app)
        testareal = body[body.index("Gestaltungsplan «Testareal»"):][:900]
        self.assertIn("Stand unbestätigt", testareal)
        self.assertNotIn("In Kraft getreten", testareal)
        self.assertIn("ob es diese Revision ist, ist nicht belegt", testareal)

    def test_an_extract_without_legal_status_is_shown_as_unverified(self):
        """A cached extract from before the status was kept lists the plan but
        does not say it is in force: the card says exactly that, and how to
        settle it, instead of "In Kraft"."""
        with sqlite3.connect(self.database) as con:
            municipality = con.execute(
                "SELECT municipality FROM parcel_results WHERE bfs=? AND parcel=?",
                (self.bfs, self.parcel)).fetchone()[0]
        self.store_extract(dict(self.EXTRACT, provisions=[
            {"title": "Gestaltungsplan Testareal", "abbr": "", "number": "", "urls": [],
             "index": 0}]))
        path = os.path.join(self.tempdir.name, "publications.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"stand": "2026-10-05", "since": "2020-01-01", "records": [
                {"pub_nr": "00.900.060", "published_on": "2025-03-01",
                 "authority": f"Gemeinde {municipality}",
                 "title": 'Gestaltungsplan "Testareal"; Beschluss',
                 "url": "https://amtsblatt.ag.ch/ekab/00.900.060/pdf/"}]}, handle)
        saved = os.environ.get(planning.ENV)
        os.environ[planning.ENV] = path
        os.environ[planning.ENV_INFERENCE] = "1"
        self.addCleanup(os.environ.pop, planning.ENV_INFERENCE, None)
        self.addCleanup(lambda: os.environ.__setitem__(planning.ENV, saved)
                        if saved is not None else os.environ.pop(planning.ENV, None))
        planning.reset_cache()
        self.addCleanup(planning.reset_cache)

        app = self.open_detail()
        wait_for_news()
        app.run()
        body = regulation_card_markup(app)
        row = body[body.index("Gestaltungsplan «Testareal»"):][:700]
        self.assertIn("Rechtsstatus im gespeicherten Auszug nicht enthalten", row)
        # No refresh is promised — "ÖREB prüfen" only fetches parcels that have
        # no extract yet. The reader gets the current extract instead.
        self.assertNotIn("ÖREB-Abfrage", row)
        self.assertIn(f"https://api.geo.ag.ch/v2/oereb/extract/pdf/?EGRID={self.egrid}", row)
        self.assertNotIn("in Kraft (Auszug", row)
        self.assertIn("Stand unbestätigt", row)
        # Said once, and without "gilt": an unverified listing does not show
        # that the plan applies in force.
        self.assertNotIn("gilt laut ÖREB", row)

    def test_a_decided_plan_on_this_parcel_carries_no_relevance_count(self):
        """An extract older than the decision says the area covers the parcel,
        not that the plan is in force. The card says so in words; the
        "N relevant" summary badge is gone (Philipp, 2026-10-05)."""
        with sqlite3.connect(self.database) as con:
            municipality = con.execute(
                "SELECT municipality FROM parcel_results WHERE bfs=? AND parcel=?",
                (self.bfs, self.parcel)).fetchone()[0]
        self.store_extract(dict(self.EXTRACT, created="2024-01-01T00:00:00",
                                provisions=[{"title": "Gestaltungsplan Testareal", "abbr": "",
                                             "number": "", "urls": [], "index": 0}]))
        path = os.path.join(self.tempdir.name, "publications.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"stand": "2026-10-05", "since": "2020-01-01", "records": [
                {"pub_nr": "00.900.040", "published_on": "2025-03-01",
                 "authority": f"Gemeinde {municipality}",
                 "title": 'Gestaltungsplan "Testareal"; Beschluss',
                 "url": "https://amtsblatt.ag.ch/ekab/00.900.040/pdf/"}]}, handle)
        saved = os.environ.get(planning.ENV)
        os.environ[planning.ENV] = path
        os.environ[planning.ENV_INFERENCE] = "1"
        self.addCleanup(os.environ.pop, planning.ENV_INFERENCE, None)
        self.addCleanup(lambda: os.environ.__setitem__(planning.ENV, saved)
                        if saved is not None else os.environ.pop(planning.ENV, None))
        planning.reset_cache()
        self.addCleanup(planning.reset_cache)

        app = self.open_detail()
        wait_for_news()
        app.run()
        body = regulation_card_markup(app)
        self.assertIn(">Beschlossen</span>", body)
        # "gilt" would claim force: the listing may be a predecessor.
        self.assertIn("im ÖREB-Auszug vom 01.01.2024 ist ein Plan dieses Namens verzeichnet, "
                      "Rechtsstatus im gespeicherten Auszug nicht enthalten — der Auszug ist älter als "
                      "die letzte Publikation", body)
        self.assertNotIn("gilt laut ÖREB", body)
        self.assertNotIn("relevant für diese Parzelle", body)

    def test_the_governing_regulations_are_shown_with_their_documents(self):
        """The point of block D: the BNO that applies to this parcel, named and
        linked by the cadastre itself rather than matched on a municipality
        name."""
        self.store_extract()
        body = self.text(self.open_detail())
        self.assertIn("Bau- und Nutzungsordnung", body)
        self.assertIn("https://oereblex.ag.ch/api/attachments/1289", body)
        self.assertIn("Bauverordnung (BauV)", body)
        self.assertIn("SAR 713.121", body)
        self.assertIn("Egliswil", body)

    def test_the_official_zone_split_and_registry_area_reach_block_a(self):
        self.store_extract()
        body = self.text(self.open_detail())
        self.assertIn("Einfamilienhauszone [E]", body)
        self.assertIn("97.7%", body)
        self.assertIn("Grundbuchfläche", body)

    def test_a_registry_area_that_disagrees_is_reported(self):
        """Silent disagreement between the measured and the registered area is
        exactly the kind of error this tool exists to not make."""
        self.store_extract(dict(self.EXTRACT, land_registry_area=99999))
        self.assertIn("Abweichung zur berechneten Fläche", self.text(self.open_detail()))

    def test_an_unchecked_parcel_says_the_regulations_are_not_fetched_yet(self):
        app = self.open_detail()
        self.assertIn("Erst nach der ÖREB-Abfrage", reference_card_markup(app))
        # No user can run the query yet: the hint names no control, no schedule.
        self.assertIn("Erst nach der ÖREB-Abfrage verfügbar.</div>", reference_card_markup(app))

    def test_a_hard_restriction_is_shown_on_the_parcel(self):
        self.edit(
            "INSERT OR REPLACE INTO oereb_cache (egrid, hard, notable, error, checked_at)"
            " VALUES (?,?,?,?,datetime('now'))",
            self.egrid, "Planungszone Ortskern", "", "",
        )
        self.assertIn("Planungszone Ortskern", self.text(self.open_detail()))

    def test_a_failed_cadastre_call_is_not_shown_as_a_clean_parcel(self):
        """The dangerous failure: an error rendering as blank reads as 'nothing
        restricts this parcel', which is the opposite of what happened."""
        self.edit(
            "INSERT OR REPLACE INTO oereb_cache (egrid, hard, notable, error, checked_at)"
            " VALUES (?,?,?,?,datetime('now'))",
            self.egrid, "", "", "HTTP 502",
        )
        body = self.text(self.open_detail())
        self.assertIn("Abfrage fehlgeschlagen", body)
        self.assertNotIn("keine Eigentumsbeschränkung", body)

    def test_zero_potential_does_not_break_the_calculation(self):
        app = self.open_detail()
        potential = next(
            n for n in app.number_input
            if n.label == "Ausnutzungsreserve aBGF m²"
        )
        potential.set_value(0.0).run()
        self.assertFalse(app.exception)
        self.assertEqual(float(potential_units(app)), 0.0)
        self.assertIn("CHF", result_markup(app))
        self.assertTrue(result_attribute(app, "data-residual"))

    def test_the_password_gate_still_stands_in_front_of_a_parcel(self):
        """A session-state key must not be a way past the gate: the deployed URL
        is public, and this list is the output of Philipp's own research."""
        os.environ["APP_PASSWORD"] = "geheim"
        try:
            app = AppTest.from_file(
                os.path.join(paths.HERE, "app.py"), default_timeout=30
            )
            app.session_state[detail.SELECTED] = self.pid
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual([t.label for t in app.text_input], ["Passwort"])
            self.assertEqual(len(app.subheader), 0)
            self.assertEqual(len(app.number_input), 0)
        finally:
            del os.environ["APP_PASSWORD"]


class CalculationEventTest(unittest.TestCase):
    def test_valid_edits_and_clear_are_isolated_to_the_current_parcel(self):
        state = {detail.OVERRIDE_STORE: {"other": {"reserve": 0}}}
        event = {"type": "override", "parcel": "current", "field": "baukosten",
                 "value": "−1’234,50"}
        self.assertTrue(detail.apply_calculation_event(event, "current", state))
        self.assertEqual(state[detail.OVERRIDE_STORE],
                         {"other": {"reserve": 0}, "current": {"baukosten": 1234.5}})
        self.assertTrue(detail.apply_calculation_event(dict(event, value=""), "current", state))
        self.assertEqual(state[detail.OVERRIDE_STORE]["current"], {})
        self.assertEqual(state[detail.OVERRIDE_STORE]["other"], {"reserve": 0})

    def test_forged_or_invalid_event_does_not_mutate_state(self):
        valid = {"type": "override", "parcel": "current", "field": "baukosten",
                 "value": "100"}
        for event in (None, [], dict(valid, parcel="other"), dict(valid, type="delete")):
            state = {}
            self.assertFalse(detail.apply_calculation_event(event, "current", state))
            self.assertEqual(state, {})
        for event in (dict(valid, field="landwert"), dict(valid, value="bad"),
                      dict(valid, value=float("nan")), dict(valid, field=["baukosten"])):
            state = {}
            with self.assertRaises(ValueError):
                detail.apply_calculation_event(event, "current", state)
            self.assertEqual(state, {})

    def test_editable_renderer_escapes_labels_and_retains_decimal_amounts(self):
        step = E.Step('<img src=x onerror="alert(1)">', 'x < 2', -123.45,
                      key="baukosten")
        html = detail._calculation_table([step], editable=True)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)
        self.assertIn('data-value="123.45"', html)


class DataSheetTest(unittest.TestCase):
    def test_markup_written_for_the_screen_does_not_reach_the_paper(self):
        """The blocks are written once and rendered twice. Reportlab printed the
        markdown verbatim, so a link read as `[Gemeinde](https://…)` on paper —
        and an ampersand in a zone name aborted the build outright."""
        pdf = report.build(
            title="Test", subtitle="Test",
            blocks=[("A", [
                ("Zuständige Stelle", "[Spreitenbach](https://www.spreitenbach.ch)"),
                ("ÖREB-Kataster", "**Harte Beschränkung:** Planungszone"),
                ("Zone", "Wohn- & Gewerbezone"),
            ])],
            steps=E.residual(
                potential_gf=100.0, sale_area_pct=80.0, sale_price_chf_m2=8000.0,
                construction_chf_m2=3000.0, ancillary_pct=15.0, existing_gf=0.0,
                demolition_chf_m2=150.0, financing_pct=3.0, reserve_pct=15.0,
            ),
            notes=[],
        )
        # The ampersand alone proves the escaping: without it reportlab aborts
        # parsing the cell rather than printing the wrong character.
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(
            report._rich("[Spreitenbach](https://www.spreitenbach.ch)"),
            '<link href="https://www.spreitenbach.ch" color="#1a4fa0">Spreitenbach</link>',
        )
        self.assertEqual(report._rich("**Harte Beschränkung:** x"),
                         "<b>Harte Beschränkung:</b> x")
        self.assertEqual(report._rich("Wohn- & Gewerbezone"), "Wohn- &amp; Gewerbezone")

    def test_export_produces_a_pdf_carrying_the_calculation_path(self):
        steps = E.residual(
            potential_gf=1000.0, sale_area_pct=80.0, sale_price_chf_m2=8000.0,
            construction_chf_m2=3000.0, ancillary_pct=15.0, existing_gf=200.0,
            demolition_chf_m2=150.0, financing_pct=3.0, reserve_pct=15.0,
        )
        pdf = report.build(
            title="Musterstrasse 1, 5000 Aarau",
            subtitle="Aarau · Parzelle 1 · Wohnzone 3",
            blocks=[("A · Grunddaten", [("Zone", "Wohnzone 3")])],
            steps=steps,
            notes=["Verkaufspreis CHF/m²: 8’000 — Quelle"],
        )
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))
        # Big enough to be a page with three tables on it, small enough that a
        # runaway loop would show.
        self.assertGreater(len(pdf), 2000)
        self.assertLess(len(pdf), 200_000)


if __name__ == "__main__":
    unittest.main()


class SafeHrefTest(unittest.TestCase):
    """A link from a data file or an extract opens a web page, nothing else."""

    def test_only_a_web_address_becomes_a_link(self):
        for url in ("javascript:alert(1)", " JavaScript:alert(1)", "data:text/html,<b>x</b>",
                    "/relative", "//evil.example/x", "ftp://example.org/x", "mailto:a@example.org"):
            self.assertEqual(detail._safe_href(url), "", url)
        self.assertEqual(detail._safe_href('https://amtsblatt.ag.ch/ekab/1/pdf/?a="b"'),
                         "https://amtsblatt.ag.ch/ekab/1/pdf/?a=&quot;b&quot;")


class RegulationOnPaperTest(unittest.TestCase):
    """The in-force date has to survive onto the exported sheet, in every state
    the lookup can be in. A data sheet that does not say which edition of the
    building regulation it assumed cannot be checked a year later."""

    def edict(self, doc="https://oereblex.ag.ch/api/attachments/99"):
        return regulations.Edict(
            municipality="Möhlin", title="Bau- und Nutzungsordnung",
            abbreviation="BNO", in_force=datetime.date(2023, 12, 13),
            syst_nr="4254", document=doc)

    def test_the_date_and_the_document_are_on_the_row(self):
        heading, rows = detail._regulation_block(self.edict(), "", "")
        self.assertEqual(heading, "Stand der Rechtsvorschrift")
        self.assertEqual(rows[0][0], "BNO")
        self.assertIn("in Kraft seit 13.12.2023", rows[0][1])
        self.assertIn("oereblex.ag.ch/api/attachments/99", rows[0][1])

    def test_a_number_mismatch_is_printed_too(self):
        _, rows = detail._regulation_block(self.edict(), "4196 vs 4194", "")
        self.assertEqual(rows[1], ("Hinweis", "4196 vs 4194"))

    def test_the_block_never_silently_disappears(self):
        """Not found and could-not-ask are different answers, and neither is
        'the regulation is current'."""
        _, missing = detail._regulation_block(None, "", "")
        self.assertIn("keine gültige Vorschrift", missing[0][1])
        _, broken = detail._regulation_block(None, "", "URLError: no route")
        self.assertIn("URLError: no route", broken[0][1])
        self.assertIn("Nicht abrufbar", broken[0][0])

    def test_untracked_procedures_are_printed_with_where_to_look(self):
        """On paper as on screen: "not tracked" is not "none", and the sheet
        carries the address where the reader can check."""
        _, rows = detail._regulation_block(
            self.edict(), "", "", planning=planning.View(status="off"), municipality="Möhlin")
        label, value = rows[-1]
        self.assertEqual(label, "Verfahren (Amtsblatt)")
        self.assertIn("nicht automatisch erfasst", value)
        self.assertIn(amtsblatt.municipal_planning_link("Möhlin"), value)

    def test_tracked_procedures_reach_the_sheet(self):
        pubs, _ = planning.publications([{
            "pub_nr": "00.900.010", "published_on": "2026-09-01",
            "authority": "Gemeinde Möhlin", "rubric": "Gemeinden / Bau- und Nutzungsordnung",
            "title": "Öffentliche Mitwirkung zum Gestaltungsplan «Testareal»",
            "text": "Mitwirkung vom 2. September 2026 bis 30. Oktober 2026.",
            "url": "https://amtsblatt.ag.ch/ekab/00.900.010/pdf/"}])
        view = inferred_view(pubs, "Möhlin", [self.edict()], today=datetime.date(2026, 10, 5))
        _, rows = detail._regulation_block(self.edict(), "", "", planning=view,
                                           municipality="Möhlin")
        printed = " ".join(value for _, value in rows)
        self.assertIn("Gestaltungsplan «Testareal»", printed)
        self.assertIn("Entwurf in Mitwirkung", printed)
        self.assertIn("01.09.2026", printed)

    def test_an_approval_not_yet_in_oereblex_reaches_the_sheet(self):
        """A plan approved last month is the rule now, even before OEREBlex
        lists it — a sheet that prints only the older BNO implies it is
        current."""
        pubs, _ = planning.publications([{
            "pub_nr": "00.900.050", "published_on": "2026-09-10",
            "authority": "Regierungsrat", "rubric": "Kanton / Raumplanung",
            "title": "Genehmigung von Nutzungsplänen",
            "text": "Gemeinde Möhlin; Allgemeine Nutzungsplanung, Teilrevision Bahnhof; Genehmigung",
            "url": "https://amtsblatt.ag.ch/ekab/00.900.050/pdf/"}])
        view = inferred_view(pubs, "Möhlin", [self.edict()], today=datetime.date(2026, 10, 5))
        _, rows = detail._regulation_block(self.edict(), "", "", planning=view,
                                           municipality="Möhlin")
        pending = dict(rows).get("Verfahren (Amtsblatt)", "")
        self.assertIn("Genehmigt, Inkrafttreten nicht belegt: Allgemeine Nutzungsplanung, "
                      "Teilrevision Bahnhof", pending)
        self.assertIn("10.09.2026", pending)

    def test_a_regulation_approved_for_later_reaches_the_sheet(self):
        """Live with no store at all: OEREBlex lists the next BNO with a date
        ahead. The card shows it; the sheet has to as well."""
        later = regulations.Edict(
            municipality="Möhlin", title="Bau- und Nutzungsordnung", abbreviation="BNO",
            in_force=datetime.date(2099, 1, 1), syst_nr="4254",
            document="https://oereblex.ag.ch/api/attachments/100")
        _, rows = detail._regulation_block(self.edict(), "", "", planning=planning.View(status="off"),
                                           municipality="Möhlin", edicts=[later, self.edict()])
        printed = " ".join(f"{label}: {value}" for label, value in rows)
        self.assertIn("in Kraft ab 01.01.2099", printed)
        self.assertIn("oereblex.ag.ch/api/attachments/100", printed)

    def test_the_approval_merged_into_the_regulation_is_printed_with_it(self):
        pubs, _ = planning.publications([{
            "pub_nr": "00.900.091", "published_on": "2023-12-20",
            "authority": "Regierungsrat", "rubric": "Kanton / Raumplanung",
            "title": "Genehmigung von Nutzungsplänen",
            "text": "Gemeinde Möhlin; Allgemeine Nutzungsplanung, Gesamtrevision; Genehmigung",
            "url": "https://amtsblatt.ag.ch/ekab/00.900.091/pdf/"}])
        view = inferred_view(pubs, "Möhlin", [self.edict()], today=datetime.date(2026, 10, 5))
        self.assertIn(self.edict(), view.approvals)
        _, rows = detail._regulation_block(self.edict(), "", "", planning=view,
                                           municipality="Möhlin", edicts=[self.edict()])
        self.assertIn("Genehmigung publiziert 20.12.2023", rows[0][1])

    def test_an_approval_merged_into_a_later_edition_is_printed_with_it(self):
        later = regulations.Edict(
            municipality="Möhlin", title="Bau- und Nutzungsordnung", abbreviation="BNO",
            in_force=datetime.date(2099, 1, 1), syst_nr="4254", document="")
        view = planning.View(status="ok", since=datetime.date(2026, 1, 1), inference=True)
        pubs, _ = planning.publications([{
            "pub_nr": "00.900.093", "published_on": "2098-12-01", "authority": "Regierungsrat",
            "rubric": "Kanton / Raumplanung", "title": "Genehmigung von Nutzungsplänen",
            "text": "Gemeinde Möhlin; Allgemeine Nutzungsplanung, Gesamtrevision; Genehmigung",
            "url": "https://amtsblatt.ag.ch/ekab/00.900.093/pdf/"}])
        view.approvals = inferred_view(pubs, "Möhlin", [later],
                                       today=datetime.date(2098, 12, 5)).approvals
        _, rows = detail._regulation_block(self.edict(), "", "", planning=view,
                                           municipality="Möhlin", edicts=[later, self.edict()])
        later_row = next(value for _, value in rows if "in Kraft ab 01.01.2099" in value)
        self.assertIn("Genehmigung publiziert 01.12.2098", later_row)

    def test_an_edition_with_its_date_ahead_claims_no_more_than_the_date(self):
        """OEREBlex gives the in-force date — not that the approval is final."""
        later = regulations.Edict(
            municipality="Möhlin", title="Bau- und Nutzungsordnung", abbreviation="BNO",
            in_force=datetime.date(2099, 1, 1), syst_nr="4254", document="")
        card = detail._regulation_card_html({"municipality": "Möhlin"}, [later, self.edict()],
                                            self.edict(), "", planning=planning.View(status="off"))
        row = card[card.index("in Kraft ab 01.01.2099") - 600:][:900]
        self.assertIn(">Künftig in Kraft</span>", row)
        self.assertNotIn("Rechtskraft", card)

    def test_the_summary_mentions_an_edition_still_to_come(self):
        later = regulations.Edict(
            municipality="Möhlin", title="Bau- und Nutzungsordnung", abbreviation="BNO",
            in_force=datetime.date(2099, 1, 1), syst_nr="4254", document="")
        card = detail._regulation_card_html({"municipality": "Möhlin"}, [later, self.edict()],
                                            self.edict(), "", planning=planning.View(status="off"))
        self.assertIn("1 künftig in Kraft", card.partition("</summary>")[0])

    def test_canton_wide_changes_reach_the_sheet(self):
        pubs, _ = planning.publications([{
            "pub_nr": "00.900.092", "published_on": "2026-09-15",
            "authority": "Departement Bau, Verkehr und Umwelt",
            "rubric": "Kanton / Anhörungs- und Mitwirkungsverfahren",
            "title": "Teilrevision Baugesetz (BauG); Anhörung",
            "text": "Anhörung vom 15. September 2026 bis 15. Dezember 2026.",
            "url": "https://amtsblatt.ag.ch/ekab/00.900.092/pdf/"}])
        view = inferred_view(pubs, "Möhlin", [self.edict()], today=datetime.date(2026, 10, 5))
        _, rows = detail._regulation_block(self.edict(), "", "", planning=view,
                                           municipality="Möhlin")
        self.assertIn("Entwurf in Anhörung: Teilrevision Baugesetz (BauG)",
                      dict(rows).get("Kanton (Amtsblatt)", ""))

    def test_an_unattributed_publication_is_printed_next_to_the_list(self):
        """A list printed without the publication that could not be placed
        reads as complete — on paper as in the card's summary."""
        pubs, _ = planning.publications([{
            "pub_nr": "00.900.094", "published_on": "2026-09-01",
            "authority": "Gemeinde Möhlin", "rubric": "Gemeinden / Bau- und Nutzungsordnung",
            "title": "Öffentliche Mitwirkung zum Gestaltungsplan «Testareal»",
            "text": "Mitwirkung vom 2. September 2026 bis 30. Oktober 2026.",
            "url": "https://amtsblatt.ag.ch/ekab/00.900.094/pdf/"}])
        view = inferred_view(pubs, "Möhlin", [self.edict()], today=datetime.date(2026, 10, 5))
        view.unattributed = 1
        _, rows = detail._regulation_block(self.edict(), "", "", planning=view,
                                           municipality="Möhlin")
        printed = " ".join(f"{label}: {value}" for label, value in rows)
        self.assertIn("Gestaltungsplan «Testareal»", printed)
        self.assertIn("1 nicht zuordenbare Publikation(en)", printed)

    def test_an_unrecognised_step_is_not_named_like_the_badge(self):
        """The badge says "Stand unbestätigt"; the line under the title says
        what is missing — the step — not a second word for the same thing."""
        pubs, _ = planning.publications([{
            "pub_nr": "00.900.096", "published_on": "2026-09-01",
            "authority": "Gemeinde Möhlin", "rubric": "Gemeinden / Bau- und Nutzungsordnung",
            "title": "Gestaltungsplan «Rain»", "text": "",
            "url": "https://amtsblatt.ag.ch/ekab/00.900.096/pdf/"}])
        revision = inferred_view(pubs, "Möhlin", today=datetime.date(2026, 10, 5)).all()[0]
        _, html = detail._revision_row(revision, datetime.date(2026, 10, 5))
        self.assertIn("Stand unbestätigt", html)
        self.assertIn("Schritt nicht erkannt, publiziert 01.09.2026", html)
        self.assertNotIn("Stand unklar", html)

    def row_for(self, title, text, published_on="2026-03-02"):
        pubs, _ = planning.publications([{
            "pub_nr": "00.900.097", "published_on": published_on,
            "authority": "Gemeinde Möhlin", "rubric": "Gemeinden / Bau- und Nutzungsordnung",
            "title": title, "text": text, "url": "https://amtsblatt.ag.ch/ekab/00.900.097/pdf/"}])
        revision = inferred_view(pubs, "Möhlin", today=datetime.date(2026, 10, 5)).all()[0]
        return detail._revision_row(revision, datetime.date(2026, 10, 5))[1]

    def test_a_draft_whose_period_ended_says_nothing_later_was_found(self):
        html = self.row_for('Gestaltungsplan "Rain"; öffentliche Auflage',
                            "Die Akten liegen vom 2. März 2026 bis 31. März 2026 auf.")
        self.assertIn("Stand unbestätigt", html)
        self.assertIn("(abgelaufen)", html)
        self.assertIn("kein späterer Schritt im Verzeichnis", html)

    def test_a_planungszone_in_force_after_its_display_is_not_waiting_for_a_step(self):
        """The display ended, the zone holds: no hint that a step is missing."""
        html = self.row_for("Erlass einer Planungszone «Oberdorf»",
                            "Die Planungszone liegt vom 2. März 2026 bis 31. März 2026 öffentlich auf.")
        self.assertIn("Planungszone in Kraft", html)
        self.assertIn("(abgelaufen)", html)
        self.assertNotIn("kein späterer Schritt", html)

    def zone_row(self, records, edicts=(), parcel=None):
        today = datetime.date(2026, 10, 5)
        records = [dict({"authority": "Gemeinde Möhlin", "text": "",
                         "rubric": "Gemeinden / Bau- und Nutzungsordnung",
                         "url": "https://amtsblatt.ag.ch/ekab/00.900.098/pdf/"}, **r) for r in records]
        pubs, _ = planning.publications(records)
        view = inferred_view(pubs, "Möhlin", edicts, today=today, parcel=parcel)
        zone = next(r for r in view.all() if r.step == "planungszone")
        return detail._revision_row(zone, today)[1]

    ZONE = {"pub_nr": "00.900.095", "published_on": "2026-03-01",
            "title": "Erlass einer Planungszone «Oberdorf»"}
    SECURES_PLAN = "Die Planungszone dient der Sicherung des Gestaltungsplans «Oberdorf»."
    SECURES_NUTZUNGSPLANUNG = ("Die Planungszone dient der Sicherung der Gesamtrevision der "
                               "Nutzungsplanung.")

    def oberdorf(self, tail=" Der Plan tritt am 1. August 2026 in Kraft."):
        return {"pub_nr": "00.900.099", "published_on": "2026-07-01", "authority": "Regierungsrat",
                "rubric": "Kanton / Raumplanung", "title": "Genehmigung von Sondernutzungsplänen",
                "text": 'Gemeinde Möhlin: Gestaltungsplan "Oberdorf"; Genehmigung.' + tail,
                "url": "https://amtsblatt.ag.ch/ekab/00.900.099/pdf/"}

    def bno(self, in_force):
        return regulations.Edict(municipality="Möhlin", title="Bau- und Nutzungsordnung",
                                 abbreviation="BNO", in_force=in_force, syst_nr="4254", document="")

    def test_a_plan_in_force_by_its_notice_does_not_borrow_the_listing(self):
        """In force by the date its approval gives; the extract lists a plan
        of that name, not shown to be this one — the note says so."""
        pubs, _ = planning.publications([self.oberdorf()])
        parcel = {"created": "2026-08-18", "provisions": [
            {"title": "Gestaltungsplan Oberdorf", "number": "26.100", "lawstatus": "inForce"}]}
        today = datetime.date(2026, 10, 5)
        plan = inferred_view(pubs, "Möhlin", [], today=today, parcel=parcel).in_force[0]
        html = detail._revision_row(plan, today)[1]
        self.assertIn("In Kraft getreten", html)
        self.assertIn("ob es diese Revision ist, ist nicht belegt", html)
        self.assertNotIn("laut ÖREB-Auszug", html)

    def test_a_zone_with_a_shorter_stated_term_says_that_one(self):
        html = self.zone_row([dict(self.ZONE, text="Die Planungszone gilt längstens bis "
                                                   "31. Dezember 2027.")])
        self.assertIn("längstens bis 31.12.2027 (laut Publikation)", html)
        self.assertNotIn("2031", html)

    def test_a_zone_past_its_term_without_a_display_date_names_no_invented_day(self):
        html = self.zone_row([dict(self.ZONE, published_on="2016-05-01")])
        self.assertIn("Höchstdauer (5 Jahre ab öffentlicher Auflage) abgelaufen — Auflage laut "
                      "Publikation vom 01.05.2016, Beginn nicht angegeben", html)
        self.assertNotIn("01.05.2021", html)

    def test_a_planned_date_is_never_printed_as_in_force(self):
        pubs, _ = planning.publications([{
            "pub_nr": "00.900.103", "published_on": "2025-10-01", "authority": "Gemeinde Möhlin",
            "rubric": "Gemeinden / Bau- und Nutzungsordnung", "title": 'Gestaltungsplan "Rain"; Beschluss',
            "text": "Der Plan tritt am 1. Januar 2026 in Kraft.",
            "url": "https://amtsblatt.ag.ch/ekab/00.900.103/pdf/"}])
        view = inferred_view(pubs, "Möhlin", [self.edict()], today=datetime.date(2026, 10, 5))
        _, html = detail._revision_row(view.all()[0], datetime.date(2026, 10, 5))
        self.assertIn("Inkrafttreten vorgesehen ab 01.01.2026", html)
        _, rows = detail._regulation_block(self.edict(), "", "", planning=view, municipality="Möhlin")
        printed = dict(rows)["Verfahren (Amtsblatt)"]
        self.assertIn("Inkrafttreten vorgesehen ab 01.01.2026", printed)
        self.assertNotIn("in Kraft seit", printed)

    def test_the_sheet_heads_plans_in_force_by_their_source(self):
        pubs, _ = planning.publications([dict(self.oberdorf())])
        view = inferred_view(pubs, "Möhlin", [self.edict()], today=datetime.date(2026, 10, 5))
        _, rows = detail._regulation_block(self.edict(), "", "", planning=view, municipality="Möhlin")
        self.assertIn("In Kraft getreten (Amtsblatt)", dict(rows))

    def test_a_draft_beside_a_listing_claims_no_state_of_its_own(self):
        pubs, _ = planning.publications([{
            "pub_nr": "00.900.104", "published_on": "2026-03-01", "authority": "Gemeinde Möhlin",
            "rubric": "Gemeinden / Bau- und Nutzungsordnung",
            "title": 'Gestaltungsplan "Oberdorf"; öffentliche Auflage',
            "text": "Die Akten liegen vom 2. März 2026 bis 31. März 2026 auf.",
            "url": "https://amtsblatt.ag.ch/ekab/00.900.104/pdf/"}])
        parcel = {"created": "2026-08-18", "provisions": [
            {"title": "Gestaltungsplan Oberdorf", "number": "05.120", "lawstatus": "inForce"}]}
        today = datetime.date(2026, 10, 5)
        plan = inferred_view(pubs, "Möhlin", [], today=today, parcel=parcel).all()[0]
        html = detail._revision_row(plan, today)[1]
        self.assertIn("Stand unbestätigt", html)
        self.assertIn("diese Revision ist im Amtsblatt nur als Entwurf publiziert", html)
        self.assertNotIn("noch im Verfahren", html)

    def test_a_zone_beside_an_unnamed_plan_of_its_kind_says_the_link_is_unproven(self):
        html = self.zone_row([dict(self.ZONE, text="Die Planungszone dient der Sicherung eines "
                                                   "Gestaltungsplans."), self.oberdorf()])
        self.assertIn("Stand unbestätigt", html)
        self.assertIn("die Planungszone sichert laut Publikation einen Plan dieser Art, ohne Namen — "
                      "ob es dieser ist, ist nicht belegt", html)

    def test_a_zone_in_force_says_how_long_at_most(self):
        html = self.zone_row([self.ZONE], [self.bno(datetime.date(2026, 9, 1))])
        self.assertIn("Planungszone in Kraft", html)
        self.assertIn("längstens bis etwa 01.03.2031 (§ 29 Abs. 2 BauG; Beginn der Auflage "
                      "nicht angegeben)", html)

    def test_a_zone_ended_by_the_plan_it_secures_says_so(self):
        html = self.zone_row([dict(self.ZONE, text=self.SECURES_PLAN), self.oberdorf()])
        self.assertIn("Planungszone beendet", html)
        self.assertIn("seit 01.08.2026 in Kraft: Gestaltungsplan «Oberdorf»; laut Publikation "
                      "sichert die Planungszone diesen Plan (§ 29 Abs. 2 BauG)", html)

    def test_a_zone_beside_a_plan_of_its_name_says_the_link_is_unproven(self):
        html = self.zone_row([self.ZONE, self.oberdorf()])
        self.assertIn("Stand unbestätigt", html)
        self.assertIn("seit 01.08.2026 in Kraft: Gestaltungsplan «Oberdorf»; gleichnamiger Plan — "
                      "ob die Planungszone ihn sichert, ist nicht belegt", html)

    def test_a_planungszone_says_why_it_may_have_ended(self):
        html = self.zone_row([dict(self.ZONE, text=self.SECURES_NUTZUNGSPLANUNG)],
                             [self.bno(datetime.date(2026, 9, 1))])
        self.assertIn("Stand unbestätigt", html)
        self.assertIn("seit 01.09.2026 gilt eine neue Ausgabe: Bau- und Nutzungsordnung; die "
                      "Planungszone sichert laut Publikation die Nutzungsplanung — ob diese "
                      "Ausgabe sie umsetzt, ist nicht belegt", html)

    def test_a_zone_names_the_approval_that_may_have_ended_it(self):
        html = self.zone_row([dict(self.ZONE, text=self.SECURES_PLAN), self.oberdorf(tail="")])
        self.assertIn("Genehmigung publiziert am 01.07.2026, Inkrafttreten nicht belegt: "
                      "Gestaltungsplan «Oberdorf»", html)

    def test_a_zone_names_the_extract_that_may_show_its_plan_in_force(self):
        decision = {"pub_nr": "00.900.100", "published_on": "2026-05-01",
                    "title": 'Gestaltungsplan "Oberdorf"; Beschluss'}
        parcel = {"created": "2026-08-18", "provisions": [
            {"title": "Gestaltungsplan Oberdorf", "number": "26.100", "lawstatus": "inForce"}]}
        html = self.zone_row([dict(self.ZONE, text=self.SECURES_PLAN), decision], parcel=parcel)
        self.assertIn("im ÖREB-Auszug vom 18.08.2026 ist ein gleichnamiger Plan verzeichnet, "
                      "Zuordnung nicht belegt: Gestaltungsplan «Oberdorf»", html)
        self.assertIn("Stand unbestätigt", html)

    def test_a_zone_without_the_list_of_regulations_says_so(self):
        html = self.zone_row([dict(self.ZONE, text=self.SECURES_NUTZUNGSPLANUNG)], None)
        self.assertIn("geltende Vorschriften (OEREBlex) nicht verfügbar", html)

    def test_a_zone_past_the_term_its_notice_states_says_so(self):
        html = self.zone_row([dict(self.ZONE, published_on="2023-01-10",
                                   text="Die Planungszone gilt längstens bis 31. Dezember 2024.")])
        self.assertIn("Planungszone beendet", html)
        self.assertIn("laut Publikation befristet bis 31.12.2024", html)

    def test_a_zone_past_its_five_years_says_so(self):
        html = self.zone_row([dict(self.ZONE, published_on="2021-09-20",
                                   text="Die Planungszone liegt vom 21. September 2021 bis "
                                        "20. Oktober 2021 öffentlich auf.")])
        self.assertIn("Höchstdauer (5 Jahre ab öffentlicher Auflage) am 21.09.2026 abgelaufen "
                      "(§ 29 Abs. 2 BauG)", html)
        html = self.zone_row([dict(self.ZONE, published_on="2021-09-20")])
        self.assertIn("Höchstdauer (5 Jahre ab öffentlicher Auflage) endet um den 20.09.2026; "
                      "Beginn der Auflage nicht angegeben", html)

    def test_a_demoted_zone_after_its_display_is_not_waiting_for_a_step(self):
        html = self.zone_row([dict(self.ZONE, text=self.SECURES_NUTZUNGSPLANUNG + " Die Planungszone "
                                   "liegt vom 2. März 2026 bis 31. März 2026 öffentlich auf.")],
                             [self.bno(datetime.date(2026, 9, 1))])
        self.assertIn("Stand unbestätigt", html)
        self.assertIn("(abgelaufen)", html)
        self.assertNotIn("kein späterer Schritt", html)

    def test_an_ended_zone_reaches_the_card_and_the_sheet(self):
        pubs, _ = planning.publications([dict({
            "authority": "Gemeinde Möhlin", "text": "", "rubric": "Gemeinden / Bau- und Nutzungsordnung",
            "url": "https://amtsblatt.ag.ch/ekab/00.900.095/pdf/"}, **dict(self.ZONE, published_on="2016-03-01"))])
        view = inferred_view(pubs, "Möhlin", [self.edict()], today=datetime.date(2026, 10, 5))
        card = detail._regulation_card_html({"municipality": "Möhlin"}, [self.edict()],
                                            self.edict(), "", planning=view)
        self.assertIn("Planungszone beendet", card)
        self.assertIn("Keine laufenden Verfahren", card)
        _, rows = detail._regulation_block(self.edict(), "", "", planning=view, municipality="Möhlin")
        self.assertIn("Planungszone beendet: Planungszone «Oberdorf»",
                      dict(rows).get("Beendet (Amtsblatt)", ""))

    def test_a_row_says_which_date_is_which(self):
        pubs, _ = planning.publications([
            {"pub_nr": "00.900.101", "published_on": "2024-06-27", "authority": "Gemeinde Möhlin",
             "rubric": "Gemeinden / Bau- und Nutzungsordnung", "text": "",
             "title": 'Gestaltungsplan "Breiten"; öffentliche Auflage',
             "url": "https://amtsblatt.ag.ch/ekab/00.900.101/pdf/"},
            {"pub_nr": "00.900.102", "published_on": "2024-09-05", "authority": "Gemeinde Möhlin",
             "rubric": "Gemeinden / Bau- und Nutzungsordnung",
             "text": "Der Gemeinderat hat am 2. September 2024 den Plan beschlossen.",
             "title": 'Gestaltungsplan "Breiten"; Beschluss',
             "url": "https://amtsblatt.ag.ch/ekab/00.900.102/pdf/"}])
        view = inferred_view(pubs, "Möhlin", [self.edict()], today=datetime.date(2026, 10, 5))
        card = detail._regulation_card_html({"municipality": "Möhlin"}, [self.edict()],
                                            self.edict(), "", planning=view)
        breiten = card[card.index("Gestaltungsplan «Breiten»") - 400:][:1200]
        self.assertIn('detail-regulation-date-kind">publiziert<', breiten)
        self.assertIn("Öffentliche Auflage, publiziert 27.06.2024 → Beschluss vom 02.09.2024, "
                      "publiziert 05.09.2024", breiten)
        bno = card[card.index("Bau- und Nutzungsordnung") - 400:][:900]
        self.assertIn('detail-regulation-date-kind">in Kraft<', bno)
        _, rows = detail._regulation_block(self.edict(), "", "", planning=view, municipality="Möhlin")
        self.assertIn("Beschlossen: Gestaltungsplan «Breiten» (Gebiet «Breiten», Entscheid vom "
                      "02.09.2024, publiziert 05.09.2024)", dict(rows)["Verfahren (Amtsblatt)"])

    def test_a_zone_in_force_is_not_followed_by_none_under_way(self):
        """A zone secures a plan being prepared: "keine laufenden Verfahren"
        beneath it would contradict it."""
        pubs, _ = planning.publications([dict({
            "authority": "Gemeinde Möhlin", "text": "", "rubric": "Gemeinden / Bau- und Nutzungsordnung",
            "url": "https://amtsblatt.ag.ch/ekab/00.900.095/pdf/"}, **self.ZONE)])
        view = inferred_view(pubs, "Möhlin", [self.edict()], today=datetime.date(2026, 10, 5))
        card = detail._regulation_card_html({"municipality": "Möhlin"}, [self.edict()],
                                            self.edict(), "", planning=view)
        self.assertIn("Planungszone in Kraft", card)
        self.assertNotIn("Keine laufenden Verfahren", card)

    def test_by_default_the_sheet_lists_publications_not_states(self):
        pubs, _ = planning.publications([{
            "pub_nr": "00.900.205", "published_on": "2026-09-01",
            "authority": "Gemeinde Möhlin", "rubric": "Gemeinden / Bau- und Nutzungsordnung",
            "title": "Öffentliche Mitwirkung zum Gestaltungsplan «Testareal»",
            "text": "Mitwirkung vom 2. September 2026 bis 30. Oktober 2026.",
            "url": "https://amtsblatt.ag.ch/ekab/00.900.205/pdf/"}])
        view = planning.view(pubs, "Möhlin", [self.edict()], today=datetime.date(2026, 10, 5))
        _, rows = detail._regulation_block(self.edict(), "", "", planning=view, municipality="Möhlin")
        printed = dict(rows)
        self.assertEqual(printed["Publikationen (Amtsblatt)"],
                         "01.09.2026 Publikation: Öffentliche Mitwirkung zum Gestaltungsplan «Testareal» — "
                         "Publ.-Nr. 00.900.205 · [amtsblatt.ag.ch/ekab/00.900.205/pdf/](https://amtsblatt.ag.ch/ekab/00.900.205/pdf/)")
        self.assertIn("nicht der heutige Stand", printed["Hinweis zu den Publikationen"])
        self.assertNotIn("Entwurf in Mitwirkung", " ".join(printed.values()))

    def test_by_default_a_canton_notices_part_is_shown_in_its_own_words(self):
        """A canton notice titled "Genehmigung von Sondernutzungsplänen" refuses
        the plan its part for this municipality names: no step claimed, on
        screen or on paper, and the refusal stays in the row's title."""
        notice = {"authority": "Abteilung Raumentwicklung", "rubric": "Kanton / Raumplanung",
                  "title": "Genehmigung von Sondernutzungsplänen"}
        pubs, _ = planning.publications([
            dict(notice, pub_nr="00.900.231", published_on="2026-03-05",
                 text="Gemeinde Möhlin: Verweigerung der Genehmigung der Teiländerung Dorf",
                 url="https://amtsblatt.ag.ch/ekab/00.900.231/pdf/"),
            dict(notice, pub_nr="00.900.232", published_on="2026-03-04",
                 text='Gemeinde Möhlin: Gestaltungsplan "Giessi"; Genehmigung verweigert',
                 url="https://amtsblatt.ag.ch/ekab/00.900.232/pdf/"),
            dict(notice, pub_nr="00.900.233", published_on="2026-03-03",
                 text='Gemeinde Möhlin: Gestaltungsplan "Rain"',
                 url="https://amtsblatt.ag.ch/ekab/00.900.233/pdf/")])
        view = planning.view(pubs, "Möhlin", [self.edict()], today=datetime.date(2026, 10, 5))
        card = detail._regulation_card_html({"municipality": "Möhlin"}, [self.edict()],
                                            self.edict(), "", planning=view)
        chips = re.findall(r'detail-regulation-status--step"[^>]*>([^<]+)<', card)
        self.assertEqual(chips, ["Publikation", "Publikation", "Publikation"])
        self.assertIn(escape('Gestaltungsplan "Giessi"; Genehmigung verweigert'), card)
        self.assertIn("Einen Schritt zeigt eine Zeile nur, wenn er an einer Quelle geprüft", card)
        self.assertIn("Titel und Datum wie publiziert; ein Verfahrensschritt nur, wenn geprüft und belegt.", card)
        _, rows = detail._regulation_block(self.edict(), "", "", planning=view, municipality="Möhlin")
        self.assertEqual(printed_list(rows, "Publikationen (Amtsblatt)"),
                         ["05.03.2026 Publikation: Verweigerung der Genehmigung der Teiländerung Dorf — "
                          "Publ.-Nr. 00.900.231 · [amtsblatt.ag.ch/ekab/00.900.231/pdf/](https://amtsblatt.ag.ch/ekab/00.900.231/pdf/)",
                          '04.03.2026 Publikation: Gestaltungsplan "Giessi"; Genehmigung verweigert — '
                          "Publ.-Nr. 00.900.232 · [amtsblatt.ag.ch/ekab/00.900.232/pdf/](https://amtsblatt.ag.ch/ekab/00.900.232/pdf/)",
                          '03.03.2026 Publikation: Gestaltungsplan "Rain" — '
                          "Publ.-Nr. 00.900.233 · [amtsblatt.ag.ch/ekab/00.900.233/pdf/](https://amtsblatt.ag.ch/ekab/00.900.233/pdf/)"])
        self.assertIn("ein Schritt nur, wenn an einer Quelle geprüft", dict(rows)["Hinweis zu den Publikationen"])

    def checked_store(self):
        """Möhlin's publications as the importer writes them, some checked."""
        seen = "https://amtsblatt.ag.ch/ekab/{}/pdf/"

        def rec(nr, day, title, verified=None, **extra):
            out = {"pub_nr": nr, "published_on": day, "authority": "Gemeinde Möhlin",
                   "rubric": "Gemeinden / Bau- und Nutzungsordnung", "title": title,
                   "url": seen.format(nr), "municipality": "Möhlin"}
            if verified:
                out["verified"] = dict({"source": seen.format(nr), "verified_on": "2026-10-06",
                                        "method": "Publikation gelesen"}, **verified)
            return dict(out, **extra)

        canton = dict(authority="Departement Bau, Verkehr und Umwelt", rubric="Kanton / Raumplanung")
        pubs, problems = planning.publications([
            rec("00.900.901", "2026-09-25", "Teiländerung Dorf; öffentliche Auflage",
                {"stage": "auflage", "auflage_from": "2026-09-28", "auflage_to": "2026-10-27"}),
            rec("00.900.902", "2026-09-30", "Genehmigung von Nutzungsplänen", part="Teiländerung Bahnhof",
                verified={"stage": "genehmigt", "approved_on": "2026-09-24", "appeal_until": "2026-10-30"},
                **canton),
            rec("00.900.902", "2026-09-30", "Genehmigung von Nutzungsplänen", municipality="Reinach",
                part="Gestaltungsplan Hof", verified={"stage": "in_kraft", "effective_on": "2026-10-01"}, **canton),
            rec("00.900.903", "2026-06-01", "Gestaltungsplan Weid; öffentliche Auflage",
                {"stage": "auflage", "auflage_from": "2026-06-02", "auflage_to": "2026-07-01"}),
            rec("00.900.904", "2026-08-20", "Genehmigung von Sondernutzungsplänen", part="Gestaltungsplan Brühl",
                verified={"stage": "verweigert", "decided_on": "2026-08-14"}, **canton),
            rec("00.900.905", "2026-07-01", "Inkraftsetzung Teiländerung Zentrum",
                {"stage": "in_kraft", "effective_on": "2026-07-01"}),
            rec("00.900.906", "2026-05-01", "Gestaltungsplan «Rain»; Beschluss")])
        self.assertEqual(problems, [])
        return planning.View(publications=planning.listing(pubs, "Möhlin").publications,
                             stand=datetime.date(2026, 10, 5), since=datetime.date(2026, 1, 1))

    def test_a_checked_stage_shows_philipps_badge_only_while_its_source_supports_it(self):
        view = self.checked_store()
        card = detail._regulation_card_html({"municipality": "Möhlin"}, [self.edict()], self.edict(), "",
                                            planning=view, today=datetime.date(2026, 10, 6))
        listed = card[card.index("· Publikationen im Amtsblatt</div>"):]
        badges = re.findall(r'class="detail-regulation-status[^"]*"[^>]*>([^<]+)<', listed)
        self.assertEqual(badges, ["Genehmigt, Rechtskraft ausstehend", "Entwurf in Auflage", "Publikation",
                                  "In Kraft getreten", "Publikation", "Publikation"])
        self.assertIn("detail-regulation-status--amber", listed)
        for words in ("Genehmigt am 24.09.2026 · Beschwerdefrist bis 30.10.2026 · geprüft am 06.10.2026",
                      "Öffentliche Auflage 28.09.2026–27.10.2026 · geprüft am 06.10.2026: Publikation gelesen",
                      "Genehmigung verweigert am 14.08.2026",
                      "Öffentliche Auflage 02.06.2026–01.07.2026 beendet — heutiger Stand nicht geprüft",
                      "Publ.-Nr. 00.900.901"):
            self.assertIn(escape(words, quote=False), listed)
        self.assertIn('<a href="https://amtsblatt.ag.ch/ekab/00.900.902/pdf/" target="_blank" '
                      'rel="noopener noreferrer">Nachweis ↗</a>', listed)
        self.assertNotIn("((", listed)
        self.assertNotIn("Gestaltungsplan Hof", card)  # Reinach's row of the same notice
        _, rows = detail._regulation_block(self.edict(), "", "", planning=view, municipality="Möhlin",
                                           today=datetime.date(2026, 10, 6))
        printed = printed_list(rows, "Publikationen (Amtsblatt)")
        self.assertTrue(printed[0].startswith("30.09.2026 Genehmigt, Rechtskraft ausstehend: Teiländerung Bahnhof "
                                              "— Publ.-Nr. 00.900.902 · "), printed[0])
        self.assertIn("Genehmigt am 24.09.2026 · Beschwerdefrist bis 30.10.2026", printed[0])
        self.assertTrue(printed[2].startswith("20.08.2026 Publikation: Gestaltungsplan Brühl"), printed[2])
        later = detail._regulation_card_html({"municipality": "Möhlin"}, [self.edict()], self.edict(), "",
                                             planning=view, today=datetime.date(2026, 11, 5))
        listed = later[later.index("· Publikationen im Amtsblatt</div>"):]
        self.assertEqual(re.findall(r'class="detail-regulation-status[^"]*"[^>]*>([^<]+)<', listed)[:2],
                         ["Publikation", "Publikation"])
        self.assertIn("Rechtskraft nach dem 30.10.2026 nicht geprüft", listed)

    def test_a_stale_list_or_a_failed_update_is_said_on_the_card_and_on_paper(self):
        view = self.checked_store()
        view.stand, view.updated_at = datetime.date(2026, 9, 20), datetime.datetime(2026, 9, 20, 7, 5)
        view.failed_at = datetime.datetime(2026, 10, 6, 7, 0)
        view.failure = "Import abgelehnt — nichts ersetzt:\nLücke: Verzeichnis bis 20.09.2026, Import ab 01.10.2026"
        card = detail._regulation_card_html({"municipality": "Möhlin"}, [self.edict()], self.edict(), "",
                                            planning=view, today=datetime.date(2026, 10, 6))
        summary = card.partition("</summary>")[0]
        self.assertIn("Verzeichnis veraltet", summary)
        self.assertIn("Aktualisierung fehlgeschlagen", summary)
        for words in ("Verzeichnis veraltet: Stand 20.09.2026, 16 Tage alt",
                      "Letzte Aktualisierung am 06.10.2026 07:00 fehlgeschlagen",
                      "Lücke: Verzeichnis bis 20.09.2026",
                      "erfasst seit 01.01.2026, Stand 20.09.2026, aktualisiert 20.09.2026 07:05"):
            self.assertIn(escape(words, quote=False), card)
        _, rows = detail._regulation_block(self.edict(), "", "", planning=view, municipality="Möhlin",
                                           today=datetime.date(2026, 10, 6))
        status = dict(rows)["Verzeichnis (Amtsblatt)"]
        self.assertIn("veraltet", status)
        self.assertIn("fehlgeschlagen", status)
        view.stand, view.failed_at, view.failure = datetime.date(2026, 10, 5), None, ""
        card = detail._regulation_card_html({"municipality": "Möhlin"}, [self.edict()], self.edict(), "",
                                            planning=view, today=datetime.date(2026, 10, 6))
        self.assertNotIn("veraltet", card)
        self.assertNotIn("fehlgeschlagen", card)

    def test_a_long_title_and_its_link_stay_on_the_page(self):
        long_title = "Gestaltungsplan «Areal» " + "mit Sondervorschriften zur Etappierung, " * 40 + "ENDE-"
        pubs, _ = planning.publications([{
            "pub_nr": f"00.900.{950 + i}", "published_on": "2026-09-01", "authority": "Gemeinde Möhlin",
            "rubric": "Gemeinden / Bau- und Nutzungsordnung", "title": long_title + str(i),
            "url": f"https://amtsblatt.ag.ch/ekab/00.900.{950 + i}/pdf/", "municipality": "Möhlin"}
            for i in range(30)])
        view = planning.listing(pubs, "Möhlin")
        view.stand, view.since = datetime.date(2026, 10, 5), datetime.date(2026, 1, 1)
        _, rows = detail._regulation_block(self.edict(), "", "", planning=view, municipality="Möhlin",
                                           today=datetime.date(2026, 10, 6))
        printed = printed_list(rows, "Publikationen (Amtsblatt)")
        self.assertTrue(all(f"{long_title}{i} — Publ.-Nr. 00.900.9" in line for i, line in
                            enumerate(sorted(printed, key=lambda l: int(l.split("ENDE-")[1].split()[0])))))
        pdf = report.build(title="T", subtitle="S", blocks=[("E", rows)], steps=E.residual(
            potential_gf=100.0, sale_area_pct=80.0, sale_price_chf_m2=8000.0, construction_chf_m2=3000.0,
            ancillary_pct=15.0, existing_gf=0.0, demolition_chf_m2=150.0, financing_pct=3.0,
            reserve_pct=15.0), notes=[])
        self.assertEqual(sorted(pdf_links(pdf)), sorted(f"https://amtsblatt.ag.ch/ekab/00.900.{950 + i}/pdf/"
                                                        for i in range(30)))
        from pypdf import PdfReader
        text = "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages).replace("\n", "")
        self.assertIn("ENDE-29", text)
        for page in PdfReader(io.BytesIO(pdf)).pages:
            width, height = float(page.mediabox.width), float(page.mediabox.height)
            for annotation in page.get("/Annots") or []:
                left, bottom, right, top = (float(v) for v in annotation.get_object()["/Rect"])
                self.assertTrue(0 < left < right < width and 0 < bottom < top < height,
                                (left, bottom, right, top))

    def test_a_row_longer_than_a_page_runs_over_pages(self):
        """A title and an address of any length: one row taller than a page
        continues on the next instead of failing the sheet."""
        url = "https://amtsblatt.ag.ch/ekab/00.900.990/pdf/?" + "x" * 9000
        title = "Gestaltungsplan «Lang» " + "mit Sondervorschriften zur Etappierung " * 180 + "ENDE"
        pubs, _ = planning.publications([{
            "pub_nr": "00.900.990", "published_on": "2026-09-01", "authority": "Gemeinde Möhlin",
            "rubric": "Gemeinden / Bau- und Nutzungsordnung", "title": title,
            "url": url, "municipality": "Möhlin"}])
        view = planning.listing(pubs, "Möhlin")
        _, rows = detail._regulation_block(self.edict(), "", "", planning=view, municipality="Möhlin",
                                           today=datetime.date(2026, 10, 6))
        pdf = report.build(title="T", subtitle="S", blocks=[("E", rows)], steps=E.residual(
            potential_gf=100.0, sale_area_pct=80.0, sale_price_chf_m2=8000.0, construction_chf_m2=3000.0,
            ancillary_pct=15.0, existing_gf=0.0, demolition_chf_m2=150.0, financing_pct=3.0,
            reserve_pct=15.0), notes=[])
        # One annotation per wrapped line of the address, every one to it.
        self.assertEqual(set(pdf_links(pdf)), {url})
        from pypdf import PdfReader
        self.assertIn("ENDE", "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages))

    def test_brackets_in_an_address_stay_inside_its_link_on_paper(self):
        url = "https://www.seon.ch/planung/(alt)/plan[1].pdf"
        printed = detail._printed_link(url)
        self.assertEqual(printed, "[seon.ch/planung/(alt)/plan1.pdf](https://www.seon.ch/planung/%28alt%29/plan%5B1%5D.pdf)")
        pdf = report.build(title="T", subtitle="S", blocks=[("E", [("Quelle", printed)])], steps=E.residual(
            potential_gf=100.0, sale_area_pct=80.0, sale_price_chf_m2=8000.0, construction_chf_m2=3000.0,
            ancillary_pct=15.0, existing_gf=0.0, demolition_chf_m2=150.0, financing_pct=3.0,
            reserve_pct=15.0), notes=[])
        self.assertEqual(pdf_links(pdf), ["https://www.seon.ch/planung/%28alt%29/plan%5B1%5D.pdf"])

    def test_no_link_on_paper_leaves_its_attribute(self):
        """A quote in an address must not end the link's attribute and start
        another — on paper only web addresses are links."""
        for bad in ("javascript:alert(1)", "https://[invalid/",
                    'https://amtsblatt.ag.ch/ekab/00.1/"href="javascript:alert%281%29'):
            self.assertEqual(detail._printed_link(bad), "", bad)
        pdf = report.build(title="T", subtitle="S", blocks=[("E", [(
            "Quelle", '[x](https://amtsblatt.ag.ch/ekab/00.1/"href="javascript:alert%281%29)')])],
            steps=E.residual(potential_gf=100.0, sale_area_pct=80.0, sale_price_chf_m2=8000.0,
                             construction_chf_m2=3000.0, ancillary_pct=15.0, existing_gf=0.0,
                             demolition_chf_m2=150.0, financing_pct=3.0, reserve_pct=15.0), notes=[])
        links = pdf_links(pdf)
        self.assertTrue(links and all(link.startswith("https://amtsblatt.ag.ch/") for link in links), links)
        self.assertFalse(any("javascript" in link and '"' in link for link in links), links)

    def test_by_default_a_long_list_runs_over_pages(self):
        """Sixty publications for the municipality and sixty for the canton are
        a row each: one cell holding them all is taller than a page, and
        reportlab cannot split a row."""
        local = [{"pub_nr": f"00.900.{300 + i}", "published_on": f"2026-0{1 + i % 9}-{10 + i % 18:02d}",
                  "authority": "Gemeinde Möhlin", "rubric": "Gemeinden / Bau- und Nutzungsordnung",
                  "title": f"Gestaltungsplan «Areal {i:02d}»; öffentliche Auflage der Teiländerung "
                           "mit Sondervorschriften", "text": "",
                  "url": f"https://amtsblatt.ag.ch/ekab/00.900.{300 + i}/pdf/"} for i in range(60)]
        wide = [{"pub_nr": f"00.900.{400 + i}", "published_on": f"2025-0{1 + i % 9}-{10 + i % 18:02d}",
                 "authority": "Departement Bau, Verkehr und Umwelt",
                 "rubric": "Kanton / Anhörungs- und Mitwirkungsverfahren",
                 "title": f"Teilrevision Baugesetz (BauG), Paket {i:02d}; Anhörung", "text": "",
                 "url": f"https://amtsblatt.ag.ch/ekab/00.900.{400 + i}/pdf/"} for i in range(60)]
        pubs, problems = planning.publications(local + wide)
        self.assertEqual(problems, [])
        view = planning.view(pubs, "Möhlin", [self.edict()], today=datetime.date(2026, 10, 5))
        block = detail._regulation_block(self.edict(), "", "", planning=view, municipality="Möhlin")
        self.assertEqual(len(printed_list(block[1], "Publikationen (Amtsblatt)")), 60)
        self.assertEqual(len(printed_list(block[1], "Kanton (Amtsblatt)")), 60)
        pdf = report.build(
            title="Test", subtitle="Test", blocks=[block],
            steps=E.residual(
                potential_gf=100.0, sale_area_pct=80.0, sale_price_chf_m2=8000.0,
                construction_chf_m2=3000.0, ancillary_pct=15.0, existing_gf=0.0,
                demolition_chf_m2=150.0, financing_pct=3.0, reserve_pct=15.0,
            ),
            notes=[])
        from pypdf import PdfReader
        pages = PdfReader(io.BytesIO(pdf)).pages
        text = " ".join(p.extract_text() or "" for p in pages)
        self.assertGreater(len(pages), 2)
        self.assertIn("Areal 59", text)
        self.assertIn("Paket 59", text)
        self.assertEqual(len(pdf_links(pdf)), 120)

    def test_by_default_a_title_cannot_become_a_link_on_paper(self):
        pubs, _ = planning.publications([{
            "pub_nr": "00.900.251", "published_on": "2026-09-01", "authority": "Gemeinde Möhlin",
            "rubric": "Gemeinden / Bau- und Nutzungsordnung",
            "title": "[Klick](https://evil.example) *fett*", "text": "",
            "url": "https://amtsblatt.ag.ch/ekab/00.900.251/pdf/"}])
        view = planning.view(pubs, "Möhlin", [self.edict()], today=datetime.date(2026, 10, 5))
        _, rows = detail._regulation_block(self.edict(), "", "", planning=view, municipality="Möhlin")
        self.assertEqual(dict(rows)["Publikationen (Amtsblatt)"],
                         "01.09.2026 Publikation: Klick(https://evil.example) fett — "
                         "Publ.-Nr. 00.900.251 · [amtsblatt.ag.ch/ekab/00.900.251/pdf/](https://amtsblatt.ag.ch/ekab/00.900.251/pdf/)")
        pdf = report.build(title="T", subtitle="S", blocks=[("E", rows)], steps=E.residual(
            potential_gf=100.0, sale_area_pct=80.0, sale_price_chf_m2=8000.0, construction_chf_m2=3000.0,
            ancillary_pct=15.0, existing_gf=0.0, demolition_chf_m2=150.0, financing_pct=3.0,
            reserve_pct=15.0), notes=[])
        self.assertEqual(pdf_links(pdf), ["https://amtsblatt.ag.ch/ekab/00.900.251/pdf/"])

    def test_by_default_no_approval_is_printed_onto_the_regulation(self):
        pubs, _ = planning.publications([{
            "pub_nr": "00.900.206", "published_on": "2023-12-20", "authority": "Regierungsrat",
            "rubric": "Kanton / Raumplanung", "title": "Genehmigung von Nutzungsplänen",
            "text": "Gemeinde Möhlin; Allgemeine Nutzungsplanung, Gesamtrevision; Genehmigung",
            "url": "https://amtsblatt.ag.ch/ekab/00.900.206/pdf/"}])
        view = planning.view(pubs, "Möhlin", [self.edict()], today=datetime.date(2026, 10, 5))
        self.assertIn(self.edict(), view.approvals)  # the inference path would merge it
        _, rows = detail._regulation_block(self.edict(), "", "", planning=view,
                                           municipality="Möhlin", edicts=[self.edict()])
        self.assertNotIn("Genehmigung publiziert", rows[0][1])
        self.assertIn("20.12.2023 Publikation: Allgemeine Nutzungsplanung, Gesamtrevision; Genehmigung — "
                      "Publ.-Nr. 00.900.206",
                      dict(rows)["Publikationen (Amtsblatt)"])

    def test_none_on_paper_says_when_coverage_started_or_that_it_is_unknown(self):
        _, rows = detail._regulation_block(self.edict(), "", "", municipality="Möhlin",
                                           planning=planning.View(status="ok"))
        self.assertIn("Erfassungsbeginn nicht angegeben", rows[-1][1])

    def test_an_uncovered_municipality_is_printed_as_not_tracked(self):
        _, rows = detail._regulation_block(self.edict(), "", "",
                                           planning=planning.View(status="uncovered"),
                                           municipality="Möhlin")
        self.assertIn("nicht erfasst", rows[-1][1])

    def test_a_link_in_the_data_becomes_a_link_only_for_web_addresses(self):
        """Values on the sheet are markdown; one that came from a publication
        store must not smuggle a `javascript:` link into the PDF."""
        self.assertNotIn("<link", report._rich("[Plan](javascript:alert(1))"))
        self.assertIn('<link href="https://agis.ag.ch"',
                      report._rich("[AGIS](https://agis.ag.ch)"))

    def test_an_unreadable_store_is_printed_as_such(self):
        _, rows = detail._regulation_block(
            self.edict(), "", "",
            planning=planning.View(status="error", error="Verzeichnis nicht lesbar (OSError)"),
            municipality="Möhlin")
        self.assertIn("nicht lesbar", rows[-1][1])

    def test_it_reaches_the_printed_page(self):
        """Through reportlab, not just into the list handed to it."""
        pdf = report.build(
            title="Test", subtitle="Test",
            blocks=[detail._regulation_block(self.edict(), "", "")],
            steps=E.residual(
                potential_gf=100.0, sale_area_pct=80.0, sale_price_chf_m2=8000.0,
                construction_chf_m2=3000.0, ancillary_pct=15.0, existing_gf=0.0,
                demolition_chf_m2=150.0, financing_pct=3.0, reserve_pct=15.0,
            ),
            notes=[],
        )
        from pypdf import PdfReader
        text = " ".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)
        self.assertIn("Stand der Rechtsvorschrift", text)
        self.assertIn("13.12.2023", text)


class SwissDateBoundaryTest(unittest.TestCase):
    """Late in the UTC evening Switzerland can already be on the next day.
    Every block-E answer has to be computed on the Swiss
    date, or the card and the exported sheet name different editions — and
    different stages of the same procedure — for the same day."""

    SERVER = datetime.date(2026, 10, 7)  # 22:30 UTC: the server's date.today()
    SWISS = datetime.date(2026, 10, 8)   # 00:30 next day in Europe/Zurich
    CHECKED = "geprüft am 06.10.2026: Publikation gelesen"

    class FakeUTCDate(datetime.date):
        """The server clock at 22:30 UTC: today() runs a day behind Zurich."""

        @classmethod
        def today(cls):
            return SwissDateBoundaryTest.SERVER

    def edict(self, in_force, document="https://oereblex.ag.ch/api/attachments/99"):
        return regulations.Edict(municipality="Möhlin", title="Bau- und Nutzungsordnung",
                                 abbreviation="BNO", in_force=in_force, syst_nr="4254",
                                 document=document)

    def auflage_store(self, *windows):
        """Möhlin's publications as the importer writes them: each a checked
        (verified) öffentliche Auflage with its source-bound period."""
        records = []
        for nr, published_on, name, period in windows:
            source = f"https://amtsblatt.ag.ch/ekab/{nr}/pdf/"
            records.append({
                "pub_nr": nr, "published_on": published_on,
                "authority": "Gemeinde Möhlin", "rubric": "Gemeinden / Bau- und Nutzungsordnung",
                "title": f'Gestaltungsplan "{name}"; öffentliche Auflage',
                "text": "", "url": source, "municipality": "Möhlin",
                "verified": {"source": source, "verified_on": "2026-10-06",
                             "method": "Publikation gelesen", "stage": "auflage",
                             "auflage_from": period[0], "auflage_to": period[1]}})
        pubs, problems = planning.publications(records)
        self.assertEqual(problems, [])
        return planning.View(publications=planning.listing(pubs, "Möhlin").publications,
                             stand=self.SERVER, since=datetime.date(2026, 1, 1))

    def test_a_verified_auflage_reads_the_same_on_card_and_sheet_at_the_boundary(self):
        """One Auflage ends on the server day, one begins on the Swiss day:
        card and sheet must agree which is current and which is over, on the
        Swiss date — not on the server's yesterday."""
        store = self.auflage_store(
            ("00.900.912", "2026-10-05", "Dorf", ("2026-10-08", "2026-10-28")),
            ("00.900.911", "2026-09-28", "Rain", ("2026-10-02", "2026-10-07")))
        with patch.object(detail, "date", self.FakeUTCDate), \
                patch.object(detail.PP, "swiss_today", return_value=self.SWISS):
            card = detail._regulation_card_html(
                {"municipality": "Möhlin"}, [], self.edict(datetime.date(2023, 12, 13)),
                "", planning=store)
            _, rows = detail._regulation_block(
                self.edict(datetime.date(2023, 12, 13)), "", "",
                planning=store, municipality="Möhlin")
        printed = printed_list(rows, "Publikationen (Amtsblatt)")
        active = next(line for line in printed if "Gestaltungsplan \"Dorf\"" in line)
        ended = next(line for line in printed if "Gestaltungsplan \"Rain\"" in line)
        # On paper: the Auflage beginning on the Swiss day is current, with its
        # checked period; the one that ended on the server day is neutral.
        self.assertIn("Entwurf in Auflage: Gestaltungsplan \"Dorf\"; öffentliche Auflage", active)
        self.assertIn("Öffentliche Auflage 08.10.2026\u201328.10.2026 · " + self.CHECKED, active)
        self.assertIn("Öffentliche Auflage 02.10.2026\u201307.10.2026 beendet — heutiger Stand "
                      "nicht geprüft · " + self.CHECKED, ended)
        self.assertNotIn("Entwurf in Auflage", ended)
        # On screen: the same labels, the same checked notes, the same dates.
        self.assertIn("Öffentliche Auflage 08.10.2026\u201328.10.2026 · " + self.CHECKED, card)
        self.assertIn("Öffentliche Auflage 02.10.2026\u201307.10.2026 beendet — heutiger Stand "
                      "nicht geprüft · " + self.CHECKED, card)
        badges = re.findall(r'detail-regulation-status[^"]*"[^>]*>([^<]+)<', card)
        self.assertEqual(badges, ["Entwurf in Auflage", "Publikation"])

    def test_the_boundary_changes_the_answer_not_the_clock(self):
        """Proof the dates above straddle the boundary: the same helpers, on an
        explicit server date, call the expired Auflage current — so a sheet
        defaulting to the server date would contradict the card next to it."""
        store = self.auflage_store(
            ("00.900.911", "2026-09-28", "Rain", ("2026-10-02", "2026-10-07")))
        _, rows = detail._regulation_block(self.edict(datetime.date(2023, 12, 13)), "", "",
                                           planning=store, municipality="Möhlin",
                                           today=self.SERVER)
        printed = printed_list(rows, "Publikationen (Amtsblatt)")
        self.assertIn("Entwurf in Auflage: Gestaltungsplan \"Rain\"; öffentliche Auflage",
                      printed[0])
        card = detail._regulation_card_html({"municipality": "Möhlin"}, [],
                                            self.edict(datetime.date(2023, 12, 13)), "",
                                            planning=store, today=self.SERVER)
        self.assertIn("Entwurf in Auflage", card)
        self.assertNotIn("beendet", card)

    def test_the_same_days_bno_is_governing_on_card_and_sheet(self):
        """A BNO taking force on the Swiss day is the governing edition on the
        card and on paper. The sheet must not print it again as a future
        edition, the way it did when the PDF helpers defaulted to the server
        date and read it as tomorrow."""
        previous = self.edict(datetime.date(2023, 12, 13),
                              document="https://oereblex.ag.ch/api/attachments/98")
        current = self.edict(self.SWISS)
        edicts = [current, previous]
        with patch.object(detail, "date", self.FakeUTCDate), \
                patch.object(detail.PP, "swiss_today", return_value=self.SWISS):
            own, note = detail.R.for_municipality(edicts, "Möhlin", 4254,
                                                  today=detail.PP.swiss_today())
            card = detail._regulation_card_html({"municipality": "Möhlin"}, edicts, own, note,
                                                planning=planning.View(status="off"))
            _, rows = detail._regulation_block(own, note, "",
                                               planning=planning.View(status="off"),
                                               municipality="Möhlin", edicts=edicts)
        self.assertIs(own, current)  # the same-day edition governs, as `page` computes it
        self.assertEqual(note, "")
        self.assertEqual(rows[0][0], "BNO")
        self.assertIn("in Kraft seit 08.10.2026", rows[0][1])
        printed = " ".join(f"{label}: {value}" for label, value in rows)
        self.assertNotIn("in Kraft ab 08.10.2026", printed)  # no future duplicate on paper
        self.assertIn("OEREBlex · in Kraft seit 08.10.2026", card)
        self.assertNotIn("Künftig in Kraft", card)
        self.assertIn("2 Vorschriften in Kraft", card.partition("</summary>")[0])
