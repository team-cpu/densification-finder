import unittest

import economics as E


class ResidualValueTest(unittest.TestCase):
    #: Round numbers, so the expected result can be checked by hand.
    BASE = dict(
        potential_gf=1000.0,
        sale_area_pct=80.0,
        sale_price_chf_m2=8000.0,
        construction_chf_m2=3000.0,
        ancillary_pct=15.0,
        existing_gf=200.0,
        demolition_chf_m2=150.0,
        financing_pct=3.0,
        profit_pct=15.0,
    )

    def test_worked_example(self):
        steps = E.residual(**self.BASE)
        by_label = {s.label: s.value for s in steps}

        # 1000 m² GF × 80% = 800 m² saleable
        self.assertEqual(by_label["Verkaufsfläche"], 800.0)
        # 800 × 8000
        self.assertEqual(by_label["Verkaufserlös"], 6_400_000.0)
        # 800 × 3000
        self.assertEqual(by_label["− Baukosten (BKP 2)"], -2_400_000.0)
        self.assertEqual(by_label["− Baunebenkosten"], -360_000.0)
        self.assertEqual(by_label["− Abbruchkosten"], -30_000.0)
        # 3% of 2,400,000 + 360,000 + 30,000
        self.assertEqual(by_label["− Finanzierungskosten"], -83_700.0)
        self.assertEqual(by_label["− Gewinn / Risiko"], -960_000.0)
        self.assertEqual(by_label["= Residualer Landwert"], 2_566_300.0)

    def test_the_path_adds_up_to_its_own_bottom_line(self):
        """The document has to be checkable line by line: the printed steps must
        be the ones the total is made of, not a parallel calculation."""
        steps = E.residual(**self.BASE)
        money = [s for s in steps if s.unit == "CHF" and s.kind != "result"]
        self.assertAlmostEqual(sum(s.value for s in money), E.land_value(steps), places=6)

    def test_no_existing_building_means_no_demolition(self):
        vacant = dict(self.BASE, existing_gf=0.0)
        steps = E.residual(**vacant)
        by_label = {s.label: s.value for s in steps}
        self.assertEqual(by_label["− Abbruchkosten"], 0.0)
        # And the financing base drops with it.
        self.assertEqual(by_label["− Finanzierungskosten"], -82_800.0)

    def test_demolition_can_be_switched_off_on_a_built_parcel(self):
        """An extension or a top-up keeps the building standing, so the checkbox
        has to remove the cost rather than only hide the line."""
        steps = E.residual(**dict(self.BASE, demolish=False))
        by_label = {s.label: s.value for s in steps}
        self.assertEqual(by_label["− Abbruchkosten"], 0.0)
        self.assertIn("kein Abbruch", dict((s.label, s.formula) for s in steps)["− Abbruchkosten"])
        self.assertGreater(E.land_value(steps), E.land_value(E.residual(**self.BASE)))

    def test_revenue_and_construction_share_one_area_basis(self):
        """Reckoning revenue on saleable area and cost on gross floor area would
        overstate the land value by the difference — the mistake this guards."""
        steps = E.residual(**dict(self.BASE, sale_area_pct=100.0))
        by_label = {s.label: s.value for s in steps}
        self.assertEqual(by_label["Verkaufsfläche"], 1000.0)
        self.assertEqual(by_label["Verkaufserlös"], 8_000_000.0)
        self.assertEqual(by_label["− Baukosten (BKP 2)"], -3_000_000.0)

    def test_per_square_metre_and_units(self):
        steps = E.residual(**self.BASE)
        self.assertAlmostEqual(E.per_square_metre(steps, 1000.0), 2566.3, places=4)
        self.assertIsNone(E.per_square_metre(steps, 0.0))
        self.assertAlmostEqual(E.units(900.0, 90.0), 10.0)
        self.assertIsNone(E.units(900.0, 0.0))

    def test_a_hopeless_parcel_returns_a_negative_land_value(self):
        """Screening only works if the answer is allowed to be 'not worth it'."""
        steps = E.residual(**dict(self.BASE, sale_price_chf_m2=3000.0))
        self.assertLess(E.land_value(steps), 0)

    def test_every_unconfirmed_benchmark_says_so(self):
        """The brief asks for the real construction cost to be confirmed with
        Philipp rather than guessed. The defaults are cited, not invented, and
        each carries its source and a confirmation mark until he names his own."""
        for key, mark in E.BENCHMARKS.items():
            self.assertTrue(mark.source, key)
            if not mark.confirmed:
                self.assertIn("zu bestätigen", mark.provenance, key)
        for key in ("sale_price_chf_m2", "construction_chf_m2", "demolition_chf_m2"):
            self.assertFalse(E.BENCHMARKS[key].confirmed, key)
            self.assertTrue(E.BENCHMARKS[key].url, key)

    def test_swiss_formatting(self):
        self.assertEqual(E.chf(2_566_300), "2’566’300")
        self.assertEqual(E.chf(-1500.4), "-1’500")
        self.assertEqual(E.chf(None), "—")


if __name__ == "__main__":
    unittest.main()
