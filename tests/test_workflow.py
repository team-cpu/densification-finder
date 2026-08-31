import os
import sqlite3
import tempfile
import unittest

import ingest
import workflow


class ParcelWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.tempdir.name, "results.sqlite")
        with sqlite3.connect(self.database) as connection:
            ingest.schema(connection)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_batch_updates_are_persistent_and_keep_unrelated_fields(self):
        keys = [(4209, "3967"), (4001, "12")]

        self.assertEqual(workflow.set_saved(keys, True, self.database), 2)
        workflow.set_hidden([(4209, "3967")], True, self.database)
        workflow.set_contact_status(
            [(4001, "12")], "meeting_scheduled", self.database
        )
        workflow.set_owner_name([(4001, "12")], "Muster Immobilien AG", self.database)

        states = workflow.load(self.database).set_index(["bfs", "parcel"])
        self.assertEqual(states.loc[(4209, "3967"), "saved"], 1)
        self.assertEqual(states.loc[(4209, "3967"), "hidden"], 1)
        self.assertEqual(
            states.loc[(4209, "3967"), "contact_status"], "not_contacted"
        )
        self.assertEqual(states.loc[(4001, "12"), "saved"], 1)
        self.assertEqual(
            states.loc[(4001, "12"), "contact_status"], "meeting_scheduled"
        )
        self.assertEqual(
            states.loc[(4001, "12"), "owner_name"], "Muster Immobilien AG"
        )

    def test_workflow_survives_replacement_of_calculated_results(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO parcel_results (bfs, parcel, delta) VALUES (4209, '3967', 1)"
            )
        workflow.set_saved([(4209, "3967")], True, self.database)

        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM parcel_results WHERE bfs = 4209")
            connection.execute(
                "INSERT INTO parcel_results (bfs, parcel, delta) VALUES (4209, '3967', 2)"
            )

        state = workflow.load(self.database).iloc[0]
        self.assertEqual(
            (state["bfs"], state["parcel"], state["saved"]),
            (4209, "3967", 1),
        )

    def test_unknown_contact_status_is_rejected(self):
        with self.assertRaises(ValueError):
            workflow.set_contact_status(
                [(4209, "3967")], "maybe_later", self.database
            )

    def test_owner_name_is_trimmed_and_bounded(self):
        workflow.set_owner_name(
            [(4209, "3967")], "  Muster AG  ", self.database
        )
        self.assertEqual(
            workflow.load(self.database).iloc[0]["owner_name"], "Muster AG"
        )
        with self.assertRaises(ValueError):
            workflow.set_owner_name(
                [(4209, "3967")], "x" * 201, self.database
            )

    def test_the_new_stage_is_stored_and_the_order_is_the_board_order(self):
        workflow.set_contact_status(
            [(4001, "12")], "in_discussion", self.database
        )
        self.assertEqual(
            workflow.load(self.database).iloc[0]["contact_status"],
            "in_discussion",
        )
        self.assertEqual(
            list(workflow.CONTACT_STATUS_LABELS),
            [
                "not_contacted",
                "contacted",
                "in_discussion",
                "meeting_scheduled",
                "declined",
            ],
        )

    def test_acquisition_fields_survive_a_round_trip(self):
        workflow.update(
            [(4001, "12")],
            owner_name="Erbengemeinschaft Weber",
            contact_person="Dr. T. Weber, Erbenvertreter",
            phone="+41 44 712 08 41",
            email="t.weber@example.ch",
            last_contact="2026-08-21",
            due_date="2026-09-04",
            next_step="Zweitgespräch, Wertindikation vorlegen",
            note="Drei Erben, einer im Ausland",
            db=self.database,
        )

        state = workflow.load(self.database).iloc[0]
        self.assertEqual(state["contact_person"], "Dr. T. Weber, Erbenvertreter")
        self.assertEqual(state["phone"], "+41 44 712 08 41")
        self.assertEqual(state["email"], "t.weber@example.ch")
        self.assertEqual(state["last_contact"], "2026-08-21")
        self.assertEqual(state["due_date"], "2026-09-04")
        self.assertEqual(state["note"], "Drei Erben, einer im Ausland")

    def test_an_omitted_field_keeps_its_stored_value(self):
        workflow.update(
            [(4001, "12")], note="Wohnsitz vermutlich Zug", db=self.database
        )
        workflow.update([(4001, "12")], due_date="2026-09-02", db=self.database)

        state = workflow.load(self.database).iloc[0]
        self.assertEqual(state["note"], "Wohnsitz vermutlich Zug")
        self.assertEqual(state["due_date"], "2026-09-02")

    def test_a_date_can_be_cleared_but_not_malformed(self):
        workflow.update([(4001, "12")], due_date="2026-09-02", db=self.database)
        workflow.update([(4001, "12")], due_date="", db=self.database)
        self.assertEqual(workflow.load(self.database).iloc[0]["due_date"], "")

        for bad in ("02.09.2026", "2026-9-2", "20260902", "morgen"):
            with self.assertRaises(ValueError, msg=bad):
                workflow.update([(4001, "12")], due_date=bad, db=self.database)

    def test_an_impossible_calendar_date_is_rejected(self):
        """Right shape, wrong day. A regex alone would let this through."""
        with self.assertRaises(ValueError):
            workflow.update([(4001, "12")], due_date="2026-02-30", db=self.database)

    def test_acquisition_text_is_trimmed_and_bounded(self):
        workflow.update(
            [(4001, "12")], next_step="  Brief aufsetzen  ", db=self.database
        )
        self.assertEqual(
            workflow.load(self.database).iloc[0]["next_step"], "Brief aufsetzen"
        )
        with self.assertRaises(ValueError):
            workflow.update([(4001, "12")], note="x" * 1001, db=self.database)
        with self.assertRaises(ValueError):
            workflow.update([(4001, "12")], phone="x" * 51, db=self.database)


if __name__ == "__main__":
    unittest.main()
