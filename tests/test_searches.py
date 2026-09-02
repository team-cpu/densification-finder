import os
import sqlite3
import tempfile
import unittest

import ingest
import searches


class SavedSearchTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.tempdir.name, "results.sqlite")
        with sqlite3.connect(self.database) as connection:
            ingest.schema(connection)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_a_saved_search_round_trips_filters_intact_and_typed(self):
        filters = {
            "screening_query": "Seestrasse",
            "screening_min_delta": 800,
            "screening_area": [300, float("inf")],
            "screening_municipality": ["Horgen"],
            "screening_hide_transport": True,
            "screening_ziffer": None,
        }
        searches.save("Wohnzone Horgen", filters, self.database)

        rows = searches.load(self.database)
        self.assertEqual(len(rows), 1)
        row = rows.iloc[0]
        self.assertEqual(row["name"], "Wohnzone Horgen")
        self.assertEqual(row["filters"], filters)
        self.assertIsInstance(row["filters"]["screening_min_delta"], int)
        self.assertIsInstance(row["filters"]["screening_hide_transport"], bool)
        self.assertTrue(row["created_at"])

    def test_saving_the_same_name_twice_replaces_rather_than_duplicates(self):
        searches.save("Wohnzone Horgen", {"screening_query": "a"}, self.database)
        searches.save("Wohnzone Horgen", {"screening_query": "b"}, self.database)

        rows = searches.load(self.database)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.iloc[0]["filters"], {"screening_query": "b"})

    def test_an_empty_name_is_rejected(self):
        with self.assertRaises(ValueError):
            searches.save("", {"screening_query": "x"}, self.database)
        with self.assertRaises(ValueError):
            searches.save("   ", {"screening_query": "x"}, self.database)

    def test_a_name_over_the_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            searches.save("x" * 101, {"screening_query": "x"}, self.database)
        # Exactly at the limit is still accepted — the boundary is "over 100",
        # not "100 or more".
        searches.save("x" * 100, {"screening_query": "x"}, self.database)
        self.assertEqual(len(searches.load(self.database)), 1)

    def test_an_unserialisable_filter_value_is_rejected(self):
        """A set has no JSON representation. Caught here, as a ValueError, is
        the difference between a message the Suche-speichern button can show
        and a raw TypeError reaching the page."""
        with self.assertRaises(ValueError):
            searches.save(
                "Broken", {"screening_area": {300, 5000}}, self.database
            )

    def test_delete_removes_one_and_leaves_the_others(self):
        searches.save("A", {"screening_query": "a"}, self.database)
        searches.save("B", {"screening_query": "b"}, self.database)

        removed = searches.delete("A", self.database)
        self.assertEqual(removed, 1)

        rows = searches.load(self.database)
        self.assertEqual(list(rows["name"]), ["B"])

    def test_deleting_a_name_that_does_not_exist_removes_nothing(self):
        searches.save("A", {"screening_query": "a"}, self.database)
        removed = searches.delete("does not exist", self.database)
        self.assertEqual(removed, 0)
        self.assertEqual(len(searches.load(self.database)), 1)

    def test_a_row_with_corrupt_json_is_skipped_not_fatal(self):
        """A hand-edited database or a future release that changes the
        payload shape must not take the whole picker down with it — one
        unreadable row is dropped, every other saved search still loads."""
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO saved_searches (name, filters) VALUES (?, ?)",
                ("Broken", "{not json"),
            )
        searches.save("Good", {"screening_query": "ok"}, self.database)

        rows = searches.load(self.database)
        self.assertEqual(list(rows["name"]), ["Good"])


if __name__ == "__main__":
    unittest.main()
