import functools
import json
import os
import shutil
import sqlite3
import tempfile
import unittest

from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

import acquisition
import bootstrap
import ingest
import merkliste
import navigation
import oereb
import paths
import screening
import scope_auth
import searches
import workflow


def field(app, label):
    """Contact-form inputs carry no widget key, only a label — this is how
    the acquisition-board tests address one instead of relying on the
    form's field order."""
    return next(w for w in app.text_input if w.label == label)


def area(app, label):
    """The same, for the two fields that are text areas: Adresse and Notizen
    are blocks of text that grow, not one-liners."""
    return next(w for w in app.text_area if w.label == label)


class StartupDatabaseLockTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = os.path.join(directory.name, "results.sqlite")
        shutil.copy2(paths.SEED_DB, self.database)
        connection = sqlite3.connect(self.database)
        try:
            ingest.schema(connection)
        finally:
            connection.close()
        self.enterContext(patch.object(paths, "DB", self.database))
        self.enterContext(patch.dict(os.environ, {
            "SCOPE_AUTH_MODE": "shared", "APP_PASSWORD": "", "DENSIFICATION_RESEED": "0",
        }))
        st.cache_data.clear()
        self.addCleanup(st.cache_data.clear)
        connect = sqlite3.connect
        self.holder = connect(self.database, check_same_thread=False)
        self.addCleanup(self.holder.close)
        self.enterContext(patch.object(sqlite3, "connect", functools.partial(connect, timeout=0.1)))

    def app(self):
        return AppTest.from_file(os.path.join(paths.HERE, "app.py"), default_timeout=60)

    def assert_blocked(self, app):
        self.assertFalse(app.exception)
        self.assertTrue(any("gesperrt" in w.value for w in app.warning))
        self.assertFalse(app.dataframe)
        self.assertFalse(app.segmented_control)
        self.assertEqual(app.button(key="database_retry").label, "Erneut versuchen")

    def retry(self, app):
        self.holder.rollback()
        app.button(key="database_retry").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.dataframe)
        self.assertFalse(any("gesperrt" in w.value for w in app.warning))

    def test_current_shared_page_loads_while_another_writer_holds_database(self):
        self.holder.execute("BEGIN IMMEDIATE")
        self.holder.execute("UPDATE organisation_profile SET name='Uncommitted'")
        app = self.app().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.dataframe)
        self.assertTrue(self.holder.in_transaction)

    def test_exclusive_lock_stops_before_private_rendering_and_retry_recovers(self):
        self.holder.execute("BEGIN EXCLUSIVE")
        app = self.app().run()
        self.assert_blocked(app)
        self.retry(app)

    def test_shared_cache_table_lock_is_retryable(self):
        connect = sqlite3.connect

        def shared_connect(database, **kwargs):
            if database == self.database:
                return connect(f"file:{database}?cache=shared", uri=True, **kwargs)
            return connect(database, **kwargs)

        self.holder.close()
        self.enterContext(patch.object(sqlite3, "connect", shared_connect))
        self.holder = sqlite3.connect(self.database)
        self.addCleanup(self.holder.close)
        self.holder.execute("BEGIN IMMEDIATE")
        self.holder.execute("UPDATE organisation_profile SET name='Uncommitted'")
        # Shared-cache table contention uses SQLITE_LOCKED_SHAREDCACHE (262),
        # rather than SQLITE_BUSY (5) from an ordinary second writer.
        reader = sqlite3.connect(self.database)
        try:
            with self.assertRaises(sqlite3.OperationalError) as caught:
                reader.execute("SELECT * FROM organisation_profile")
            self.assertEqual(caught.exception.sqlite_errorcode, sqlite3.SQLITE_LOCKED_SHAREDCACHE)
        finally:
            reader.close()
        app = self.app().run()
        self.assert_blocked(app)
        self.retry(app)

    def test_pending_migration_is_retried_without_resetting_other_preferences(self):
        self.holder.execute("DELETE FROM schema_migrations WHERE name='mail_switches_opt_in'")
        self.holder.execute("UPDATE organisation_profile SET weekly_digest=1, "
                            "due_reminders=1, shared_calculations=1, name='Preserve me'")
        self.holder.commit()
        self.holder.execute("BEGIN IMMEDIATE")
        app = self.app().run()
        self.assert_blocked(app)
        self.assertIsNone(self.holder.execute("SELECT 1 FROM schema_migrations "
                                             "WHERE name='mail_switches_opt_in'").fetchone())
        self.retry(app)
        self.assertEqual(self.holder.execute("SELECT name,weekly_digest,due_reminders,shared_calculations "
                                            "FROM organisation_profile").fetchone(), ("Preserve me", 0, 0, 1))
        self.holder.execute("UPDATE organisation_profile SET weekly_digest=1")
        self.holder.commit()
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(self.holder.execute("SELECT weekly_digest FROM organisation_profile").fetchone(), (1,))

    def test_pandas_read_lock_after_schema_check_is_retryable(self):
        prepare = bootstrap.prepare_database

        def lock_after_preparing():
            result = prepare()
            self.holder.execute("BEGIN EXCLUSIVE")
            return result

        with patch.object(bootstrap, "prepare_database", lock_after_preparing):
            app = self.app().run()
        self.assert_blocked(app)
        self.retry(app)

    def test_personal_startup_lock_keeps_session_and_rechecks_access_on_retry(self):
        self.enterContext(patch.dict(os.environ, {
            "SCOPE_AUTH_MODE": "personal", "SCOPE_OWNER_EMAIL": "owner@example.com",
            "SCOPE_SUPABASE_URL": "https://scope.supabase.co", "SCOPE_SUPABASE_ANON_KEY": "test-key",
        }))
        identity = {"email": "owner@example.com", "id": "test-owner", "email_confirmed_at": "2026-09-01"}
        scope_auth.bootstrap_owner(self.database)
        scope_auth.verified_member(identity, self.database, bind=True)
        provider = self.enterContext(patch.object(scope_auth, "api", return_value=identity))
        self.holder.execute("BEGIN IMMEDIATE")
        app = self.app()
        app.session_state["scope_access_token"] = "test-session-token"
        app.run()
        self.assert_blocked(app)
        self.assertEqual(app.session_state["scope_access_token"], "test-session-token")
        provider.assert_not_called()
        # Revoking access while blocked must still take effect after retry.
        self.holder.execute("DELETE FROM scope_access")
        self.holder.commit()
        app.button(key="database_retry").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.dataframe)
        self.assertNotIn("scope_access_token", app.session_state)
        self.assertTrue(any(w.label == "E-Mail-Adresse" for w in app.text_input))

    def test_personal_session_recovers_after_lock_without_logging_in_again(self):
        self.enterContext(patch.dict(os.environ, {
            "SCOPE_AUTH_MODE": "personal", "SCOPE_OWNER_EMAIL": "owner@example.com",
            "SCOPE_SUPABASE_URL": "https://scope.supabase.co", "SCOPE_SUPABASE_ANON_KEY": "test-key",
        }))
        identity = {"email": "owner@example.com", "id": "test-owner", "email_confirmed_at": "2026-09-01"}
        scope_auth.bootstrap_owner(self.database)
        scope_auth.verified_member(identity, self.database, bind=True)
        self.enterContext(patch.object(scope_auth, "api", return_value=identity))
        self.holder.execute("BEGIN IMMEDIATE")
        app = self.app()
        app.session_state["scope_access_token"] = "test-session-token"
        app.run()
        self.assert_blocked(app)
        self.retry(app)
        self.assertEqual(app.session_state["scope_access_token"], "test-session-token")

    def test_locked_database_does_not_run_before_shared_password_gate(self):
        self.enterContext(patch.dict(os.environ, {"APP_PASSWORD": "test-password"}))
        self.holder.execute("BEGIN EXCLUSIVE")
        app = self.app().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.dataframe)
        self.assertFalse(app.warning)
        self.assertTrue(any(w.label == "Passwort" for w in app.text_input))

    def test_non_lock_database_error_is_not_disguised_as_retryable(self):
        self.holder.executescript("DROP TABLE schema_migrations; "
                                 "CREATE VIEW schema_migrations AS SELECT 'different_migration' AS name;")
        app = self.app().run()
        self.assertTrue(app.exception)
        self.assertIn("view", app.exception[0].message)
        self.assertFalse(any("gesperrt" in w.value for w in app.warning))
        self.assertFalse(app.dataframe)


class AppRegressionTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.tempdir.name, "results.sqlite")
        shutil.copy2(paths.SEED_DB, self.database)
        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM oereb_cache")
            # The committed fixture predates fields later tasks added to
            # `parcel_workflow` (e.g. `due_date`, `next_step`) — on disk it
            # only ever gets to current schema via `app.py`'s own bootstrap.
            # A test that calls `workflow.update()` before the app has run
            # once needs that widening done here, the same way it would
            # already be done on any database a real deploy has touched.
            ingest.schema(connection)

        self.original_database = paths.DB
        paths.DB = self.database
        # `load()` is cached with no arguments, so pointing `paths.DB` elsewhere
        # does not change its key.
        st.cache_data.clear()

    def tearDown(self):
        paths.DB = self.original_database
        self.tempdir.cleanup()

    def screening(self, timeout=60):
        """The app with the Screening page showing, which is its default."""
        return AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=timeout
        ).run()

    def test_empty_screening_keeps_saved_search_and_restore_controls(self):
        first = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 1",
            sqlite3.connect(self.database),
        ).iloc[0]
        workflow.set_hidden([(int(first.bfs), str(first.parcel))], True, self.database)
        app = self.screening()
        field(app, "Parzellen-Nr. suchen").set_value("999999999999").run()
        self.assertFalse(app.exception)
        self.assertTrue(any(w.label == "Name der Suche" for w in app.text_input))
        self.assertEqual(len(app.get("component_instance")), 1)
        field(app, "Name der Suche").set_value("Empty audit search").run()
        next(w for w in app.button if w.label == "Speichern").click().run()
        self.assertFalse(app.exception)
        self.assertIn("Empty audit search", searches.load(self.database)["name"].tolist())

    def test_empty_saved_pages_keep_their_components(self):
        app = self.screening()
        for page in ("Merkliste", "Akquisition"):
            app.segmented_control(key="acq_page").set_value(page).run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get("component_instance")), 1)

    def saved_leads(self):
        with sqlite3.connect(self.database) as connection:
            parcels = pd.read_sql_query("SELECT * FROM parcel_results", connection)
        return acquisition.leads(parcels, workflow.load(self.database), "saved")

    def test_controls_mixed_results_and_economic_indicator(self):
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=30
        ).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.number_input[0].label, "Min. Potenzial m²")
        self.assertEqual(app.number_input[0].min, 130)
        area_min = next(n for n in app.number_input if n.label == "Fläche von (m²)")
        area_max = next(n for n in app.number_input if n.label == "Fläche bis (m²)")
        self.assertEqual(area_min.value, 300)
        self.assertIsNone(area_max.value)
        result_limit = next(s for s in app.selectbox if s.label == "Anzeigen")
        self.assertEqual(result_limit.value, 50)
        self.assertEqual(app.number_input[1].label, "Mind. AZ")
        self.assertIsNone(app.number_input[1].value)

        # Addressed by label: the filter row's selectbox order shifts whenever
        # a control changes shape (Gemeinde became one of them).
        parcel_type = next(s for s in app.selectbox if s.label == "Objekttyp")
        parcel_type.select("Alle").run()
        self.assertFalse(app.exception)
        frame = app.dataframe[0].value
        self.assertEqual(len(frame), 50)
        self.assertTrue(frame["Potenzial m² (Schätzung)"].is_monotonic_decreasing)
        self.assertIn("≈ Landwert / Potenzial-GF", frame.columns)
        self.assertTrue(frame["Preisebene"].eq("Kanton AG").all())
        self.assertTrue(frame["Preisstand"].eq("2021 Q2").all())
        self.assertTrue(frame["≈ Landwert / Potenzial-GF"].notna().all())
        self.assertIn("Merkliste", frame.columns)
        self.assertIn("Kontaktstatus", frame.columns)

        result_limit.select(50).run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.dataframe[0].value), 50)

    def test_area_filter_reaches_past_the_old_fixed_window(self):
        """Both ends of the area control used to be walls rather than the end of
        the data: the cascade stored 300–5,000 m² and the slider offered exactly
        that, so parcels outside it could not be reached at any setting. This is
        the regression that the widening exists to prevent."""
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=30
        ).run()
        # Addressed by label: the filter row's selectbox order shifts whenever
        # a control changes shape (Gemeinde became one of them).
        parcel_type = next(s for s in app.selectbox if s.label == "Objekttyp")
        parcel_type.select("Alle").run()

        area_min = next(n for n in app.number_input if n.label == "Fläche von (m²)")
        area_min.set_value(5000).run()
        self.assertFalse(app.exception)
        large = app.dataframe[0].value
        self.assertTrue(len(large) > 0)
        self.assertTrue((large["Fläche m²"] >= 5000).all())

        area_min = next(n for n in app.number_input if n.label == "Fläche von (m²)")
        area_min.set_value(0).run()
        area_max = next(n for n in app.number_input if n.label == "Fläche bis (m²)")
        area_max.set_value(300).run()
        self.assertFalse(app.exception)
        small = app.dataframe[0].value
        self.assertTrue(len(small) > 0)
        self.assertTrue((small["Fläche m²"] <= 300).all())

    def test_open_upper_end_surfaces_the_largest_lead(self):
        """The parcel with the most potential in the canton — Rheinfelden 574,
        199,442 m² of Wohnzone B — was invisible under the 5,000 m² cap."""
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=30
        ).run()
        # Addressed by label: the filter row's selectbox order shifts whenever
        # a control changes shape (Gemeinde became one of them).
        parcel_type = next(s for s in app.selectbox if s.label == "Objekttyp")
        parcel_type.select("Unbebaut").run()
        self.assertFalse(app.exception)
        top = app.dataframe[0].value.iloc[0]
        self.assertEqual(top["Gemeinde"], "Rheinfelden")
        self.assertEqual(top["Parzelle"], "574")
        self.assertGreater(top["Potenzial m² (Schätzung)"], 100_000)

    def test_ziffer_filters_to_the_typed_exact_value(self):
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=30
        ).run()

        # The filter regroup put Ziffer second, ahead of Anzahl Resultate.
        app.number_input[1].set_value(0.8).run()

        self.assertFalse(app.exception)
        frame = app.dataframe[0].value
        self.assertGreater(len(frame), 0)
        self.assertTrue(frame["Ziffer"].round(3).eq(0.8).all())

    def test_transport_filter_is_on_by_default_and_hides_confirmed_parcels(self):
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=30
        ).run()
        first = app.dataframe[0].value.iloc[0]
        second = app.dataframe[0].value.iloc[1]
        transport_filter = next(
            checkbox
            for checkbox in app.checkbox
            if checkbox.label == "Strassen-/Bahnparzellen"
        )
        self.assertTrue(transport_filter.value)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE parcel_results SET transport_share = 0.95 "
                "WHERE municipality = ? AND parcel = ?",
                (first["Gemeinde"], str(first["Parzelle"])),
            )
            connection.execute(
                "UPDATE parcel_results SET transport_share = NULL "
                "WHERE municipality = ? AND parcel = ?",
                (second["Gemeinde"], str(second["Parzelle"])),
            )
        st.cache_data.clear()

        app.run()
        visible = app.dataframe[0].value
        match = (
            (visible["Gemeinde"] == first["Gemeinde"])
            & (visible["Parzelle"].astype(str) == str(first["Parzelle"]))
        )
        self.assertFalse(match.any())

        # transport_share NULL means unclassified, not a confirmed road: the
        # conservative rule keeps those rows visible even with the filter on.
        null_match = (
            (visible["Gemeinde"] == second["Gemeinde"])
            & (visible["Parzelle"].astype(str) == str(second["Parzelle"]))
        )
        self.assertTrue(null_match.any())

    def test_saved_contact_state_is_shown_and_hidden_leads_leave_the_hotlist(self):
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=30
        ).run()
        first = app.dataframe[0].value.iloc[0]
        # The rendered frame has a display index, so resolve the cadastral key
        # from the same ranked source columns instead of relying on that index.
        with sqlite3.connect(self.database) as connection:
            parcel = connection.execute(
                "SELECT bfs, parcel FROM parcel_results "
                "WHERE municipality = ? AND parcel = ? LIMIT 1",
                (first["Gemeinde"], str(first["Parzelle"])),
            ).fetchone()
        self.assertIsNotNone(parcel)
        key = (int(parcel[0]), str(parcel[1]))

        workflow.update(
            [key], saved=True, contact_status="contacted", db=self.database
        )
        app.run()
        self.assertFalse(app.exception)
        shown = app.dataframe[0].value
        match = shown[
            (shown["Gemeinde"] == first["Gemeinde"])
            & (shown["Parzelle"].astype(str) == str(first["Parzelle"]))
        ]
        self.assertEqual(match.iloc[0]["Merkliste"], "Gespeichert")
        self.assertEqual(match.iloc[0]["Kontaktstatus"], "Brief versandt")

        workflow.set_hidden([key], True, self.database)
        app.run()
        self.assertFalse(app.exception)
        visible = app.dataframe[0].value
        self.assertFalse(
            (
                (visible["Gemeinde"] == first["Gemeinde"])
                & (visible["Parzelle"].astype(str) == str(first["Parzelle"]))
            ).any()
        )

    def test_the_parcel_search_narrows_the_table(self):
        app = self.screening()
        full = len(app.dataframe[0].value)
        target = str(app.dataframe[0].value.iloc[0]["Parzelle"])

        field(app, "Parzellen-Nr. suchen").set_value(target).run()

        self.assertFalse(app.exception)
        narrowed = app.dataframe[0].value
        self.assertLess(len(narrowed), full)
        self.assertTrue((narrowed["Parzelle"].astype(str) == target).any())

    def test_the_search_also_matches_an_address(self):
        """Philipp knows the street more often than the parcel number."""
        app = self.screening()
        address = str(app.dataframe[0].value.iloc[0]["Adresse"])
        if address == "—":
            self.skipTest("first row has no address in this fixture")

        field(app, "Parzellen-Nr. suchen").set_value(address[:6]).run()

        self.assertFalse(app.exception)
        self.assertTrue(len(app.dataframe[0].value) >= 1)

    def test_the_summary_line_agrees_with_the_table(self):
        app = self.screening()
        shown = app.dataframe[0].value
        text = " ".join(element.value for element in app.markdown)

        self.assertIn(str(len(shown)), text)

    def test_reset_restores_the_defaults(self):
        """The search field is the assertion that can actually fail. Every
        other control is created *after* the reset button, so the `st.rerun()`
        orphans its widget state whether or not the handler cleared it — those
        controls come back to their defaults even with an empty clear-list, and
        asserting on them proves nothing. The search box is rendered before the
        button, so it survives the rerun and only the handler can empty it."""
        app = self.screening()
        field(app, "Parzellen-Nr. suchen").set_value("Seestrasse").run()
        app.number_input[0].set_value(2000).run()
        self.assertEqual(field(app, "Parzellen-Nr. suchen").value, "Seestrasse")
        self.assertNotEqual(app.number_input[0].value, 130)

        app.button(key="screening_reset").click().run()

        self.assertFalse(app.exception)
        self.assertEqual(field(app, "Parzellen-Nr. suchen").value, "")
        self.assertEqual(app.number_input[0].value, 130)

    def test_saving_and_applying_a_search_restores_the_filter(self):
        """The test that matters: applying a saved search writes straight
        into widget-keyed session_state values (screening_min_delta and the
        rest), and the "Anwenden" button that triggers it is rendered well
        after those widgets are instantiated. Writing to one of those keys
        after its widget already exists this run raises
        StreamlitAPIException — `_apply_pending_search` parks the request
        instead, exactly as `navigation.reconcile` does for page jumps. Set a
        filter away from its default, save, reset, apply, and check the value
        survives the whole round trip."""
        app = self.screening()

        def button(label):
            return next(b for b in app.button if b.label == label)

        app.number_input[0].set_value(2000).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.number_input[0].value, 2000)

        field(app, "Name der Suche").set_value("Testsuche").run()
        button("Speichern").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(list(searches.load(self.database)["name"]), ["Testsuche"])

        button("Zurücksetzen").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.number_input[0].value, 130)

        button("Anwenden").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.number_input[0].value, 2000)

    def test_only_aargau_can_be_chosen(self):
        """The dataset is Aargau. Omitting the others implies they were never
        planned; offering them as working options returns an empty list that
        reads as a fault in the app rather than as the end of the data."""
        app = self.screening()

        canton = next(w for w in app.selectbox if w.label == "Kanton")
        self.assertEqual(canton.value, "Aargau")
        self.assertTrue(len(app.dataframe[0].value) > 0)
        self.assertTrue(
            any("noch nicht verfügbar" in str(option) for option in canton.options),
            "the unavailable cantons are not named",
        )

    def test_the_gemeinde_filter_picks_one_municipality(self):
        """The design's Gemeinde control is a single select whose first entry
        is "Alle Gemeinden" — not a multiselect whose chips outgrow the 30px
        field the row is built around. Picking one municipality has to narrow
        the table to it and leave every other filter alone."""
        app = self.screening()

        gemeinde = next(w for w in app.selectbox if w.label == "Gemeinde")
        self.assertEqual(gemeinde.value, screening.ALL_MUNICIPALITIES)
        self.assertEqual(gemeinde.options[0], screening.ALL_MUNICIPALITIES)

        shown = app.dataframe[0].value
        municipality = str(shown["Gemeinde"].iloc[0])
        app = gemeinde.set_value(municipality).run()

        self.assertFalse(app.exception)
        narrowed = app.dataframe[0].value
        self.assertTrue(len(narrowed) > 0)
        self.assertEqual(set(narrowed["Gemeinde"]), {municipality})
        self.assertTrue(len(narrowed) <= len(shown))

    def test_zuruecksetzen_clears_the_chosen_municipality(self):
        """Zurücksetzen has to put the Gemeinde control back to "Alle
        Gemeinden", not just drop the filter behind it. Passing an explicit
        `index` to this keyed selectbox left the browser showing the old
        municipality while the results were already unfiltered — a control
        that lies about what it is filtering."""
        app = self.screening()

        gemeinde = next(w for w in app.selectbox if w.label == "Gemeinde")
        municipality = str(app.dataframe[0].value["Gemeinde"].iloc[0])
        app = gemeinde.set_value(municipality).run()
        self.assertEqual(
            next(w for w in app.selectbox if w.label == "Gemeinde").value,
            municipality,
        )

        app = next(b for b in app.button if b.label == "Zurücksetzen").click().run()

        self.assertFalse(app.exception)
        for label, key in (
            ("Kanton", "screening_canton"),
            ("Gemeinde", "screening_municipality"),
            ("Anzeigen", "screening_top_n"),
        ):
            self.assertEqual(
                next(w for w in app.selectbox if w.label == label).value,
                screening.RESET_DEFAULTS[key],
                f"{label} did not return to its default",
            )
        self.assertTrue(
            len(set(app.dataframe[0].value["Gemeinde"])) > 1,
            "the reset table is still limited to one municipality",
        )

    def test_a_search_saved_as_a_list_of_municipalities_still_applies(self):
        """Searches stored before the single select kept a list. Reading one
        back must not hand the selectbox a list it cannot show — keep the
        first municipality the data still has, and say what was dropped."""
        parcels = pd.DataFrame({"municipality": ["Aarau", "Brugg"], "az": [0.5, 0.6]})

        values, skipped = screening._valid_search_values(
            parcels, {"screening_municipality": ["Brugg", "Weggezogen"]}
        )
        self.assertEqual(values["screening_municipality"], "Brugg")
        self.assertEqual(skipped, ["Gemeinde"])

        values, skipped = screening._valid_search_values(
            parcels, {"screening_municipality": screening.ALL_MUNICIPALITIES}
        )
        self.assertEqual(
            values["screening_municipality"], screening.ALL_MUNICIPALITIES
        )
        self.assertEqual(skipped, [])

    def test_the_csv_export_carries_the_shown_rows(self):
        import io
        from unittest.mock import patch

        from streamlit.runtime.memory_media_file_storage import (
            MemoryMediaFileStorage,
        )

        # This Streamlit version puts a download button's bytes in the
        # in-memory media store and leaves only a content-addressed `url` on
        # the button's own proto — there is no `.proto.data` to read. The
        # store itself is torn down the moment `.run()` returns, so the
        # bytes have to be caught as they go in, keyed by the same file id
        # the button's url exposes afterwards.
        captured = {}
        original = MemoryMediaFileStorage.load_and_get_id

        def spy(self, path_or_data, mimetype, kind, filename=None):
            file_id = original(self, path_or_data, mimetype, kind, filename)
            captured[file_id] = path_or_data
            return file_id

        with patch.object(MemoryMediaFileStorage, "load_and_get_id", spy):
            app = self.screening()

        shown = app.dataframe[0].value
        exported = list(app.get("download_button"))
        self.assertTrue(exported, "no CSV export on the screening page")
        file_id = os.path.splitext(os.path.basename(exported[0].proto.url))[0]
        payload = captured[file_id]
        frame = pd.read_csv(io.BytesIO(payload))
        self.assertEqual(len(frame), len(shown))
        self.assertIn("Parzelle", frame.columns)

    def test_a_failed_cadastre_call_is_retried_rather_than_cached_forever(self):
        """A transient 502 used to count as an answer: the parcel stayed
        unchecked and the interface still called the shortlist complete."""
        import screening

        cache = pd.DataFrame(
            {"details": ["", "{}"], "error": ["HTTP Error 502: Bad Gateway", ""]},
            index=pd.Index(["CH_FAILED", "CH_OK"], name="egrid"),
        )
        self.assertEqual(list(screening.with_extract(cache)), ["CH_OK"])
        self.assertEqual(list(screening.failed_egrids(cache)), ["CH_FAILED"])

    def test_a_legacy_row_without_the_extract_is_asked_again(self):
        """Rows written before the legal basis was stored carry restrictions but
        no documents, and must not count as complete."""
        import screening

        cache = pd.DataFrame(
            {"details": [None], "error": [None]},
            index=pd.Index(["CH_OLD"], name="egrid"),
        )
        self.assertEqual(list(screening.with_extract(cache)), [])
        self.assertEqual(list(screening.failed_egrids(cache)), [])

    def test_an_extract_stored_before_the_legal_status_was_kept_is_asked_again(self):
        """Once: a fresh answer carries the marker, with or without a status."""
        import screening

        cache = pd.DataFrame(
            {"details": ['{"provisions": []}', '{"provisions": [], "lawstatus_kept": true}', ""],
             "error": ["", "", "HTTP Error 502: Bad Gateway"]},
            index=pd.Index(["CH_OLD", "CH_NEW", "CH_FAILED"], name="egrid"),
        )
        self.assertEqual(list(screening.stale_extracts(cache)), ["CH_OLD"])
        self.assertEqual(screening.oereb_targets(["CH_OLD", "CH_NEW", "CH_FAILED", "CH_NONE", ""], cache),
                         ["CH_OLD", "CH_FAILED", "CH_NONE"])

    def test_the_board_renders_a_saved_lead_with_its_acquisition_fields(self):
        """The whole path: a decision in `parcel_workflow`, joined to a parcel
        the cascade produced, drawn as a card. The join is `validate=
        "one_to_one"`, so a duplicate decision would raise here rather than
        double a lead on the board."""
        first = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 1",
            sqlite3.connect(self.database),
        ).iloc[0]
        key = [(int(first["bfs"]), str(first["parcel"]))]
        workflow.set_saved(key, True, self.database)
        workflow.update(
            key,
            contact_status="in_discussion",
            owner_name="Erbengemeinschaft Weber",
            due_date="2020-01-01",
            next_step="Zweitgespräch vereinbaren",
            db=self.database,
        )

        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=30
        )
        # The board is its own page now, not stacked under Screening.
        app.session_state[navigation.PAGE] = "Akquisition"
        app.run()

        self.assertFalse(app.exception)
        text = " ".join(element.value for element in app.markdown)
        # The stage label lives inside the board component now, so the check
        # is on the data the component is handed, not on server-rendered text.
        # The card itself is the only place a lead appears now — the Fällige-
        # Wiedervorlagen list above it was dropped, so nothing else can be
        # standing in for a board that failed to draw.
        parcels = pd.read_sql_query(
            "SELECT * FROM parcel_results", sqlite3.connect(self.database)
        )
        cards = acquisition.board_data(
            acquisition.leads(parcels, workflow.load(self.database), "saved"),
            lambda row: None,
            "2026-09-07",
        )
        in_discussion = next(
            stage for stage in cards if stage["code"] == "in_discussion"
        )
        self.assertEqual(len(in_discussion["cards"]), 1)
        self.assertEqual(
            in_discussion["cards"][0]["next"], "Zweitgespräch vereinbaren"
        )
        self.assertTrue(in_discussion["cards"][0]["overdue"])
        self.assertNotIn("Fällige Wiedervorlagen", text)

    def test_the_acquisition_page_is_the_board_and_nothing_above_it(self):
        """The Fällige-Wiedervorlagen strip is gone by request. What has to
        stay is everything that hung off the same page: the export, the board
        and the footer."""
        first = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 1",
            sqlite3.connect(self.database),
        ).iloc[0]
        workflow.set_saved(
            [(int(first["bfs"]), str(first["parcel"]))], True, self.database
        )

        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=30
        )
        app.session_state[navigation.PAGE] = "Akquisition"
        app.run()

        self.assertFalse(app.exception)
        text = " ".join(element.value for element in app.markdown)
        self.assertNotIn("Fällige Wiedervorlagen", text)
        self.assertNotIn("offen", text)
        self.assertTrue(
            any(widget.key == "acq_contacts_csv" for widget in app.button)
            or any(
                getattr(widget, "label", "") == "Kontaktliste exportieren"
                for widget in app.get("download_button")
            )
        )
        html = " ".join(element.proto.body for element in app.get("html"))
        self.assertIn("acquisition-page-footer", html)
        self.assertIn("Kein\nBestandteil der amtlichen Parzellendaten", html)

    def test_moving_a_card_to_another_stage_persists_it(self):
        """A stage change that only moves the widget and never reaches
        `parcel_workflow` would look real on screen right up until the next
        reload put the lead back where it started."""
        first = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 1",
            sqlite3.connect(self.database),
        ).iloc[0]
        bfs, parcel = int(first["bfs"]), str(first["parcel"])
        key = [(bfs, parcel)]
        workflow.set_saved(key, True, self.database)
        workflow.update(key, contact_status="contacted", db=self.database)
        moved = acquisition.handle_board_event(
            {
                "type": "move",
                "bfs": bfs,
                "parcel": parcel,
                "stage": "in_discussion",
            },
            self.saved_leads(),
            self.database,
        )
        self.assertTrue(moved)

        with sqlite3.connect(self.database) as connection:
            stored = connection.execute(
                "SELECT contact_status FROM parcel_workflow "
                "WHERE bfs = ? AND parcel = ?",
                (bfs, parcel),
            ).fetchone()[0]
        self.assertEqual(stored, "in_discussion")

    def test_the_contact_form_stores_what_was_typed(self):
        """`Fertig` fires the same toast whether or not the write behind
        it succeeded — a field `_contact_dialog` dropped on the way to
        `WF.update` would only surface the next time someone reopened this
        exact lead and found their own note missing."""
        first = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 1",
            sqlite3.connect(self.database),
        ).iloc[0]
        bfs, parcel = int(first["bfs"]), str(first["parcel"])
        key = [(bfs, parcel)]
        workflow.set_saved(key, True, self.database)
        workflow.update(key, db=self.database, phone="0787778800")
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        )
        # The board is its own page now, not stacked under Screening.
        app.session_state[navigation.PAGE] = "Akquisition"
        app.session_state[acquisition.CONTACT_OPEN] = f"{bfs}:{parcel}"
        app.run()

        self.assertFalse(app.exception)
        self.assertEqual(field(app, "Telefon").value, "078 777 88 00")
        with sqlite3.connect(self.database) as connection:
            stored_phone = connection.execute(
                "SELECT phone FROM parcel_workflow WHERE bfs = ? AND parcel = ?",
                (bfs, parcel),
            ).fetchone()[0]
        self.assertEqual(stored_phone, "0787778800")

        field(app, "Kontaktperson").set_value("Frau Meier")
        # Typed as digits; stored grouped, which is the point of the formatter.
        field(app, "Telefon").set_value("0790000000")
        field(app, "Letzter Kontakt").set_value("15.09.2026")
        area(app, "Adresse").set_value(
            "Frau Meier\nLandstrasse 10B\n4313 Möhlin"
        )
        area(app, "Notizen").set_value("Rückruf nächste Woche vereinbart.")
        app.button(key="acq_contact_save").click().run()
        self.assertFalse(app.exception)

        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT contact_person, phone, last_contact, owner_address, note "
                "FROM parcel_workflow WHERE bfs = ? AND parcel = ?",
                (bfs, parcel),
            ).fetchone()
        self.assertEqual(
            row,
            (
                "Frau Meier",
                "079 000 00 00",
                "2026-09-15",
                "Frau Meier\nLandstrasse 10B\n4313 Möhlin",
                "Rückruf nächste Woche vereinbart.",
            ),
        )

    def test_a_malformed_date_is_reported_without_losing_valid_live_edits(self):
        """The reference saves each field on change. An invalid date stays
        unsaved and keeps the modal open, while another valid changed field is
        already durable rather than being rolled back with it."""
        first = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 1",
            sqlite3.connect(self.database),
        ).iloc[0]
        bfs, parcel = int(first["bfs"]), str(first["parcel"])
        key = [(bfs, parcel)]
        workflow.set_saved(key, True, self.database)
        workflow.update(
            key,
            last_contact="2026-01-01",
            contact_person="Herr Muster",
            db=self.database,
        )
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        )
        # The board is its own page now, not stacked under Screening.
        app.session_state[navigation.PAGE] = "Akquisition"
        app.session_state[acquisition.CONTACT_OPEN] = f"{bfs}:{parcel}"
        app.run()

        self.assertFalse(app.exception)

        field(app, "Letzter Kontakt").set_value("32.09.2026").run()
        # A later successful field callback must not erase the date error.
        field(app, "Kontaktperson").set_value("Neuer Name").run()
        app.button(key="acq_contact_save").click().run()
        self.assertFalse(app.exception)

        self.assertEqual(len(app.error), 1)
        self.assertEqual(
            app.error[0].value,
            "Bitte ein gültiges Datum im Format TT.MM.JJJJ eingeben.",
        )
        # A refused save must not look like a closed, successful one.
        self.assertEqual(
            app.session_state[acquisition.CONTACT_OPEN], f"{bfs}:{parcel}"
        )

        with sqlite3.connect(self.database) as connection:
            last_contact, contact_person = connection.execute(
                "SELECT last_contact, contact_person FROM parcel_workflow "
                "WHERE bfs = ? AND parcel = ?",
                (bfs, parcel),
            ).fetchone()
        # Only the invalid date is refused; the valid field was saved on change.
        self.assertEqual(last_contact, "2026-01-01")
        self.assertEqual(contact_person, "Neuer Name")

        field(app, "Letzter Kontakt").set_value("15.09.2026").run()
        self.assertEqual(len(app.error), 0)
        app.button(key="acq_contact_save").click().run()
        self.assertNotIn(acquisition.CONTACT_OPEN, app.session_state)

    def test_the_contact_dialog_exposes_the_designs_single_fertig_action(self):
        """The owner modal has one footer action, as in the design; removing
        a parcel remains available from Screening instead of sitting beside
        the contact save action where it could be clicked accidentally."""
        first = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 1",
            sqlite3.connect(self.database),
        ).iloc[0]
        bfs, parcel = int(first["bfs"]), str(first["parcel"])
        key = [(bfs, parcel)]
        workflow.set_saved(key, True, self.database)
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        )
        # The board is its own page now, not stacked under Screening.
        app.session_state[navigation.PAGE] = "Akquisition"
        app.session_state[acquisition.CONTACT_OPEN] = f"{bfs}:{parcel}"
        app.run()

        self.assertFalse(app.exception)

        self.assertEqual(app.button(key="acq_contact_save").label, "Fertig")
        self.assertFalse(
            any(
                widget.key in {"acq_contact_remove", "acq_contact_cancel"}
                for widget in app.button
            )
        )

    def test_a_closed_board_carries_no_contact_form_widgets(self):
        """The reason the dialog exists at all: a per-card `st.expander`
        built the same 9 form widgets for every lead whether or not it was
        open, so a board of 100 leads shipped ~900 invisible widgets to the
        browser. A regression that put the form back on the card — even
        collapsed — would leave those widgets in the page tree with the
        dialog still reporting closed, so this counts them directly rather
        than trusting a toggle that a moved-back form wouldn't touch."""
        parcels = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 5",
            sqlite3.connect(self.database),
        )
        keys = [(int(row.bfs), str(row.parcel)) for row in parcels.itertuples()]
        workflow.set_saved(keys, True, self.database)

        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        )
        # The board is its own page now, not stacked under Screening.
        app.session_state[navigation.PAGE] = "Akquisition"
        app.run()
        self.assertFalse(app.exception)

        self.assertNotIn(acquisition.CONTACT_OPEN, app.session_state)
        self.assertEqual(len(app.text_input), 0)
        self.assertEqual(len(app.text_area), 0)
        # Cards and stage controls now live in one iframe component rather than
        # producing three Streamlit widgets per lead. Five leads must therefore
        # still render one board component and zero native stage selectboxes.
        stage_selectboxes = [
            widget
            for widget in app.selectbox
            if str(widget.key or "").startswith("stage_")
        ]
        self.assertEqual(len(stage_selectboxes), 0)
        self.assertEqual(len(app.get("component_instance")), 1)

    def test_the_contact_list_exports_every_saved_lead(self):
        """Owner details are typed in by hand from the AGIS extract. The export
        has to carry exactly what was recorded, because it is the only way that
        work leaves the application."""
        import io
        from unittest.mock import patch

        from streamlit.runtime.memory_media_file_storage import (
            MemoryMediaFileStorage,
        )

        first = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 2",
            sqlite3.connect(self.database),
        )
        keys = [(int(r.bfs), str(r.parcel)) for r in first.itertuples()]
        workflow.set_saved(keys, True, self.database)
        workflow.update(
            keys, owner_name="Muster AG", phone="+41 44 000 00 00",
            db=self.database,
        )

        # Same capture as `test_the_csv_export_carries_the_shown_rows`: the
        # button's own proto carries only a content-addressed `url`, and the
        # in-memory store behind it is torn down the moment `.run()` returns.
        captured = {}
        original = MemoryMediaFileStorage.load_and_get_id

        def spy(self, path_or_data, mimetype, kind, filename=None):
            file_id = original(self, path_or_data, mimetype, kind, filename)
            captured[file_id] = path_or_data
            return file_id

        with patch.object(MemoryMediaFileStorage, "load_and_get_id", spy):
            app = AppTest.from_file(
                os.path.join(paths.HERE, "app.py"), default_timeout=60
            )
            # The board is its own page now, not stacked under Screening.
            app.session_state[navigation.PAGE] = "Akquisition"
            app.run()

        self.assertFalse(app.exception)
        exported = list(app.get("download_button"))
        self.assertTrue(exported, "no contact-list export on the acquisition board")
        file_id = os.path.splitext(os.path.basename(exported[0].proto.url))[0]
        payload = captured[file_id]
        frame = pd.read_csv(io.BytesIO(payload))

        self.assertEqual(len(frame), 2)
        for column in (
            "Adresse", "Gemeinde", "Eigentümerschaft", "Telefon", "Stufe",
            "Wiedervorlage", "Nächster Schritt",
        ):
            self.assertIn(column, frame.columns)
        self.assertTrue((frame["Eigentümerschaft"] == "Muster AG").all())
        self.assertTrue((frame["Telefon"] == "+41 44 000 00 00").all())

    def test_the_analyse_button_opens_the_single_parcel_view(self):
        """Analyse is the only door into the detail view; if the session key
        it sets ever drifted from what `detail.find` reads back, the button
        would still render and still be clickable while doing nothing."""
        first = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 1",
            sqlite3.connect(self.database),
        ).iloc[0]
        bfs, parcel = int(first["bfs"]), str(first["parcel"])
        key = [(bfs, parcel)]
        workflow.set_saved(key, True, self.database)
        state = {navigation.PAGE: "Akquisition"}
        opened = acquisition.handle_board_event(
            {"type": "analyse", "bfs": bfs, "parcel": parcel},
            self.saved_leads(),
            self.database,
            state,
        )
        self.assertTrue(opened)
        self.assertEqual(state["selected_parcel_id"], f"{bfs}:{parcel}")
        self.assertEqual(state[navigation.PENDING], "Analyse")

    def test_the_merkliste_totals_the_shortlist_it_lists(self):
        """The board groups the same leads by stage; this page's whole job is
        the total. A tile that drifted from the table beneath it would be the
        number someone quotes without ever scrolling down to check it."""
        rows = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 2",
            sqlite3.connect(self.database),
        )
        keys = [(int(row.bfs), str(row.parcel)) for row in rows.itertuples()]
        workflow.set_saved(keys, True, self.database)

        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=30
        )
        app.session_state[navigation.PAGE] = "Merkliste"
        app.run()

        self.assertFalse(app.exception)

        shortlist = self.saved_leads().sort_values(
            ["municipality", "parcel"], kind="stable"
        )
        component_rows = merkliste.table_rows(shortlist, lambda row: 0.0)
        self.assertEqual(len(component_rows), 2)

        # `app.metric[i].value` is the formatted body text (`MetricProto.body`,
        # e.g. "2"), not a number — read via the label so this does not depend
        # on tile order.
        parcels_tile = next(m for m in app.metric if m.label == "Parzellen")
        self.assertEqual(parcels_tile.value, "2")

    def test_only_the_selected_page_renders(self):
        """The reason navigation is a segmented control and not `st.tabs`:
        tabs run every tab body on every rerun, and Analyse recomputes residual
        values, reads the ÖREB cache and can build a PDF. If the screening
        table ever appears while another page is selected, that laziness has
        been lost and every keystroke pays for all four pages."""
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        ).run()
        self.assertTrue(app.dataframe, "screening table missing on the default page")

        app.segmented_control(key="acq_page").set_value("Merkliste").run()

        self.assertFalse(app.exception)
        headings = " ".join(h.value for h in app.subheader)
        self.assertIn("Gemerkte Parzellen", headings)
        columns = [
            list(frame.value.columns)
            for frame in app.dataframe
            if hasattr(frame.value, "columns")
        ]
        self.assertFalse(
            any("Ziffer" in cols for cols in columns),
            "the screening table rendered while another page was selected",
        )

    def test_analyse_says_so_when_nothing_is_selected(self):
        """Reachable only now that Analyse is a page rather than an early
        return that could not be reached without a parcel — so it had never
        been designed."""
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        ).run()

        app.segmented_control(key="acq_page").set_value("Analyse").run()

        self.assertFalse(app.exception)
        self.assertTrue(app.info)
        self.assertIn("Keine Parzelle", " ".join(i.value for i in app.info))

    def test_opening_a_lead_from_the_board_lands_on_analyse(self):
        """The whole reason navigation carries a second state key. Selecting
        the parcel without moving the reader leaves them on the board wondering
        what the button did."""
        first = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 1",
            sqlite3.connect(self.database),
        ).iloc[0]
        bfs, parcel = int(first["bfs"]), str(first["parcel"])
        workflow.set_saved([(bfs, parcel)], True, self.database)

        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        )
        app.session_state[navigation.PAGE] = "Akquisition"
        app.run()
        state = {navigation.PAGE: "Akquisition"}
        opened = acquisition.handle_board_event(
            {"type": "analyse", "bfs": bfs, "parcel": parcel},
            self.saved_leads(),
            self.database,
            state,
        )
        self.assertTrue(opened)
        for name, value in state.items():
            app.session_state[name] = value
        app.run()

        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["acq_page"], "Analyse")
        self.assertEqual(
            app.session_state["selected_parcel_id"], f"{bfs}:{parcel}"
        )
        # Absence, not just presence: the companion test proves the empty state
        # appears with nothing selected, which a branch that always showed it
        # would also satisfy. Only asserting it is gone here can tell the two
        # apart, and a banner saying no parcel is chosen sitting above the
        # parcel would be a plain contradiction on screen.
        self.assertNotIn(
            "Keine Parzelle", " ".join(element.value for element in app.info)
        )



