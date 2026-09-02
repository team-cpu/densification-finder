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
                any("Areal" in body for body in bodies),
                f"wordmark missing on {page}",
            )

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
        self.assertTrue(
            any(f"Datenstand {expected_display}" in body for body in bodies)
        )


    def test_the_header_names_no_person(self):
        """The prototype's chip shows "M. Brunner · Hochbau AG" because it
        mocks up a multi-user product. This application has one shared
        password and no accounts, so a name there would belong to nobody and
        would imply a login that does not exist."""
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        ).run()

        # The chip used to be inert `st.html` markup; it is now a real
        # `st.button` that opens `organisation.py`'s preview dialog (see
        # `shell._account_chip`), so its label lives in `app.button`, not
        # `app.get("html")`, and both have to be searched for an invented
        # name to keep meaning what this test's docstring says it means.
        rendered = " ".join(node.proto.body for node in app.get("html"))
        rendered += " " + " ".join(b.label or "" for b in app.button)
        self.assertIn("Gemeinsamer Zugang", rendered)
        for invented in ("Brunner", "Hochbau"):
            self.assertNotIn(invented, rendered)

if __name__ == "__main__":
    unittest.main()
