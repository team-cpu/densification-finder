import unittest
from unittest import mock

from shapely.geometry import box
from shapely.strtree import STRtree

import cascade
import land_cover as LC


BFS = 4001


def make_engine():
    """An Engine with the heavy layer loading bypassed, for a single municipality."""
    engine = cascade.Engine.__new__(cascade.Engine)
    engine.built_after = 2020
    engine.min_delta = 0.0
    engine.min_area = 0.0
    engine.max_area = float("inf")
    engine.exclude_inventory = False
    engine._zones = {
        BFS: [
            {
                "metric": "AZ",
                "value": 0.5,
                "floors": None,
                "name": "Wohnzone A",
                "residential": True,
                "geom": box(0, 0, 20, 10),
            }
        ]
    }
    engine._labels = {}
    engine._addresses = {}
    engine._gwr = {}
    engine._occupied_egrids = {}
    engine.heritage = STRtree([])
    engine.tiers = {}
    engine.freezes = None
    engine.plans = None
    return engine


def make_parcels():
    return [
        {"nummer": "100", "egrid": "EG1", "area": 100.0, "geom": box(0, 0, 10, 10)},
        {"nummer": "200", "egrid": "EG2", "area": 100.0, "geom": box(10, 0, 20, 10)},
    ]


class TransportExclusionTest(unittest.TestCase):
    def run_engine(self, transport_surfaces):
        engine = make_engine()
        with mock.patch.object(cascade, "load_parcels", return_value=make_parcels()), \
                mock.patch.object(LC, "load", return_value=transport_surfaces):
            return engine.run(BFS)

    def test_transport_parcel_is_not_a_candidate(self):
        result = self.run_engine([box(0, 0, 10, 10)])  # fully covers parcel 100

        self.assertEqual([c["parcel"] for c in result["candidates"]], ["200"])
        candidate = result["candidates"][0]
        self.assertEqual(candidate["transport_share"], 0.0)

    def test_unknown_transport_coverage_does_not_exclude(self):
        # LC.load returns None when the layer is unavailable; that must keep
        # every parcel eligible instead of reading "unknown" as "road".
        result = self.run_engine(None)

        self.assertEqual(
            sorted(c["parcel"] for c in result["candidates"]), ["100", "200"]
        )
        self.assertTrue(
            all(c["transport_share"] is None for c in result["candidates"])
        )


if __name__ == "__main__":
    unittest.main()
