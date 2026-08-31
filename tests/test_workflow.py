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


if __name__ == "__main__":
    unittest.main()
