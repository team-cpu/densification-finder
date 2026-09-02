import os
import shutil
import sqlite3
import tempfile
import unittest

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

import acquisition
import ingest
import merkliste
import navigation
import paths
import searches
import workflow


def field(app, label):
    """Contact-form inputs carry no widget key, only a label — this is how
    the acquisition-board tests address one instead of relying on the
    form's field order."""
    return next(w for w in app.text_input if w.label == label)


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

    def saved_leads(self):
        with sqlite3.connect(self.database) as connection:
            parcels = pd.read_sql_query("SELECT * FROM parcel_results", connection)
        return acquisition.leads(parcels, workflow.load(self.database), "saved")

    def test_controls_mixed_results_and_economic_indicator(self):
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=30
        ).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.number_input[0].min, 130)
        area_min = next(n for n in app.number_input if n.label == "Fläche von (m²)")
        area_max = next(n for n in app.number_input if n.label == "Fläche bis (m²)")
        self.assertEqual(area_min.value, 300)
        self.assertIsNone(area_max.value)
        result_limit = next(s for s in app.selectbox if s.label == "Anzeigen")
        self.assertEqual(result_limit.value, 20)
        self.assertEqual(app.number_input[1].label, "Ziffer")
        self.assertIsNone(app.number_input[1].value)

        # The Kanton selector sits ahead of Grundstückstyp in the control row,
        # so it is selectbox[0]; Grundstückstyp moved to selectbox[1].
        app.selectbox[1].select("Alle").run()
        self.assertFalse(app.exception)
        frame = app.dataframe[0].value
        self.assertEqual(
            frame["Typ"].value_counts().to_dict(),
            {"bebaut": 10, "unbebaut": 10},
        )
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
        # The Kanton selector sits ahead of Grundstückstyp in the control row,
        # so it is selectbox[0]; Grundstückstyp moved to selectbox[1].
        app.selectbox[1].select("Alle").run()

        area_min = next(n for n in app.number_input if n.label == "Fläche von (m²)")
        area_min.set_value(5000).run()
        self.assertFalse(app.exception)
        large = app.dataframe[0].value
        self.assertTrue(len(large) > 0)
        self.assertTrue((large["Fläche m²"] > 5000).all())

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
        # The Kanton selector sits ahead of Grundstückstyp in the control row,
        # so it is selectbox[0]; Grundstückstyp moved to selectbox[1].
        app.selectbox[1].select("Unbebaut").run()
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

    def test_confirmed_transport_parcel_is_hidden_by_default_and_recoverable(self):
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=30
        ).run()
        first = app.dataframe[0].value.iloc[0]
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE parcel_results SET transport_share = 0.95 "
                "WHERE municipality = ? AND parcel = ?",
                (first["Gemeinde"], str(first["Parzelle"])),
            )
        st.cache_data.clear()

        app.run()
        visible = app.dataframe[0].value
        match = (
            (visible["Gemeinde"] == first["Gemeinde"])
            & (visible["Parzelle"].astype(str) == str(first["Parzelle"]))
        )
        self.assertFalse(match.any())

        transport_filter = next(
            checkbox
            for checkbox in app.checkbox
            if checkbox.label == "Strassen-/Bahnparzellen"
        )
        transport_filter.uncheck().run()
        visible = app.dataframe[0].value
        match = (
            (visible["Gemeinde"] == first["Gemeinde"])
            & (visible["Parzelle"].astype(str) == str(first["Parzelle"]))
        )
        self.assertTrue(match.any())

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
        self.assertIn("Im Gespräch", text)
        # Due in 2020 and today is not: the overdue list must have drawn.
        # The follow-up list is a row of widgets now, not a dataframe with a
        # `Wiedervorlage` column, so its presence is read off the row's own
        # `Eigentümer` button and next-step text instead.
        self.assertIn("Fällige Wiedervorlagen", text)
        due_buttons = [
            widget
            for widget in app.button
            if str(widget.key or "").startswith("due_contact_")
        ]
        self.assertEqual(len(due_buttons), 1)
        self.assertIn("Zweitgespräch vereinbaren", text)

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
        """`Speichern` fires the same toast whether or not the write behind
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
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        )
        # The board is its own page now, not stacked under Screening.
        app.session_state[navigation.PAGE] = "Akquisition"
        app.session_state[acquisition.CONTACT_OPEN] = f"{bfs}:{parcel}"
        app.run()

        self.assertFalse(app.exception)

        field(app, "Kontaktperson").set_value("Frau Meier")
        field(app, "Telefon").set_value("+41 79 000 00 00")
        field(app, "Wiedervorlage").set_value("2026-09-15")
        app.text_area[0].set_value("Rückruf nächste Woche vereinbart.")
        app.button(key="acq_contact_save").click().run()
        self.assertFalse(app.exception)

        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT contact_person, phone, due_date, note "
                "FROM parcel_workflow WHERE bfs = ? AND parcel = ?",
                (bfs, parcel),
            ).fetchone()
        self.assertEqual(
            row,
            (
                "Frau Meier",
                "+41 79 000 00 00",
                "2026-09-15",
                "Rückruf nächste Woche vereinbart.",
            ),
        )

    def test_a_malformed_date_is_reported_and_the_whole_save_is_refused(self):
        """`workflow.update` validates every field before it writes any of
        them, but only because `_contact_dialog` stops on the `ValueError`
        instead of ignoring it — and leaves the dialog open on the error
        rather than closing it, which would read as "saved" to the user. A
        save that let the good fields through anyway would leave a contact
        name typed today sitting next to a Wiedervorlage date nobody actually
        entered."""
        first = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 1",
            sqlite3.connect(self.database),
        ).iloc[0]
        bfs, parcel = int(first["bfs"]), str(first["parcel"])
        key = [(bfs, parcel)]
        workflow.set_saved(key, True, self.database)
        workflow.update(
            key,
            due_date="2026-01-01",
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

        field(app, "Wiedervorlage").set_value("02.09.2026")
        field(app, "Kontaktperson").set_value("Neuer Name")
        app.button(key="acq_contact_save").click().run()
        self.assertFalse(app.exception)

        self.assertEqual(len(app.error), 1)
        self.assertEqual(
            app.error[0].value,
            "due_date must be an ISO date (YYYY-MM-DD) or empty",
        )
        # A refused save must not look like a closed, successful one.
        self.assertEqual(
            app.session_state[acquisition.CONTACT_OPEN], f"{bfs}:{parcel}"
        )

        with sqlite3.connect(self.database) as connection:
            due_date, contact_person = connection.execute(
                "SELECT due_date, contact_person FROM parcel_workflow "
                "WHERE bfs = ? AND parcel = ?",
                (bfs, parcel),
            ).fetchone()
        # Refused together, not partially applied: neither field moved.
        self.assertEqual(due_date, "2026-01-01")
        self.assertEqual(contact_person, "Herr Muster")

    def test_removing_a_lead_takes_it_off_the_board(self):
        """`Von Merkliste entfernen` sits one button away from `Speichern`
        in the same dialog — a copy-paste of the wrong boolean into
        `WF.set_saved` would silently keep a declined lead on the board
        instead of taking it off."""
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

        app.button(key="acq_contact_remove").click().run()
        self.assertFalse(app.exception)

        with sqlite3.connect(self.database) as connection:
            saved = connection.execute(
                "SELECT saved FROM parcel_workflow WHERE bfs = ? AND parcel = ?",
                (bfs, parcel),
            ).fetchone()[0]
        self.assertEqual(saved, 0)
        self.assertFalse(
            any(
                str(widget.key or "").startswith("stage_")
                for widget in app.selectbox
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

    def test_the_due_lists_eigentuemer_button_opens_that_rows_own_lead(self):
        """A button wired to the first lead in the shortlist, rather than the
        one on its own row, would still pass with a single overdue lead on
        the list — this needs two, both overdue, so clicking the second row
        and getting the first row's owner back is a visible failure."""
        rows = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 2",
            sqlite3.connect(self.database),
        )
        keys = [(int(r.bfs), str(r.parcel)) for r in rows.itertuples()]
        workflow.set_saved(keys, True, self.database)
        workflow.update(
            [keys[0]], due_date="2020-01-01", owner_name="Erste Eigentümerin",
            db=self.database,
        )
        workflow.update(
            [keys[1]], due_date="2020-02-02", owner_name="Zweite Eigentümerin",
            db=self.database,
        )
        bfs, parcel = keys[1]
        slug = f"{bfs}_{parcel}"

        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        )
        app.session_state[navigation.PAGE] = "Akquisition"
        app.run()

        app.button(key=f"due_contact_{slug}").click().run()
        self.assertFalse(app.exception)

        self.assertEqual(
            app.session_state[acquisition.CONTACT_OPEN], f"{bfs}:{parcel}"
        )
        self.assertEqual(
            field(app, "Eigentümerschaft").value, "Zweite Eigentümerin"
        )

    def test_the_due_lists_analyse_button_opens_that_rows_parcel(self):
        """Mirrors `test_opening_a_lead_from_the_board_lands_on_analyse` for
        the follow-up list's own copy of the button — the list is a second
        place a lead can be opened from, with its own widget key, so it needs
        its own proof that key actually opens the right parcel."""
        first = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 1",
            sqlite3.connect(self.database),
        ).iloc[0]
        bfs, parcel = int(first["bfs"]), str(first["parcel"])
        workflow.set_saved([(bfs, parcel)], True, self.database)
        workflow.update([(bfs, parcel)], due_date="2020-01-01", db=self.database)
        slug = f"{bfs}_{parcel}"

        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        )
        app.session_state[navigation.PAGE] = "Akquisition"
        app.run()

        app.button(key=f"due_open_{slug}").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["acq_page"], "Analyse")
        self.assertEqual(
            app.session_state["selected_parcel_id"], f"{bfs}:{parcel}"
        )

    def test_the_due_list_tints_only_overdue_rows_and_counts_only_them(self):
        """The badge and the tint are the two things telling "late" from
        "merely scheduled" apart on this list; both used to come from one
        `Styler` call over a dataframe, so this proves neither guarantee was
        lost now that the rows are drawn as separate widgets."""
        rows = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 2",
            sqlite3.connect(self.database),
        )
        keys = [(int(r.bfs), str(r.parcel)) for r in rows.itertuples()]
        workflow.set_saved(keys, True, self.database)
        # One overdue, one not — due_items shows both, but only the first
        # counts toward the badge and should carry the tint.
        workflow.update([keys[0]], due_date="2020-01-01", db=self.database)
        workflow.update([keys[1]], due_date="2099-01-01", db=self.database)

        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        )
        app.session_state[navigation.PAGE] = "Akquisition"
        app.run()
        self.assertFalse(app.exception)

        text = " ".join(element.value for element in app.markdown)
        self.assertIn("Fällige Wiedervorlagen** · 1 offen", text)

        due_buttons = [
            widget
            for widget in app.button
            if str(widget.key or "").startswith("due_contact_")
        ]
        self.assertEqual(len(due_buttons), 2)

        tinted = [m for m in app.markdown if "#fdf5e7" in m.value]
        self.assertEqual(len(tinted), 1)
        self.assertIn("2020-01-01", tinted[0].value)
        self.assertNotIn("2099-01-01", tinted[0].value)


    def test_a_hand_edited_due_date_cannot_inject_markup(self):
        """The tinted date is the one field on a follow-up row interpolated
        into markup rather than written through `st.write`. `workflow.update`
        would refuse this value, but the database is a file on a volume that
        can be edited by hand, so the row must defend itself rather than trust
        a validator in another module."""
        first = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 1",
            sqlite3.connect(self.database),
        ).iloc[0]
        bfs, parcel = int(first["bfs"]), str(first["parcel"])
        workflow.set_saved([(bfs, parcel)], True, self.database)
        # Written straight past the validation `workflow.update` would apply.
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE parcel_workflow SET due_date = ? "
                "WHERE bfs = ? AND parcel = ?",
                ("2020-01-01<img src=x onerror=alert(1)>", bfs, parcel),
            )

        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        )
        app.session_state[navigation.PAGE] = "Akquisition"
        app.run()

        self.assertFalse(app.exception)
        # The date still sorts as overdue, so it takes the tinted branch — the
        # one that builds markup. Only that branch is asserted here: the plain
        # cells go through `st.write`, whose element value is the markdown
        # source Streamlit escapes when it renders, not markup it emits.
        tinted = [m.value for m in app.markdown if "#fdf5e7" in m.value]
        self.assertEqual(len(tinted), 1)
        self.assertNotIn("<img src=x", tinted[0])
        self.assertIn("&lt;img src=x", tinted[0])

if __name__ == "__main__":
    unittest.main()
