import os
import shutil
import sqlite3
import tempfile
import unittest

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

import ingest
import navigation
import paths
import shell


class DataAsOfTest(unittest.TestCase):
    """`shell.data_as_of` is the one place that turns `runs.finished_at`
    into the banner's date — it has to pick the newest run, not just
    whatever row happens to sort first out of the database, and it has to
    admit "unknown" rather than inventing today's date when there is
    nothing to compute from."""

    def test_picks_the_latest_run_regardless_of_row_order(self):
        runs = pd.DataFrame(
            {
                "finished_at": [
                    "2026-08-20 10:00:00",
                    "2026-08-25 13:35:02",
                    "2026-08-19 09:00:00",
                ]
            }
        )
        self.assertEqual(shell.data_as_of(runs), "25.08.2026")

    def test_a_single_row_formats_correctly(self):
        runs = pd.DataFrame({"finished_at": ["2026-01-05 08:00:00"]})
        self.assertEqual(shell.data_as_of(runs), "05.01.2026")

    def test_an_empty_table_reads_as_unknown(self):
        """No rows means no run has ever finished — the banner must say so
        rather than crash on an empty `.max()`."""
        self.assertEqual(shell.data_as_of(pd.DataFrame({"finished_at": []})), "—")

    def test_an_all_null_column_reads_as_unknown(self):
        """Rows exist (municipalities were queued) but none finished — still
        nothing genuine to show."""
        self.assertEqual(
            shell.data_as_of(pd.DataFrame({"finished_at": [None, None]})), "—"
        )

    def test_a_missing_table_reads_as_unknown(self):
        """`app.py`'s own `load()` returns `None` for `runs` when the
        database file does not exist yet — the banner must survive that,
        not the ingest-database edge case being reached first."""
        self.assertEqual(shell.data_as_of(None), "—")

    def test_a_frame_without_the_column_reads_as_unknown(self):
        self.assertEqual(shell.data_as_of(pd.DataFrame({"bfs": [261]})), "—")


class ShellHeaderRegressionTest(unittest.TestCase):
    """The shell wraps the router in `app.py`; a page that stopped calling
    it (or a future page added without it) would still pass every
    page-specific test while quietly losing the header. This walks all four
    pages of the real app and checks the wordmark survives on each."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.tempdir.name, "results.sqlite")
        shutil.copy2(paths.SEED_DB, self.database)
        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM oereb_cache")
            ingest.schema(connection)

        self.original_database = paths.DB
        paths.DB = self.database
        st.cache_data.clear()

    def tearDown(self):
        paths.DB = self.original_database
        self.tempdir.cleanup()

    def _html_bodies(self, app):
        # `st.html` has no typed AppTest accessor (streamlit/testing/v1's
        # element_tree.py falls through to `UnknownElement` for it), so the
        # header's markup is read off the raw `Html` proto's `body` field
        # rather than through a `.value`-style helper — `UnknownElement.value`
        # assumes an `id` field that `Html` does not have and raises on it.
        return [node.proto.body for node in app.get("html")]

    def test_the_wordmark_survives_every_page(self):
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        ).run()
        self.assertFalse(app.exception)

        for page in navigation.PAGES:
            app.session_state[navigation.PAGE] = page
            app.run()
            self.assertFalse(app.exception, f"{page} raised on render")
            bodies = self._html_bodies(app)
            self.assertTrue(
                any("Scope" in body for body in bodies),
                f"wordmark missing on {page}",
            )
            self.assertIn(
                shell._INPUT_CSS.strip(), [body.strip() for body in bodies],
                f"shared editable input surfaces missing on {page}",
            )

    def test_shared_input_colors_do_not_override_disabled_or_focus_styles(self):
        css = shell._INPUT_CSS
        for selector in (
            '[data-testid="stTextInputRootElement"]:has(input:enabled)',
            '[data-testid="stNumberInputContainer"]:has(input:enabled)',
            '[data-testid="stSelectbox"] [role="group"]:has(input:enabled)',
            '[data-testid="stTextArea"] textarea:enabled',
        ):
            self.assertIn(selector, css)
        self.assertIn("background-color: #fff !important", css)
        self.assertNotIn("border:", css)
        self.assertNotIn("outline:", css)
        self.assertNotIn("box-shadow:", css)

    def test_the_shell_container_and_style_block_are_present(self):
        """`app.py` used to draw a bare `st.title`; this is the regression
        test for silently losing the shell back to that — the scoped style
        block and the `app_shell` container key are what the CSS in
        `shell.py` depends on to stay off the four pages it does not style."""
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        ).run()
        self.assertFalse(app.exception)
        bodies = self._html_bodies(app)
        self.assertTrue(
            any(".st-key-app_shell" in body for body in bodies),
            "the shell's scoped <style> block did not render",
        )
        css = next(body for body in bodies if ".st-key-app_shell" in body)
        self.assertIn("flex-wrap: nowrap !important", css)
        self.assertIn("@media (max-width: 720px)", css)
        self.assertNotIn("@media (max-width: 960px)", css)
        self.assertIn('[data-testid="stHeader"]', css)
        self.assertIn('[data-testid="stToolbar"]', css)
        self.assertTrue(
            any("normiq-shell-logo" in body for body in bodies),
            "the sanitiser-safe logo element did not render",
        )
        brand = next(body for body in bodies if "normiq-shell-brand" in body)
        self.assertNotIn("Kanton Aargau", brand)
        self.assertIn("border: 0 !important", css)

    def test_the_data_as_of_banner_uses_the_real_run_date(self):
        """Not a placeholder: the seed database's own `runs.finished_at`
        must show up verbatim, so a future change that hardcodes or drops
        the date is caught here rather than only by eye."""
        with sqlite3.connect(self.database) as connection:
            expected = connection.execute(
                "SELECT MAX(finished_at) FROM runs"
            ).fetchone()[0]
        expected_display = pd.Timestamp(expected).strftime("%d.%m.%Y")

        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        ).run()
        self.assertFalse(app.exception)
        bodies = self._html_bodies(app)
        banner = next(body for body in bodies if "normiq-shell-data" in body)
        self.assertIn("Datenstand", banner)
        self.assertIn(expected_display, banner)
        self.assertIn("gap:5px", banner)

    def test_the_header_uses_truthful_empty_organisation_identity(self):
        """An empty install must not seed the prototype's fictional identity."""
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        ).run()

        # The account trigger is a popover and its truthful menu header is
        # HTML, while its actions are buttons. Search both surfaces so a
        # fictional identity cannot slip into either one.
        rendered = " ".join(node.proto.body for node in app.get("html"))
        rendered += " " + " ".join(b.label or "" for b in app.button)
        self.assertIn("Gemeinsamer Zugang", rendered)
        css = next(
            body for body in app.get("html")
            if ".st-key-app_shell" in body.proto.body
        ).proto.body
        self.assertIn('content: "GZ"', css)
        self.assertIn('content: "▾"', css)
        self.assertIn("color: #a8a8b2", css)
        self.assertIn("font-size: 9px", css)
        self.assertIn("scope-account-menu", css)
        for invented in ("Brunner", "Hochbau"):
            self.assertNotIn(invented, rendered)

if __name__ == "__main__":
    unittest.main()
