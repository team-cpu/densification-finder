import unittest

import pandas as pd

from ranking import rank_candidates


class RankingTest(unittest.TestCase):
    def setUp(self):
        self.candidates = pd.DataFrame(
            [
                {"id": "b-low", "buildings": 1, "delta": 500, "existing": 1000},
                {"id": "b-high", "buildings": 1, "delta": 300, "existing": 100},
                {"id": "v-low", "buildings": 0, "delta": 400, "existing": 0},
                {"id": "v-high", "buildings": 0, "delta": 800, "existing": 0},
            ]
        )

    def test_built_parcels_rank_by_absolute_potential_not_relative_gain(self):
        ranked = rank_candidates(self.candidates, "Bebaut")
        self.assertEqual(list(ranked["id"]), ["b-low", "b-high"])

    def test_vacant_parcels_rank_by_absolute_potential(self):
        ranked = rank_candidates(self.candidates, "Unbebaut")
        self.assertEqual(list(ranked["id"]), ["v-high", "v-low"])

    def test_all_ranks_built_and_vacant_together_by_absolute_potential(self):
        ranked = rank_candidates(self.candidates, "Alle")
        self.assertEqual(
            list(ranked["id"]), ["v-high", "b-low", "v-low", "b-high"]
        )

    def test_all_handles_a_single_available_type(self):
        vacant = self.candidates[self.candidates["buildings"] == 0]
        ranked = rank_candidates(vacant, "Alle")
        self.assertEqual(list(ranked["id"]), ["v-high", "v-low"])

    def test_equal_potential_is_stable_and_source_is_unchanged(self):
        self.candidates["delta"] = 300
        original = self.candidates.copy(deep=True)
        ranked = rank_candidates(self.candidates, "Alle")
        self.assertEqual(list(ranked["id"]), list(original["id"]))
        pd.testing.assert_frame_equal(self.candidates, original)

    def test_empty_candidates_and_unknown_type(self):
        self.assertTrue(rank_candidates(self.candidates.iloc[:0], "Alle").empty)
        with self.assertRaises(ValueError):
            rank_candidates(self.candidates, "Invalid")


if __name__ == "__main__":
    unittest.main()
