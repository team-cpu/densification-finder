import datetime
import io
import os
import shutil
import sqlite3
import tempfile
import unittest

import streamlit as st
from streamlit.testing.v1 import AppTest

import detail
import economics as E
import paths
import regulations
import report


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


class DetailViewTest(unittest.TestCase):
    """The single-parcel analysis view, driven the way the interface drives it."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.tempdir.name, "results.sqlite")
        shutil.copy2(paths.SEED_DB, self.database)
        self.original_database = paths.DB
        paths.DB = self.database
        st.cache_data.clear()

        with sqlite3.connect(self.database) as connection:
            (self.bfs, self.parcel, self.delta, self.area,
             self.municipality) = connection.execute(
                "SELECT bfs, parcel, delta, area, municipality FROM parcel_results "
                "ORDER BY delta DESC LIMIT 1"
            ).fetchone()
        self.pid = f"{self.bfs}:{self.parcel}"

    def tearDown(self):
        paths.DB = self.original_database
        self.tempdir.cleanup()

    def open_detail(self):
        stub_regulations(self, getattr(self, "municipality", None),
                         getattr(self, "bfs", None))
        app = AppTest.from_file(os.path.join(paths.HERE, "app.py"), default_timeout=30)
        app.session_state[detail.SELECTED] = self.pid
        app.run()
        self.assertFalse(app.exception)
        return app

    def test_all_four_blocks_render_for_a_selected_parcel(self):
        app = self.open_detail()
        self.assertEqual(
            [s.value for s in app.subheader],
            ["A · Grunddaten", "B · Potenzial", "C · Residualwertrechnung",
             "E · Neueste Änderungen"],
        )
        # D is the fourth block, folded away rather than dropped.
        self.assertIn("D · Rechtsgrundlagen", [e.label for e in app.expander])
        labels = {n.label for n in app.number_input}
        self.assertIn("Potenzial (m² GF)", labels)
        self.assertIn("Verkaufspreis (CHF/m²)", labels)
        self.assertIn("Reserve / Unvorhergesehenes (%)", labels)

        # Block B is pre-filled from the pipeline, not typed in again.
        potential = next(n for n in app.number_input if n.label == "Potenzial (m² GF)")
        self.assertAlmostEqual(potential.value, self.delta, places=6)

    def test_the_references_are_folded_away_and_the_sources_stay_folded(self):
        """Philipp asked for block D to open on demand like «Annahmen und
        Quellen», so the parcel a user is judging is not pushed off the screen
        by a list of documents they will read once."""
        app = self.open_detail()
        # `expanded` lives on the block proto; the test helper exposes the
        # label but not the state.
        folded = {e.label: e.proto.expanded for e in app.expander}
        self.assertFalse(folded["D · Rechtsgrundlagen"])
        self.assertFalse(folded["Annahmen und Quellen"])

    def test_the_result_is_written_above_the_form_that_produces_it(self):
        """The bar has to be drawn before the inputs to sit above them: the
        container is claimed early and filled at the end, once the numbers it
        shows exist. This is the check that the claim did not move — it is the
        one thing keeping the total off the foot of the page now that the bar
        no longer sticks."""
        app = self.open_detail()
        order = [
            (element.type, getattr(element, "label", ""))
            for element in app.main
        ]
        result = order.index(("metric", "Residualer Landwert"))
        first_input = min(
            i for i, (kind, _) in enumerate(order) if kind == "number_input"
        )
        self.assertLess(result, first_input)

    def test_the_caveat_sits_directly_under_the_calculation(self):
        """It used to sit under the export button, three screens below the
        figure it qualifies. Directly under means directly under — nothing
        between the table and the sentence that says what the number is not."""
        app = self.open_detail()
        captions = [c.value for c in app.caption]
        self.assertIn(detail.RESULT_CAVEAT, captions)
        self.assertIn(detail.DISCLAIMER, captions)

        body = [getattr(e, "value", None) for e in app.main]
        table = next(i for i, v in enumerate(body)
                     if isinstance(v, str) and 'class="calc"' in v)
        self.assertEqual(body[table + 1], detail.DISCLAIMER)

    def test_a_negative_result_does_not_grow_the_result_bar(self):
        """The warning used to be its own block inside the bar, which added 72px
        to it in the one case where the inputs underneath most need the room. It
        is the same single caption line now, whatever the sign."""
        app = self.open_detail()
        self.assertEqual(len(app.warning), 0)
        self.assertIn(detail.RESULT_CAVEAT, [c.value for c in app.caption])

        cost = next(n for n in app.number_input
                    if n.label == "Baukosten (CHF/m²)")
        cost.set_value(99999.0).run()
        self.assertFalse(app.exception)
        land = next(m for m in app.metric if m.label == "Residualer Landwert")
        self.assertIn("-", land.value)
        # The strip gained no block: same one line, different words.
        self.assertEqual(len(app.warning), 0)
        captions = [c.value for c in app.caption]
        self.assertIn(detail.NEGATIVE_CAVEAT, captions)
        self.assertNotIn(detail.RESULT_CAVEAT, captions)

    def test_the_export_is_at_the_top(self):
        """Philipp asked for it in the top right corner. It is built at the end
        of the run — the column it lands in is claimed at the start."""
        app = self.open_detail()
        kinds = [e.type for e in app.main]
        export = next(i for i, e in enumerate(app.main)
                      if e.type == "download_button")
        first_input = min(i for i, k in enumerate(kinds) if k == "number_input")
        self.assertLess(export, first_input)

    def test_the_calculation_is_not_confined_to_a_column(self):
        """Option C. The split is for the two things read against each other —
        the registers and the assumptions. The calculation is read on its own,
        and it is the one thing on the page that wants width: in half a column
        its longest line, about seventy characters of arithmetic, wrapped the
        step names and pushed the table into its own sideways scroll."""
        app = self.open_detail()
        boxed = [m.value for column in app.columns for m in column.markdown
                 if 'class="calc"' in m.value]
        self.assertEqual(boxed, [], "the calculation is back inside a column")
        # …and it is still on the page at all.
        self.assertTrue(any('class="calc"' in m.value for m in app.markdown))
        # The assumptions list, by contrast, stays inside the column — it
        # explains the figures above it and closes the shorter side.
        self.assertIn(
            "Annahmen und Quellen",
            [e.label for column in app.columns for e in column.expander],
        )

    def test_the_change_list_says_when_it_could_not_be_fetched(self):
        """The dangerous failure mode for this panel is the silent one: an empty
        change list reads as "nothing has changed lately", which is the opposite
        of "the canton did not answer". Block D has to be unaffected — it comes
        out of the parcel's own ÖREB extract, not this request."""
        real = regulations.load
        regulations.load = lambda timeout=30: ([], "URLError: [Errno 8] nodename nor servname provided")
        self.addCleanup(lambda: setattr(regulations, "load", real))
        st.cache_data.clear()

        app = AppTest.from_file(os.path.join(paths.HERE, "app.py"), default_timeout=30)
        app.session_state[detail.SELECTED] = self.pid
        app.run()
        self.assertFalse(app.exception)

        captions = " ".join(c.value for c in app.caption)
        self.assertIn("nicht abrufbar", captions)
        self.assertIn("nodename nor servname", captions)
        # …and the page is otherwise whole.
        self.assertIn("E · Neueste Änderungen", [s.value for s in app.subheader])
        self.assertIn("D · Rechtsgrundlagen", [e.label for e in app.expander])
        self.assertTrue(any(m.label == "Residualer Landwert" for m in app.metric))

    def test_the_parcel_carries_its_own_regulation_date(self):
        """What block D cannot say: since when. It is the first line of E, above
        the canton-wide list, because it is the only line about this parcel."""
        app = self.open_detail()
        body = " ".join(m.value for m in app.markdown)
        self.assertIn("in Kraft seit", body)
        # three rows on show, the remainder folded away
        self.assertTrue(any(e.label.startswith("Alle ") and "Änderungen" in e.label
                            for e in app.expander))

    def test_each_step_carries_the_formula_that_produced_it(self):
        """Philipp asked for the reasoning to be visible on hover, and for the
        tooltip not to be a second copy that can go stale. It is rendered from
        the rule that computed the number."""
        app = self.open_detail()
        body = " ".join(m.value for m in app.markdown)
        self.assertIn("verkaufsflaeche * verkaufspreis", body)
        self.assertIn("potenzial_gf * baukosten_pro_m2", body)
        self.assertIn('class="calc__name"', body)
        # The help on the input says where its number goes, read off the formulas.
        price = next(n for n in app.number_input if n.label == "Verkaufspreis (CHF/m²)")
        self.assertIn("{verkaufspreis}", price.help)
        self.assertIn("Verkaufserlös", price.help)

    def test_the_list_is_not_drawn_while_a_parcel_is_open(self):
        """A conditional view, not a second page — and not both at once."""
        app = self.open_detail()
        self.assertEqual(len(app.dataframe), 0)
        self.assertNotIn(
            "Verdichtungspotenzial — Kanton Aargau", [t.value for t in app.title]
        )

    def test_back_returns_to_the_list(self):
        app = self.open_detail()
        back = next(b for b in app.button if b.label.startswith("←"))
        back.click().run()
        self.assertFalse(app.exception)
        self.assertNotIn(detail.SELECTED, app.session_state)
        # The hotlist is back. Counting tables would be counting the wrong
        # thing: a second one appears as soon as the cadastre has excluded a
        # parcel from the shortlist, which is data, not behaviour.
        self.assertIn("Adresse", app.dataframe[0].value.columns)
        self.assertEqual(
            [heading.value for heading in app.subheader],
            ["Merkliste & Eigentümerkontakte"],
        )

    def test_editing_an_assumption_recalculates_live(self):
        """No recalculate button: the residual value has to follow the input on
        the same rerun, which is the whole interaction the brief describes."""
        app = self.open_detail()
        before = next(m for m in app.metric if m.label == "Residualer Landwert").value

        price = next(n for n in app.number_input if n.label == "Verkaufspreis (CHF/m²)")
        price.set_value(price.value * 2).run()
        self.assertFalse(app.exception)
        after = next(m for m in app.metric if m.label == "Residualer Landwert").value
        self.assertNotEqual(before, after)
        self.assertGreater(self._amount(after), self._amount(before))

    def test_unit_count_follows_the_assumed_unit_size(self):
        app = self.open_detail()
        size = next(n for n in app.number_input if n.label == "Wohnungsgrösse (m²)")
        size.set_value(180.0).run()
        self.assertFalse(app.exception)
        units = next(m for m in app.metric if m.label == "Mögliche Wohnungen")
        self.assertAlmostEqual(float(units.value), self.delta / 180.0, places=1)

    def test_own_numbers_follow_the_user_to_the_next_parcel(self):
        """A developer's construction cost does not change because they clicked
        a different row. Keeping these per parcel would mean retyping seven
        numbers on every lead."""
        app = self.open_detail()
        price = next(n for n in app.number_input if n.label == "Verkaufspreis (CHF/m²)")
        price.set_value(9999.0).run()

        with sqlite3.connect(self.database) as con:
            other = con.execute(
                "SELECT bfs, parcel, delta FROM parcel_results "
                "WHERE bfs || ':' || parcel <> ? ORDER BY delta DESC LIMIT 1",
                (self.pid,),
            ).fetchone()
        next(b for b in app.button if b.label.startswith("←")).click().run()
        app.session_state[detail.SELECTED] = f"{other[0]}:{other[1]}"
        app.run()
        self.assertFalse(app.exception)

        price = next(n for n in app.number_input if n.label == "Verkaufspreis (CHF/m²)")
        self.assertEqual(price.value, 9999.0)
        # …but the parcel's own figures do not follow it.
        potential = next(n for n in app.number_input if n.label == "Potenzial (m² GF)")
        self.assertAlmostEqual(potential.value, other[2], places=6)
        self.assertNotAlmostEqual(potential.value, self.delta, places=6)

    def test_reset_restores_the_published_benchmarks(self):
        app = self.open_detail()
        price = next(n for n in app.number_input if n.label == "Verkaufspreis (CHF/m²)")
        price.set_value(9999.0).run()
        next(b for b in app.button if b.label == "Annahmen zurücksetzen").click().run()
        self.assertFalse(app.exception)
        price = next(n for n in app.number_input if n.label == "Verkaufspreis (CHF/m²)")
        self.assertEqual(price.value, E.BENCHMARKS["sale_price_chf_m2"].value)

    def test_a_selection_that_no_longer_exists_says_so(self):
        """A recompute can drop a parcel out of the table while it is open."""
        stub_regulations(self)
        app = AppTest.from_file(os.path.join(paths.HERE, "app.py"), default_timeout=30)
        app.session_state[detail.SELECTED] = "9999:12345"
        app.run()
        self.assertFalse(app.exception)
        self.assertTrue(any("steht nicht mehr" in w.value for w in app.warning))

    @staticmethod
    def _amount(text):
        return float(text.replace("CHF", "").replace("’", "").strip())


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
        app.run()
        self.assertFalse(app.exception)
        return app

    def text(self, app):
        return " ".join(m.value for m in app.markdown)

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
        self.assertTrue(
            any("Erst nach der ÖREB-Abfrage" in c.value for c in app.caption)
        )

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
        potential = next(n for n in app.number_input if n.label == "Potenzial (m² GF)")
        potential.set_value(0.0).run()
        self.assertFalse(app.exception)
        units = next(m for m in app.metric if m.label == "Mögliche Wohnungen")
        self.assertEqual(float(units.value), 0.0)
        land = next(m for m in app.metric if m.label == "Residualer Landwert")
        self.assertIn("CHF", land.value)

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
            self.assertEqual([t.value for t in app.title],
                             ["Verdichtungspotenzial — Kanton Aargau"])
            self.assertEqual(len(app.subheader), 0)
            self.assertEqual(len(app.number_input), 0)
        finally:
            del os.environ["APP_PASSWORD"]


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
