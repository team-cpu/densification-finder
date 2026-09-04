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


class OrganisationPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.tempdir.name, "results.sqlite")
        shutil.copy2(paths.SEED_DB, self.database)
        with sqlite3.connect(self.database) as connection:
            ingest.schema(connection)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_schema_bootstraps_empty_truthful_organisation(self):
        profile = organisation.load_profile(self.database)
        self.assertEqual(profile["name"], "")
        self.assertTrue(profile["weekly_digest"])
        self.assertFalse(profile["enforce_2fa"])
        self.assertEqual(organisation.load_members(self.database), [])

    def test_profile_fields_and_toggles_persist(self):
        organisation.update_profile(
            {
                "name": "Beispiel AG",
                "legal_name": "Beispiel Immobilien AG",
                "uid": "CHE-123.456.789",
                "billing_email": "rechnung@beispiel.ch",
                "weekly_digest": False,
                "enforce_2fa": True,
            },
            self.database,
        )
        saved = organisation.load_profile(self.database)
        self.assertEqual(saved["name"], "Beispiel AG")
        self.assertEqual(saved["billing_email"], "rechnung@beispiel.ch")
        self.assertFalse(saved["weekly_digest"])
        self.assertTrue(saved["enforce_2fa"])

    def test_invalid_profile_value_is_rejected_before_write(self):
        with self.assertRaises(ValueError):
            organisation.update_profile(
                {"name": "Should not persist", "billing_email": "invalid"},
                self.database,
            )
        self.assertEqual(organisation.load_profile(self.database)["name"], "")

    def test_unknown_fields_and_non_boolean_toggles_are_rejected(self):
        for values in (
            {"name = ''; DROP TABLE organisation_members; --": "invalid"},
            {"enforce_2fa": "false"},
            {"weekly_digest": 1},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                organisation.update_profile(values, self.database)
        self.assertEqual(organisation.load_members(self.database), [])
        self.assertFalse(organisation.load_profile(self.database)["enforce_2fa"])

    def test_partial_profile_update_keeps_other_fields(self):
        organisation.update_profile({"name": "QA Company"}, self.database)
        organisation.update_profile({"city": "QA City"}, self.database)
        profile = organisation.load_profile(self.database)
        self.assertEqual(profile["name"], "QA Company")
        self.assertEqual(profile["city"], "QA City")

    def test_repeated_schema_keeps_organisation_and_parcel_data(self):
        organisation.update_profile({"name": "QA Company"}, self.database)
        member_id = organisation.invite_member("qa@example.com", db=self.database)
        with sqlite3.connect(self.database) as connection:
            before = connection.execute("SELECT COUNT(*) FROM parcel_results").fetchone()
            ingest.schema(connection)
            ingest.schema(connection)
            after = connection.execute("SELECT COUNT(*) FROM parcel_results").fetchone()
        self.assertEqual(before, after)
        self.assertEqual(organisation.load_profile(self.database)["name"], "QA Company")
        self.assertEqual(organisation.load_members(self.database)[0]["id"], member_id)

    def test_invite_role_resend_and_removal_lifecycle(self):
        member_id = organisation.invite_member(
            "anna.muster@example.ch", "Bearbeiter", db=self.database
        )
        member = organisation.load_members(self.database)[0]
        self.assertEqual(member["id"], member_id)
        self.assertEqual(member["name"], "Anna Muster")
        self.assertTrue(member["pending"])

        self.assertTrue(
            organisation.set_member_role(member_id, "Leseweise", self.database)
        )
        self.assertTrue(organisation.resend_invite(member_id, self.database))
        member = organisation.load_members(self.database)[0]
        self.assertEqual(member["role"], "Leseweise")
        self.assertEqual(member["activity"], "erneut vorgemerkt")
        self.assertFalse(organisation.remove_member(member_id, self.database))

        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE organisation_members SET status = 'active' WHERE id = ?",
                (member_id,),
            )
        self.assertTrue(organisation.remove_member(member_id, self.database))
        self.assertEqual(organisation.load_members(self.database), [])

    def test_duplicate_pending_invite_is_refreshed_not_duplicated(self):
        first = organisation.invite_member("a@example.ch", db=self.database)
        second = organisation.invite_member(
            " A@EXAMPLE.CH ", "Leseweise", db=self.database
        )
        self.assertEqual(first, second)
        members = organisation.load_members(self.database)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["role"], "Leseweise")

    def test_simultaneous_invites_do_not_duplicate_or_raise(self):
        from concurrent.futures import ThreadPoolExecutor

        def invite(_):
            return organisation.invite_member("qa@example.com", db=self.database)

        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(invite, range(2)))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(len(organisation.load_members(self.database)), 1)

    def test_avatar_initials_cannot_escape_css_string(self):
        for name in ('\\" \\"', '<script>alert(1)</script>', 'ä ö', 'QA Company'):
            with self.subTest(name=name):
                organisation.update_profile({"name": name}, self.database)
                self.assertTrue(organisation.account_summary(self.database)["initials"].isalpha())

    def test_account_summary_uses_configured_organisation_without_fake_person(self):
        organisation.update_profile({"name": "Beispiel AG"}, self.database)
        summary = organisation.account_summary(self.database)
        self.assertEqual(summary["label"], "Beispiel AG")
        self.assertEqual(summary["initials"], "BA")
        self.assertEqual(summary["member_count"], 0)