class OerebRefreshTest(unittest.TestCase):
    """"ÖREB prüfen" on a scratch database: a stored extract without the legal
    status is fetched again; a failed fetch keeps it; an answer still without
    a status is not fetched again."""

    STORED = {"provisions": [{"title": "Gestaltungsplan Giessi", "number": "21.259"}],
              "created": "2026-08-18T10:49:35"}

    def setUp(self):
        import screening
        import scope_auth
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.database = os.path.join(self.tempdir.name, "scratch.sqlite")
        with sqlite3.connect(self.database) as con:
            con.execute("CREATE TABLE oereb_cache (egrid TEXT PRIMARY KEY, hard TEXT, notable TEXT, "
                        "error TEXT, checked_at TEXT, details TEXT)")
            con.execute("INSERT INTO oereb_cache VALUES (?,?,?,?,?,?)",
                        ("CH_OLD", "", "Waldabstand (12 m²)", "", "2026-08-18 10:50:00",
                         json.dumps(self.STORED)))
        patcher = patch.object(paths, "DB", self.database)
        patcher.start()
        self.addCleanup(patcher.stop)
        auth = patch.object(scope_auth, "require_write", lambda db=None: None)
        auth.start()
        self.addCleanup(auth.stop)
        self.screening = screening

    def doc(self, lawstatus):
        provision = {"Type": {"Code": "LegalProvision"},
                     "Title": [{"Language": "de", "Text": "Gestaltungsplan Giessi"}],
                     "OfficialNumber": [{"Language": "de", "Text": "21.259"}]}
        if lawstatus:
            provision["Lawstatus"] = {"Code": lawstatus}
        return {"GetExtractByIdResponse": {"extract": {"CreationDate": "2026-10-05T08:10:14",
                "RealEstate": {"RestrictionOnLandownership": [{"LegalProvisions": [provision]}]}}}}

    def stored(self):
        with sqlite3.connect(self.database) as con:
            return con.execute("SELECT notable, error, checked_at, details FROM oereb_cache "
                               "WHERE egrid='CH_OLD'").fetchone()

    def test_a_refresh_stores_the_legal_status(self):
        self.assertEqual(self.screening.oereb_targets(["CH_OLD"], self.screening.read_oereb_cache()),
                         ["CH_OLD"])
        with patch.object(oereb, "assess", lambda egrid: ([], [], None, oereb.details(self.doc("inForce")))):
            self.screening.check_oereb(["CH_OLD"])
        details = json.loads(self.stored()[3])
        self.assertEqual([p["lawstatus"] for p in details["provisions"]], ["inForce"])
        self.assertEqual(self.screening.oereb_targets(["CH_OLD"], self.screening.read_oereb_cache()), [])

    def test_a_failed_refresh_keeps_the_stored_extract(self):
        before = self.stored()
        with patch.object(oereb, "assess", lambda egrid: ([], [], "HTTP Error 502: Bad Gateway", None)):
            self.screening.check_oereb(["CH_OLD"])
        self.assertEqual(self.stored(), before)
        # Asked again on the next click — the user's, not a loop.
        self.assertEqual(self.screening.oereb_targets(["CH_OLD"], self.screening.read_oereb_cache()),
                         ["CH_OLD"])

    def test_an_answer_that_is_no_extract_keeps_the_stored_extract(self):
        before = self.stored()
        with patch.object(oereb, "fetch", lambda egrid: {"message": "Service temporarily unavailable"}):
            self.screening.check_oereb(["CH_OLD"])
        self.assertEqual(self.stored(), before)

    def test_a_failed_call_keeps_a_legacy_row_and_its_exclusion(self):
        with sqlite3.connect(self.database) as con:
            con.execute("INSERT INTO oereb_cache VALUES (?,?,?,?,?,?)",
                        ("CH_LEGACY", "Planungszone Ortskern", "", "", "2026-08-11 09:00:00", None))
        with patch.object(oereb, "assess", lambda egrid: ([], [], "HTTP Error 502: Bad Gateway", None)):
            summary = self.screening.check_oereb(["CH_LEGACY", "CH_NEW"])
        with sqlite3.connect(self.database) as con:
            legacy = con.execute("SELECT hard, error FROM oereb_cache WHERE egrid='CH_LEGACY'").fetchone()
            new = con.execute("SELECT hard, error FROM oereb_cache WHERE egrid='CH_NEW'").fetchone()
        self.assertEqual(legacy, ("Planungszone Ortskern", ""))
        self.assertEqual(new, ("", "HTTP Error 502: Bad Gateway"))
        self.assertEqual(summary, {"asked": 2, "failed": 2})

    def test_an_answer_still_without_a_status_is_not_asked_again(self):
        calls = []
        def assess(egrid):
            calls.append(egrid)
            return [], [], None, oereb.details(self.doc(None))
        with patch.object(oereb, "assess", assess):
            for _ in range(3):
                self.screening.check_oereb(self.screening.oereb_targets(
                    ["CH_OLD"], self.screening.read_oereb_cache()))
        self.assertEqual(calls, ["CH_OLD"])
        details = json.loads(self.stored()[3])
        self.assertEqual([p["lawstatus"] for p in details["provisions"]], [""])

    def test_a_call_that_raises_keeps_the_stored_extract(self):
        before = self.stored()
        with patch.object(oereb, "assess", side_effect=RuntimeError("timed out")):
            summary = self.screening.check_oereb(["CH_OLD"])
        self.assertEqual(self.stored(), before)
        self.assertEqual(summary, {"asked": 1, "failed": 1})

    def test_a_failure_never_replaces_a_row_another_run_stored_meanwhile(self):
        """Two clicks at once: the parcel was not cached when this run began,
        and another run stored its extract before this one's call failed."""
        good = ("", "", "", "2026-10-05 08:00:00", json.dumps({"lawstatus_kept": True}))
        def assess(egrid):
            with sqlite3.connect(self.database) as con:
                con.execute("INSERT OR REPLACE INTO oereb_cache VALUES (?,?,?,?,?,?)", (egrid,) + good)
            return [], [], "HTTP Error 502: Bad Gateway", None
        with patch.object(oereb, "assess", assess):
            summary = self.screening.check_oereb(["CH_NEW"])
        with sqlite3.connect(self.database) as con:
            row = con.execute("SELECT hard, notable, error, checked_at, details FROM oereb_cache "
                              "WHERE egrid='CH_NEW'").fetchone()
        self.assertEqual(row, good)
        self.assertEqual(summary, {"asked": 1, "failed": 1})


