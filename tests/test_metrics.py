import unittest

import metrics


class ZoneUseTest(unittest.TestCase):
    def test_residential_and_mixed_zones_are_development_targets(self):
        canton = metrics.CANTONS["AG"]
        residential = metrics.read_zone(
            canton,
            {"AZmax": 0.6, "HNCode": 11, "GDEBez": "Wohnzone"},
        )
        mixed = metrics.read_zone(
            canton,
            {"AZmax": 0.8, "HNCode": 13, "GDEBez": "Mischzone"},
        )
        self.assertTrue(residential["residential"])
        self.assertTrue(mixed["residential"])

    def test_employment_zone_is_not_a_vacant_residential_lead(self):
        zone = metrics.read_zone(
            metrics.CANTONS["AG"],
            {"AZmax": 1.0, "HNCode": 12, "GDEBez": "Arbeitszone"},
        )
        self.assertFalse(zone["residential"])


if __name__ == "__main__":
    unittest.main()
