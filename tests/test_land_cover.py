import os
import tempfile
import unittest
from unittest import mock

from shapely.geometry import box

import land_cover


SAMPLE = """<?xml version="1.0"?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"
 xmlns:ms="http://mapserver.gis.umn.edu/mapserver"
 xmlns:gml="http://www.opengis.net/gml/3.2">
 <wfs:member><ms:LCSF>
  <ms:Art>Strasse_Weg</ms:Art>
  <ms:msGeometry><gml:Polygon><gml:exterior><gml:LinearRing>
   <gml:posList>0 0 8 0 8 10 0 10 0 0</gml:posList>
  </gml:LinearRing></gml:exterior></gml:Polygon></ms:msGeometry>
 </ms:LCSF></wfs:member>
 <wfs:member><ms:LCSF>
  <ms:Art>Gartenanlage</ms:Art>
  <ms:msGeometry><gml:Polygon><gml:exterior><gml:LinearRing>
   <gml:posList>8 0 10 0 10 10 8 10 8 0</gml:posList>
  </gml:LinearRing></gml:exterior></gml:Polygon></ms:msGeometry>
 </ms:LCSF></wfs:member>
</wfs:FeatureCollection>
"""


class LandCoverTest(unittest.TestCase):
    def test_only_official_transport_classes_are_loaded_and_measured(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            land_cover.paths, "DATA", directory
        ):
            path = land_cover.cache_path(4001)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(SAMPLE)
            surfaces = land_cover.load(4001)

        self.assertEqual(len(surfaces), 1)
        share = land_cover.TransportSurfaceIndex(surfaces).share(box(0, 0, 10, 10))
        self.assertAlmostEqual(share, 0.8)
        self.assertGreaterEqual(share, land_cover.MIN_TRANSPORT_SHARE)

    def test_missing_layer_stays_unknown_instead_of_becoming_non_transport(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            land_cover.paths, "DATA", directory
        ):
            self.assertIsNone(land_cover.load(4001))

    def test_wfs_filter_requests_only_transport_land_cover(self):
        query = land_cover._filter(4254)
        self.assertIn("<fes:Literal>4254</fes:Literal>", query)
        for kind in land_cover.TRANSPORT_TYPES:
            self.assertIn(f"<fes:Literal>{kind}</fes:Literal>", query)


if __name__ == "__main__":
    unittest.main()