class OrganisationDialogTest(unittest.TestCase):
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

    def _open(self, view="team", timeout=60):
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=timeout
        ).run()
        key = "app_shell_account_settings" if view == "settings" else "app_shell_account_team"
        app.button(key=key).click().run()
        self.assertFalse(app.exception)
        return app

    def _dialog_html(self, app):
        return " ".join(
            node.proto.body
            for node in app.get("html")
            if "scope-org-modal" in node.proto.body
        )

    def test_account_menu_opens_active_team_dialog_and_closes(self):
        app = self._open("team")
        self.assertTrue(app.session_state[organisation.DIALOG_OPEN])
        self.assertIn("scope-org-modal--team", self._dialog_html(app))
        self.assertFalse(app.text_input(key="org_invite_email").disabled)
        self.assertFalse(app.selectbox(key="org_invite_role").disabled)
        self.assertFalse(app.button(key="org_invite_submit").disabled)

        app.button(key="org_dialog_close").click().run()
        self.assertFalse(app.exception)
        self.assertNotIn(organisation.DIALOG_OPEN, app.session_state)

    def test_settings_dialog_exposes_all_editable_fields_and_toggles(self):
        app = self._open("settings")
        for field in organisation.PROFILE_TEXT_FIELDS:
            self.assertFalse(app.text_input(key=f"org_profile_{field}").disabled)
        for field in organisation.PROFILE_BOOLEAN_FIELDS:
            self.assertFalse(app.toggle(key=f"org_profile_{field}").disabled)
        html = " ".join(node.proto.body for node in app.get("html"))
        self.assertIn("Aargau", html)
        self.assertIn("AGIS", html)
        self.assertIn("geodienste.ch", html)
        self.assertIn("Keine aktive 2FA", html)
        self.assertIn("E-Mail-Versand ist noch nicht eingerichtet", html)
        self.assertNotIn("Abo Team", html)
        self.assertIn("st-key-org_modal_content", html)
        self.assertIn('[data-testid="stTextInputRootElement"]', html)
        self.assertIn('[data-testid="stSelectbox"] [role="group"]', html)
        self.assertNotIn("data-baseweb", organisation._DIALOG_CSS)

    def test_settings_save_on_change_and_keep_each_error_until_fixed(self):
        app = self._open("settings")
        app.text_input(key="org_profile_uid").set_value("invalid").run()
        app.text_input(key="org_profile_billing_email").set_value("invalid").run()
        app.text_input(key="org_profile_name").set_value("QA Company").run()
        self.assertEqual(len(app.error), 2)
        self.assertEqual(organisation.load_profile(self.database)["name"], "QA Company")

        app.button(key="org_dialog_close").click().run()
        self.assertTrue(app.session_state[organisation.DIALOG_OPEN])
        self.assertEqual(len(app.error), 2)
        app.text_input(key="org_profile_uid").set_value("CHE-123.456.789").run()
        self.assertEqual(len(app.error), 1)
        app.text_input(key="org_profile_billing_email").set_value("qa@example.com").run()
        self.assertEqual(len(app.error), 0)
        app.button(key="org_dialog_close").click().run()
        self.assertNotIn(organisation.DIALOG_OPEN, app.session_state)

    def test_team_invite_role_and_resend_work_through_widgets(self):
        app = self._open("team")
        app.text_input(key="org_invite_email").set_value("qa.person@example.com")
        app.button(key="org_invite_submit").click().run()
        self.assertFalse(app.exception)
        member = organisation.load_members(self.database)[0]
        member_id = member["id"]
        app.selectbox(key=f"org_member_role_{member_id}").select("Leseweise").run()
        app.button(key=f"org_resend_{member_id}").click().run()
        self.assertFalse(app.exception)
        member = organisation.load_members(self.database)[0]
        self.assertEqual(member["role"], "Leseweise")
        self.assertEqual(member["activity"], "erneut vorgemerkt")
        self.assertIn('[class*="st-key-org_member_row_"]', organisation._DIALOG_CSS)

    def test_no_reference_person_or_company_is_seeded(self):
        app = self._open("team")
        rendered = " ".join(node.proto.body for node in app.get("html"))
        rendered += " " + " ".join(button.label or "" for button in app.button)
        for invented in ("Brunner", "Sutter", "Iten", "Meili", "Hochbau AG"):
            self.assertNotIn(invented, rendered)

    def test_stale_dialog_does_not_overwrite_another_sessions_role_change(self):
        member_id = organisation.invite_member("qa@example.com", db=self.database)
        app = self._open("team")
        self.assertEqual(app.selectbox(key=f"org_member_role_{member_id}").value, "Bearbeiter")
        organisation.set_member_role(member_id, "Leseweise", self.database)
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(organisation.load_members(self.database)[0]["role"], "Leseweise")
        self.assertEqual(app.selectbox(key=f"org_member_role_{member_id}").value, "Leseweise")


if __name__ == "__main__":
    unittest.main()
