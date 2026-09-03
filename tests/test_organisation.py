import os
import re
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

    def _open(self, view="team", timeout=60):
        """Open one organisation view through the account menu."""
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=timeout
        ).run()
        self.assertFalse(app.exception)
        self.assertNotIn(organisation.DIALOG_OPEN, app.session_state)
        key = (
            "app_shell_account_settings"
            if view == "settings"
            else "app_shell_account_team"
        )
        app.button(key=key).click().run()
        self.assertFalse(app.exception)
        return app

    def _dialog_html(self, app):
        """The modal is intentionally one sanitised HTML surface so its
        dimensions and grid match the export instead of inheriting the
        larger default Streamlit form spacing."""
        return " ".join(
            node.proto.body
            for node in app.get("html")
            if "scope-org-modal" in node.proto.body
        )

    def _preview_controls(self, app):
        return re.findall(
            r"<(?:input|select|button)\b(?=[^>]*data-org-field=)[^>]*>",
            self._dialog_html(app),
        )

    def test_account_control_exposes_menu_before_any_modal(self):
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        ).run()

        self.assertFalse(app.exception)
        self.assertNotIn(organisation.DIALOG_OPEN, app.session_state)
        self.assertIsNotNone(app.button(key="app_shell_account_team"))
        self.assertIsNotNone(app.button(key="app_shell_account_settings"))
        self.assertIsNotNone(app.button(key="app_shell_account_logout"))
        menu = " ".join(node.proto.body for node in app.get("html"))
        self.assertIn("scope-account-menu", menu)
        self.assertIn("Keine Benutzerkonten aktiv", menu)

    def test_the_menu_opens_the_selected_dialog_and_closes_again(self):
        app = self._open()
        self.assertTrue(app.session_state[organisation.DIALOG_OPEN])
        self.assertEqual(app.session_state[organisation.DIALOG_VIEW], "team")
        # The dialog only exists once it is open — same guarantee
        # test_app.py's `test_a_closed_board_carries_no_contact_form_widgets`
        # makes for the acquisition board's contact dialog.
        self.assertIn("scope-org-modal--team", self._dialog_html(app))

        app.button(key="org_dialog_close").click().run()
        self.assertFalse(app.exception)
        self.assertNotIn(organisation.DIALOG_OPEN, app.session_state)
        self.assertNotIn(organisation.DIALOG_VIEW, app.session_state)
        self.assertNotIn("scope-org-modal", self._dialog_html(app))

    def test_every_control_the_preview_renders_is_disabled(self):
        """The point of the whole screen: a toggle the user can flip that
        changes nothing teaches them a lie about what this tool does. This
        is the regression test for a single missed `disabled=True` — it
        enumerates every input, selectbox, button and toggle the preview
        draws (3 in the invite form, 7 company fields and 4 settings toggles)
        and fails loudly
        if even one of them is left interactive."""
        for view, expected in (("team", 3), ("settings", 11)):
            with self.subTest(view=view):
                app = self._open(view)
                controls = self._preview_controls(app)
                self.assertEqual(len(controls), expected)
                for control in controls:
                    self.assertRegex(control, r"\sdisabled(?:\s|>)")

    def test_no_invented_person_or_domain_appears(self):
        """Same guarantee `test_shell.py::test_the_header_names_no_person`
        makes for the header chip: the prototype's fictional roster —
        Brunner, Sutter, Iten, Meili at hochbau-ag.ch — must not survive
        into a screen this application actually ships, or it would imply a
        member system that does not exist."""
        for view in ("team", "settings"):
            app = self._open(view)

            text = self._dialog_html(app)
            text += " " + " ".join(button.label or "" for button in app.button)

            for invented in _INVENTED_NAMES:
                self.assertNotIn(invented, text)
            self.assertNotIn(_INVENTED_DOMAIN, text)

    def test_the_preview_disclosure_is_present(self):
        app = self._open()
        dialog = self._dialog_html(app)
        self.assertIn("Vorschau", dialog)
        self.assertIn("gemeinsames Passwort", dialog)

    def test_the_datenlizenz_row_names_the_real_data_sources(self):
        """The prototype's row shows a Zurich cadastral subscription
        expiring 31.12.2026 and a seat count ("3 von 6 Lizenzen belegt").
        Neither is true here — this application has no subscription and no
        seats — so the row must name the real provenance (README.md's data
        sources: Canton Aargau via AGIS and geodienste.ch) instead of
        carrying either fiction across."""
        app = self._open("settings")
        text = self._dialog_html(app)

        self.assertIn("Aargau", text)
        self.assertIn("AGIS", text)
        self.assertIn("geodienste.ch", text)
        self.assertNotIn("Lizenzen belegt", text)
        self.assertNotIn("Abo Team", text)
        self.assertNotIn("31.12.2026", text)
        self.assertNotIn("Zürich", text)


if __name__ == "__main__":
    unittest.main()