class OerebButtonUiTest(unittest.TestCase):
    """"ÖREB prüfen" as the user meets it, on a scratch copy of the seed
    database: a failed run warns once and leaves every cached row as it was;
    the next, successful run shows no warning, nor does the render after it."""

    STORED = json.dumps({"provisions": [{"title": "Gestaltungsplan Alt", "number": "05.120"}],
                         "created": "2026-08-18T10:00:00"})
    DOC = {"GetExtractByIdResponse": {"extract": {"CreationDate": "2026-10-05T08:00:00", "RealEstate": {
        "RestrictionOnLandownership": [{"LegalProvisions": [{
            "Type": {"Code": "LegalProvision"},
            "Title": [{"Language": "de", "Text": "Gestaltungsplan Alt"}],
            "OfficialNumber": [{"Language": "de", "Text": "05.120"}],
            "Lawstatus": {"Code": "inForce"}}]}]}}}}

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.database = os.path.join(self.tempdir.name, "results.sqlite")
        shutil.copy2(paths.SEED_DB, self.database)
        with sqlite3.connect(self.database) as con:
            con.execute("DELETE FROM oereb_cache")
            ingest.schema(con)
            egrids = [row[0] for row in con.execute(
                "SELECT egrid FROM parcel_results WHERE egrid IS NOT NULL AND egrid != '' "
                "ORDER BY delta DESC LIMIT 400")]
            # Stored before the legal status was kept: "ÖREB prüfen" asks again.
            con.executemany(
                "INSERT INTO oereb_cache (egrid, hard, notable, error, checked_at, details) "
                "VALUES (?,?,?,?,?,?)",
                [(e, "", "Waldabstand (12 m²)", "", "2026-08-18 10:50:00", self.STORED)
                 for e in egrids])
        self.seeded = set(egrids)
        original = paths.DB
        paths.DB = self.database
        self.addCleanup(setattr, paths, "DB", original)
        st.cache_data.clear()
        self.addCleanup(st.cache_data.clear)
        # The hosted case: no geodata, so the button reads "ÖREB prüfen".
        geodata = patch.object(ingest, "geodata_available", lambda canton="AG": False)
        geodata.start()
        self.addCleanup(geodata.stop)
        self.calls = []

    def cached(self):
        with sqlite3.connect(self.database) as con:
            return {row[0]: row[1:] for row in con.execute(
                "SELECT egrid, hard, notable, error, checked_at, details FROM oereb_cache")}

    def click(self, app, assess):
        with patch.object(oereb, "assess", assess):
            app.button(key="screening_oereb_refresh").click().run()
        self.assertFalse(app.exception)

    @staticmethod
    def warnings(app):
        return [w.value for w in app.warning if "fehlgeschlagen" in w.value]

    def test_a_failed_run_warns_once_keeps_the_cache_and_a_good_run_clears_it(self):
        def failing(egrid):
            self.calls.append(egrid)
            return [], [], "HTTP Error 502: Bad Gateway", None

        def answering(egrid):
            self.calls.append(egrid)
            return [], [], None, oereb.details(self.DOC)

        before = self.cached()
        app = AppTest.from_file(os.path.join(paths.HERE, "app.py"), default_timeout=120).run()
        self.assertFalse(app.exception)

        self.click(app, failing)
        asked = self.seeded & set(self.calls)
        self.assertTrue(asked, "the run asked for none of the stored extracts")
        self.assertEqual(len(self.warnings(app)), 1, self.warnings(app))
        after = self.cached()
        self.assertEqual({e: after[e] for e in self.seeded}, {e: before[e] for e in self.seeded})
        app.run()  # shown once, not on every render after
        self.assertEqual(self.warnings(app), [])

        self.calls.clear()
        self.click(app, answering)
        self.assertEqual(self.warnings(app), [])
        refreshed = self.cached()
        self.assertTrue(all('"lawstatus_kept": true' in refreshed[e][4] for e in asked))

        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(self.warnings(app), [])

    def test_the_refresh_above_the_list_asks_the_cadastre_only(self):
        """Visible beside the export, for members who may write. With geodata
        at hand it still asks the cadastre only — no recompute of the cascade."""
        def answering(egrid):
            self.calls.append(egrid)
            return [], [], None, oereb.details(self.DOC)

        def recompute(**_):
            raise AssertionError("ÖREB prüfen must not recompute the cascade")

        app = AppTest.from_file(os.path.join(paths.HERE, "app.py"), default_timeout=120).run()
        with patch.object(ingest, "geodata_available", lambda canton="AG": True), \
                patch.object(ingest, "recompute", recompute):
            app.run()
            button = app.button(key="screening_oereb_refresh")
            self.assertEqual(button.label, "ÖREB prüfen")
            self.click(app, answering)
        self.assertTrue(self.seeded & set(self.calls))
        self.assertTrue(any("ÖREB-Abfrage abgeschlossen" in s.value for s in app.success),
                        [s.value for s in app.success])
        app.run()
        self.assertFalse(any("ÖREB-Abfrage abgeschlossen" in s.value for s in app.success))

    def test_a_database_another_writer_holds_stops_the_run_with_a_message(self):
        """Another session writing while the run writes: a real lock, held by
        a second connection for exactly the run, with SQLite's wait cut to a
        tenth of a second. The page says why the run stopped instead of
        failing, and every cached row stays as it was."""
        def answering(egrid):
            self.calls.append(egrid)
            return [], [], None, oereb.details(self.DOC)

        connect, run = sqlite3.connect, screening.check_oereb
        holder = connect(self.database, isolation_level=None, check_same_thread=False)
        self.addCleanup(holder.close)

        def while_another_writes(egrids, progress=None):
            holder.execute("BEGIN IMMEDIATE")
            try:
                with patch.object(sqlite3, "connect", functools.partial(connect, timeout=0.1)):
                    return run(egrids, progress)
            finally:
                holder.rollback()

        before = self.cached()
        app = AppTest.from_file(os.path.join(paths.HERE, "app.py"), default_timeout=120).run()
        self.assertFalse(app.exception)
        with patch.object(screening, "check_oereb", while_another_writes):
            self.click(app, answering)
        self.assertTrue(self.calls)
        self.assertTrue(any("Datenbank gesperrt" in e.value for e in app.error), [e.value for e in app.error])
        self.assertEqual(self.cached(), before)

    def test_a_reader_is_not_offered_the_refresh(self):
        import scope_auth
        with patch.object(scope_auth, "may_write", lambda db=None: False):
            app = AppTest.from_file(os.path.join(paths.HERE, "app.py"), default_timeout=120).run()
        self.assertFalse(app.exception)
        self.assertNotIn("screening_oereb_refresh", [b.key for b in app.button])

    def test_after_a_recompute_the_shortlist_as_recomputed_is_asked(self):
        """With geodata the button recomputes first; the cadastre is then asked
        for the shortlist as recomputed, not as the app had it cached."""
        def recompute(**_):
            with sqlite3.connect(self.database) as con:
                con.execute("UPDATE parcel_results SET egrid = 'CH_FRESH_' || egrid "
                            "WHERE egrid IS NOT NULL AND egrid != ''")

        def answering(egrid):
            self.calls.append(egrid)
            return [], [], None, oereb.details(self.DOC)

        app = AppTest.from_file(os.path.join(paths.HERE, "app.py"), default_timeout=120).run()
        self.assertFalse(app.exception)
        with patch.object(ingest, "geodata_available", lambda canton="AG": True), \
                patch.object(ingest, "recompute", recompute), patch.object(oereb, "assess", answering):
            app.run()
            next(b for b in app.button if "Neu berechnen" in b.label).click().run()
        self.assertFalse(app.exception)
        self.assertTrue(self.calls)
        self.assertEqual([e for e in self.calls if not e.startswith("CH_FRESH_")], [])


if __name__ == "__main__":
    unittest.main()
