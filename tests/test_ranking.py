import unittest

import pandas as pd

from ranking import rank_candidates


class RankingTest(unittest.TestCase):
    def setUp(self):
        self.candidates = pd.DataFrame(
            [
                {"id": "b-low", "buildings": 1, "delta": 200, "existing": 200},
                {"id": "b-high", "buildings": 1, "delta": 300, "existing": 100},
                {"id": "v-low", "buildings": 0, "delta": 400, "existing": 0},
                {"id": "v-high", "buildings": 0, "delta": 800, "existing": 0},
            ]
        )

    def test_built_parcels_rank_by_relative_gain(self):
        ranked = rank_candidates(self.candidates, "Bebaut")
        self.assertEqual(list(ranked["id"]), ["b-high", "b-low"])

    def test_vacant_parcels_rank_by_absolute_potential(self):
        ranked = rank_candidates(self.candidates, "Unbebaut")
        self.assertEqual(list(ranked["id"]), ["v-high", "v-low"])

    def test_all_interleaves_built_and_vacant_rankings(self):
        ranked = rank_candidates(self.candidates, "Alle")
        self.assertEqual(
            list(ranked["id"]), ["b-high", "v-high", "b-low", "v-low"]
        )

    def test_all_handles_a_single_available_type(self):
        vacant = self.candidates[self.candidates["buildings"] == 0]
        ranked = rank_candidates(vacant, "Alle")
        self.assertEqual(list(ranked["id"]), ["v-high", "v-low"])


if __name__ == "__main__":
    unittest.main()
