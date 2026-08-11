import os
import tempfile
import unittest

import land_prices


class LandPricesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
        self.temp.write(
            "bfs,municipality,zone_pattern,price_chf_m2,source,source_url,as_of,note\n"
            ",,,950,Canton,,,\n"
            "4001,Aarau,,1200,Municipality,,,\n"
            "4001,Aarau,*wohnen*,1350,Zone,,,\n"
        )
        self.temp.close()
        self.references = land_prices.load(self.temp.name)

    def tearDown(self):
        os.unlink(self.temp.name)

    def test_most_specific_reference_wins(self):
        ref = land_prices.resolve(
            self.references, 4001, "Aarau", "Zone Wohnen dreigeschossig"
        )
        self.assertEqual(ref.price_chf_m2, 1350)

    def test_municipality_reference_beats_fallback(self):
        ref = land_prices.resolve(self.references, 4001, "Aarau", "Kernzone")
        self.assertEqual(ref.price_chf_m2, 1200)

    def test_canton_fallback_is_available(self):
        ref = land_prices.resolve(self.references, 4029, "Biberstein", "Wohnzone")
        self.assertEqual(ref.price_chf_m2, 950)


if __name__ == "__main__":
    unittest.main()
