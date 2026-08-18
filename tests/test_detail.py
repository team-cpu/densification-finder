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
import report


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
            self.bfs, self.parcel, self.delta, self.area = connection.execute(
                "SELECT bfs, parcel, delta, area FROM parcel_results "
                "ORDER BY delta DESC LIMIT 1"
            ).fetchone()
        self.pid = f"{self.bfs}:{self.parcel}"

    def tearDown(self):
        paths.DB = self.original_database
        self.tempdir.cleanup()

    def open_detail(self):
        app = AppTest.from_file(os.path.join(paths.HERE, "app.py"), default_timeout=30)
        app.session_state[detail.SELECTED] = self.pid
        app.run()
        self.assertFalse(app.exception)
        return app

    def test_all_three_blocks_render_for_a_selected_parcel(self):
        app = self.open_detail()
        self.assertEqual(
            [s.value for s in app.subheader],
            ["A · Grunddaten", "B · Potenzial", "C · Residualwertrechnung",
             "D · Rechtsgrundlagen"],
        )
        labels = {n.label for n in app.number_input}
        self.assertIn("Potenzial (m² GF)", labels)
        self.assertIn("Verkaufspreis (CHF/m²)", labels)
        self.assertIn("Reserve / Unvorhergesehenes (%)", labels)

        # Block B is pre-filled from the pipeline, not typed in again.
        potential = next(n for n in app.number_input if n.label == "Potenzial (m² GF)")
        self.assertAlmostEqual(potential.value, self.delta, places=6)

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
        self.assertEqual(len(app.subheader), 0)

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
