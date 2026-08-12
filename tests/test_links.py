import unittest

import links


class ParcelLinksTest(unittest.TestCase):
    def test_lv95_to_wgs84_matches_swisstopo_example(self):
        latitude, longitude = links.lv95_to_wgs84(2_700_000, 1_100_000)
        self.assertAlmostEqual(latitude, 46.0441, places=4)
        self.assertAlmostEqual(longitude, 8.7305, places=4)

    def test_google_links_are_api_urls(self):
        map_url = links.google_map_link(2_648_061.94, 1_250_107.32)
        street_url = links.google_street_view_link(2_648_061.94, 1_250_107.32)
        self.assertIn("api=1", map_url)
        self.assertIn("query=", map_url)
        self.assertIn("map_action=pano", street_url)
        self.assertIn("viewpoint=", street_url)

    def test_missing_coordinates_have_no_link(self):
        self.assertIsNone(links.agis_link(None, 1_200_000))
        self.assertIsNone(links.google_map_link(float("nan"), 1_200_000))

    def test_street_view_uses_parcel_fallback_for_old_database_rows(self):
        url = links.google_street_view_link(
            None, None, 2_648_061.94, 1_250_107.32
        )
        self.assertIn("map_action=pano", url)
        self.assertIn("viewpoint=", url)


if __name__ == "__main__":
    unittest.main()
