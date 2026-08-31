import os
import shutil
import sqlite3
import tempfile
import unittest

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

import ingest
import paths
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

    def test_controls_mixed_results_and_economic_indicator(self):
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=30
        ).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.number_input[0].min, 130)
        self.assertEqual(app.number_input[1].max, 50)
        area = app.select_slider[0]
        self.assertEqual(area.value, (300, float("inf")))
        self.assertEqual(area.options[-1], "ohne Limite")
        self.assertEqual(app.number_input[2].label, "Ziffer")
        self.assertIsNone(app.number_input[2].value)

        app.selectbox[0].select("Alle").run()
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

        app.number_input[1].set_value(50).run()
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
        app.selectbox[0].select("Alle").run()

        app.select_slider[0].set_value((5000, float("inf"))).run()
        self.assertFalse(app.exception)
        large = app.dataframe[0].value
        self.assertTrue(len(large) > 0)
        self.assertTrue((large["Fläche m²"] > 5000).all())

        app.select_slider[0].set_value((0, 300)).run()
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
        app.selectbox[0].select("Unbebaut").run()
        self.assertFalse(app.exception)
        top = app.dataframe[0].value.iloc[0]
        self.assertEqual(top["Gemeinde"], "Rheinfelden")
        self.assertEqual(top["Parzelle"], "574")
        self.assertGreater(top["Potenzial m² (Schätzung)"], 100_000)

    def test_ziffer_filters_to_the_typed_exact_value(self):
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=30
        ).run()

        app.number_input[2].set_value(0.8).run()

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
            if checkbox.label == "Strassen-/Bahnparzellen ausblenden"
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


    def test_a_failed_cadastre_call_is_retried_rather_than_cached_forever(self):
        """A transient 502 used to count as an answer: the parcel stayed
        unchecked and the interface still called the shortlist complete."""
        import app

        cache = pd.DataFrame(
            {"details": ["", "{}"], "error": ["HTTP Error 502: Bad Gateway", ""]},
            index=pd.Index(["CH_FAILED", "CH_OK"], name="egrid"),
        )
        self.assertEqual(list(app.with_extract(cache)), ["CH_OK"])
        self.assertEqual(list(app.failed_egrids(cache)), ["CH_FAILED"])

    def test_a_legacy_row_without_the_extract_is_asked_again(self):
        """Rows written before the legal basis was stored carry restrictions but
        no documents, and must not count as complete."""
        import app

        cache = pd.DataFrame(
            {"details": [None], "error": [None]},
            index=pd.Index(["CH_OLD"], name="egrid"),
        )
        self.assertEqual(list(app.with_extract(cache)), [])
        self.assertEqual(list(app.failed_egrids(cache)), [])

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
        ).run()

        self.assertFalse(app.exception)
        text = " ".join(element.value for element in app.markdown)
        self.assertIn("Im Gespräch", text)
        # Due in 2020 and today is not: the overdue list must have drawn.
        overdue = [
            frame.value
            for frame in app.dataframe
            if "Wiedervorlage" in getattr(frame.value, "columns", [])
        ]
        self.assertEqual(len(overdue), 1)
        self.assertEqual(
            list(overdue[0]["Nächster Schritt"]), ["Zweitgespräch vereinbaren"]
        )

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
        slug = f"{bfs}_{parcel}"

        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        ).run()
        self.assertFalse(app.exception)

        app.selectbox(key=f"stage_{slug}").select("in_discussion").run()
        self.assertFalse(app.exception)

        with sqlite3.connect(self.database) as connection:
            stored = connection.execute(
                "SELECT contact_status FROM parcel_workflow "
                "WHERE bfs = ? AND parcel = ?",
                (bfs, parcel),
            ).fetchone()[0]
        self.assertEqual(stored, "in_discussion")

    def test_the_contact_form_stores_what_was_typed(self):
        """`Speichern` fires the same toast whether or not the write behind
        it succeeded — a field `_render_contact_form` dropped on the way to
        `WF.update` would only surface the next time someone reopened this
        exact lead and found their own note missing."""
        first = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 1",
            sqlite3.connect(self.database),
        ).iloc[0]
        bfs, parcel = int(first["bfs"]), str(first["parcel"])
        key = [(bfs, parcel)]
        workflow.set_saved(key, True, self.database)
        slug = f"{bfs}_{parcel}"

        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        ).run()

        field(app, "Kontaktperson").set_value("Frau Meier")
        field(app, "Telefon").set_value("+41 79 000 00 00")
        field(app, "Wiedervorlage").set_value("2026-09-15")
        app.text_area[0].set_value("Rückruf nächste Woche vereinbart.")
        app.button(key=f"FormSubmitter:contact_{slug}-Speichern").click().run()
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
        them, but only because `_render_contact_form` stops on the
        `ValueError` instead of ignoring it. A save that let the good fields
        through anyway would leave a contact name typed today sitting next
        to a Wiedervorlage date nobody actually entered."""
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
        slug = f"{bfs}_{parcel}"

        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        ).run()

        field(app, "Wiedervorlage").set_value("02.09.2026")
        field(app, "Kontaktperson").set_value("Neuer Name")
        app.button(key=f"FormSubmitter:contact_{slug}-Speichern").click().run()
        self.assertFalse(app.exception)

        self.assertEqual(len(app.error), 1)
        self.assertEqual(
            app.error[0].value,
            "due_date must be an ISO date (YYYY-MM-DD) or empty",
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
        in the same form — a copy-paste of the wrong boolean into
        `WF.set_saved` would silently keep a declined lead on the board
        instead of taking it off."""
        first = pd.read_sql_query(
            "SELECT bfs, parcel FROM parcel_results LIMIT 1",
            sqlite3.connect(self.database),
        ).iloc[0]
        bfs, parcel = int(first["bfs"]), str(first["parcel"])
        key = [(bfs, parcel)]
        workflow.set_saved(key, True, self.database)
        slug = f"{bfs}_{parcel}"

        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        ).run()

        remove_key = f"FormSubmitter:contact_{slug}-Von Merkliste entfernen"
        app.button(key=remove_key).click().run()
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
        slug = f"{bfs}_{parcel}"

        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=60
        ).run()

        app.button(key=f"open_{slug}").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(
            app.session_state["selected_parcel_id"], f"{bfs}:{parcel}"
        )


if __name__ == "__main__":
    unittest.main()
