import os
import shutil
import sqlite3
import tempfile
import unittest

import streamlit as st
from streamlit.testing.v1 import AppTest

import ingest
import organisation
import paths

#: The prototype's own fictional roster, and its domain — see the module
#: docstring in `organisation.py` for why none of it may appear rendered.
_INVENTED_NAMES = ("Brunner", "Sutter", "Iten", "Meili")
_INVENTED_DOMAIN = "hochbau-ag.ch"


class OrganisationDialogTest(unittest.TestCase):
    """The organisation screen behind the header's account chip. Every test
    here goes through the real entry point — clicking the chip on a fully
    rendered `app.py` — rather than calling `organisation.render` directly,
    because the thing this screen has to get right (a dialog that actually
    opens and closes, on the real header) only shows up end to end."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.tempdir.name, "results.sqlite")
        shutil.copy2(paths.SEED_DB, self.database)
        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM oereb_cache")
            # See test_app.py / test_shell.py: the committed fixture predates
            # columns later work added to `parcel_workflow`, and only ever
            # gets to current schema via `app.py`'s own bootstrap on a real
            # database.
            ingest.schema(connection)

        self.original_database = paths.DB
        paths.DB = self.database
        st.cache_data.clear()

    def tearDown(self):
        paths.DB = self.original_database
        self.tempdir.cleanup()

    def _open(self, timeout=60):
        """The app with the organisation dialog open, reached the only way a
        real user could: through the header's account chip."""
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=timeout
        ).run()
        self.assertFalse(app.exception)
        app.button(key="app_shell_account_open").click().run()
        self.assertFalse(app.exception)
        return app

    def _preview_widgets(self, app):
        """Every widget the preview itself renders — found by the
        `org_field_` prefix `organisation.py` gives each one, precisely so
        this can reach exactly the mocked product's own controls and not
        `org_dialog_close`, which has to stay real or the dialog this test
        opened could never be closed again."""
        widgets = []
        for kind in (app.text_input, app.selectbox, app.button, app.toggle):
            widgets.extend(
                w for w in kind if str(w.key or "").startswith("org_field_")
            )
        return widgets

    def test_the_dialog_opens_from_the_header_chip_and_closes_again(self):
        app = self._open()
        self.assertTrue(app.session_state[organisation.DIALOG_OPEN])
        # The dialog only exists once it is open — same guarantee
        # test_app.py's `test_a_closed_board_carries_no_contact_form_widgets`
        # makes for the acquisition board's contact dialog.
        self.assertGreater(len(self._preview_widgets(app)), 0)

        app.button(key="org_dialog_close").click().run()
        self.assertFalse(app.exception)
        self.assertNotIn(organisation.DIALOG_OPEN, app.session_state)
        self.assertEqual(len(self._preview_widgets(app)), 0)

    def test_every_control_the_preview_renders_is_disabled(self):
        """The point of the whole screen: a toggle the user can flip that
        changes nothing teaches them a lie about what this tool does. This
        is the regression test for a single missed `disabled=True` — it
        enumerates every input, selectbox, button and toggle the preview
        draws (3 in the invite form, 4 settings toggles) and fails loudly
        if even one of them is left interactive."""
        app = self._open()
        widgets = self._preview_widgets(app)
        self.assertEqual(len(widgets), 7)
        for widget in widgets:
            self.assertTrue(widget.disabled, f"{widget.key} is not disabled")

    def test_no_invented_person_or_domain_appears(self):
        """Same guarantee `test_shell.py::test_the_header_names_no_person`
        makes for the header chip: the prototype's fictional roster —
        Brunner, Sutter, Iten, Meili at hochbau-ag.ch — must not survive
        into a screen this application actually ships, or it would imply a
        member system that does not exist."""
        app = self._open()

        rendered = []
        for kind in (app.markdown, app.caption, app.info, app.warning):
            rendered.extend(el.value for el in kind)
        for widget in app.text_input:
            rendered.append(widget.label or "")
            rendered.append(widget.placeholder or "")
        for widget in app.selectbox:
            rendered.append(widget.label or "")
            rendered.extend(widget.options)
        for widget in app.button:
            rendered.append(widget.label or "")
        for widget in app.toggle:
            rendered.append(widget.label or "")
        text = " ".join(rendered)

        for invented in _INVENTED_NAMES:
            self.assertNotIn(invented, text)
        self.assertNotIn(_INVENTED_DOMAIN, text)

    def test_the_preview_banner_is_present(self):
        app = self._open()
        banners = " ".join(el.value for el in app.warning)
        self.assertIn("Vorschau", banners)
        self.assertIn("gemeinsames Passwort", banners)

    def test_the_datenlizenz_row_names_the_real_data_sources(self):
        """The prototype's row shows a Zurich cadastral subscription
        expiring 31.12.2026 and a seat count ("3 von 6 Lizenzen belegt").
        Neither is true here — this application has no subscription and no
        seats — so the row must name the real provenance (README.md's data
        sources: Canton Aargau via AGIS and geodienste.ch) instead of
        carrying either fiction across."""
        app = self._open()
        text = " ".join(el.value for el in app.markdown)

        self.assertIn("Aargau", text)
        self.assertIn("AGIS", text)
        self.assertIn("geodienste.ch", text)
        self.assertNotIn("Lizenzen belegt", text)
        self.assertNotIn("Abo Team", text)
        self.assertNotIn("31.12.2026", text)
        self.assertNotIn("Zürich", text)


if __name__ == "__main__":
    unittest.main()
