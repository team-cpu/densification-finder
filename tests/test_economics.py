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
        reserve_pct=15.0,
    )

    def path(self, **overrides):
        return {s.label: s.value for s in E.residual(**dict(self.BASE, **overrides))}

    def test_worked_example(self):
        by_label = self.path()
        # 1000 m² GF × 80% = 800 m² saleable — the sale price sees this…
        self.assertEqual(by_label["Verkaufsfläche"], 800.0)
        self.assertEqual(by_label["Verkaufserlös"], 6_400_000.0)
        # …while the construction cost is incurred on all 1000 m².
        self.assertEqual(by_label["− Baukosten (BKP 2)"], -3_000_000.0)
        self.assertEqual(by_label["− Baunebenkosten"], -450_000.0)
        self.assertEqual(by_label["− Abbruchkosten"], -30_000.0)
        # 3% of 3,000,000 + 450,000 + 30,000
        self.assertEqual(by_label["− Finanzierungskosten"], -104_400.0)
        # 15% of the cost estimate, not of the revenue
        self.assertEqual(by_label["− Reserve / Unvorhergesehenes"], -537_660.0)
        self.assertEqual(by_label["= Residualer Landwert"], 2_277_940.0)

    def test_the_sale_share_leaves_the_construction_cost_alone(self):
        """Philipp, 18.08.2026: sale price on 80% of the floor area, construction
        cost on 100%. Halving the share must move the revenue and nothing else."""
        wide, narrow = self.path(sale_area_pct=80.0), self.path(sale_area_pct=40.0)
        self.assertEqual(narrow["Verkaufserlös"], wide["Verkaufserlös"] / 2)
        self.assertEqual(narrow["− Baukosten (BKP 2)"], wide["− Baukosten (BKP 2)"])

    def test_the_reserve_is_a_contingency_on_cost_not_a_margin_on_revenue(self):
        """It covers the long winter and the neighbour who objects, so a better
        sale price must not enlarge it."""
        cheap, dear = self.path(), self.path(sale_price_chf_m2=16_000.0)
        self.assertEqual(cheap["− Reserve / Unvorhergesehenes"],
                         dear["− Reserve / Unvorhergesehenes"])
        costlier = self.path(construction_chf_m2=6_000.0)
        self.assertLess(costlier["− Reserve / Unvorhergesehenes"],
                        cheap["− Reserve / Unvorhergesehenes"])

    def test_the_path_adds_up_to_its_own_bottom_line(self):
        """The document has to be checkable line by line: the printed steps must
        be the ones the total is made of, not a parallel calculation."""
        steps = E.residual(**self.BASE)
        money = [s for s in steps if s.unit == "CHF" and s.kind != "result"]
        self.assertAlmostEqual(sum(s.value for s in money), E.land_value(steps), places=6)

    def test_the_formula_shown_is_the_formula_evaluated(self):
        """The whole point of the rewrite: the tooltip is not a description kept
        beside the arithmetic, it is the arithmetic."""
        step = next(s for s in E.residual(**self.BASE) if s.label == "Verkaufserlös")
        self.assertEqual(step.expr, "verkaufsflaeche * verkaufspreis")
        self.assertEqual(step.formula, "800 * 8’000")
        self.assertEqual(
            eval(step.expr, {"__builtins__": {}},  # noqa: S307 — our own literal
                 {"verkaufsflaeche": 800.0, "verkaufspreis": 8000.0}),
            step.value,
        )

    def test_every_rule_only_reaches_inputs_and_earlier_rules(self):
        """A rule that mentions a symbol nothing has defined yet would raise at
        the first parcel rather than at import."""
        known = set(E.INPUTS)
        for rule in E.PATH:
            for symbol in E._NAME.findall(rule.expr):
                self.assertIn(symbol, known, f"{rule.key} reaches unknown {symbol}")
            known.add(rule.key)

    def test_used_in_reads_the_formulas(self):
        self.assertEqual(E.used_in("verkaufspreis"), ["Verkaufserlös"])
        self.assertIn("Baukosten (BKP 2)", E.used_in("baukosten_pro_m2"))
        self.assertEqual(E.used_in("nothing_at_all"), [])

    def test_no_existing_building_means_no_demolition(self):
        by_label = self.path(existing_gf=0.0)
        self.assertEqual(by_label["− Abbruchkosten"], 0.0)
        self.assertEqual(by_label["− Finanzierungskosten"], -103_500.0)

    def test_demolition_can_be_switched_off_on_a_built_parcel(self):
        """An extension or a top-up keeps the building standing, so the checkbox
        has to remove the cost rather than only hide the line."""
        kept = self.path(demolish=False)
        self.assertEqual(kept["− Abbruchkosten"], 0.0)
        self.assertGreater(kept["= Residualer Landwert"],
                           self.path()["= Residualer Landwert"])

    def test_per_square_metre_and_units(self):
        steps = E.residual(**self.BASE)
        self.assertAlmostEqual(E.per_square_metre(steps, 1000.0), 2277.94, places=4)
        self.assertIsNone(E.per_square_metre(steps, 0.0))
        self.assertAlmostEqual(E.units(900.0, 90.0), 10.0)
        self.assertIsNone(E.units(900.0, 0.0))

    def test_a_hopeless_parcel_returns_a_negative_land_value(self):
        """Screening only works if the answer is allowed to be 'not worth it'."""
        self.assertLess(self.path(sale_price_chf_m2=2000.0)["= Residualer Landwert"], 0)

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
        # Answered on 18.08.2026, so no longer carrying the mark.
        for key in ("reserve_pct", "sale_area_pct"):
            self.assertTrue(E.BENCHMARKS[key].confirmed, key)

    def test_swiss_formatting(self):
        self.assertEqual(E.chf(2_566_300), "2’566’300")
        self.assertEqual(E.chf(-1500.4), "-1’500")
        self.assertEqual(E.chf(None), "—")

    def test_manual_construction_recalculates_dependent_costs(self):
        steps = E.residual(**self.BASE, overrides={"baukosten": 2_000_000})
        by_key = {s.key: s for s in steps}
        self.assertEqual(by_key["baukosten"].value, -2_000_000)
        self.assertEqual(by_key["baunebenkosten"].value, -300_000)
        self.assertEqual(by_key["finanzierung"].value, -69_900)
        self.assertEqual(by_key["reserve"].value, -359_985)
        self.assertEqual(E.land_value(steps), 3_640_115)
        self.assertEqual(by_key["baukosten"].formula, "Manuell überschrieben")
        self.assertEqual(by_key["baukosten"].expr, "")
        self.assertTrue(by_key["baukosten"].overridden)
        self.assertFalse(by_key["reserve"].overridden)
        self.assertIn("2’000’000", by_key["baunebenkosten"].formula)

    def test_multiple_manual_rows_and_zero_still_add_up(self):
        steps = E.residual(**self.BASE, overrides={
            "verkaufserloes": 2_000_000, "baukosten": 1_000_000,
            "baunebenkosten": 0, "reserve": 10_000,
        })
        by_key = {s.key: s.value for s in steps}
        self.assertEqual(by_key["baunebenkosten"], 0)
        self.assertEqual(by_key["finanzierung"], -30_900)
        self.assertEqual(by_key["reserve"], -10_000)
        self.assertEqual(E.land_value(steps), 929_100)
        self.assertEqual(sum(s.value for s in steps if s.kind in ("cost", "revenue")),
                         E.land_value(steps))
        self.assertEqual(E.residual(**self.BASE, overrides={}), E.residual(**self.BASE))

    def test_manual_amounts_validate_without_changing_the_formula_model(self):
        for bad in ([], {"landwert": 1}, {"verkaufsflaeche": 1},
                    {"baukosten": -1}, {"baukosten": True}, {"baukosten": "100"},
                    {"baukosten": float("nan")}, {"baukosten": float("inf")},
                    {"baukosten": 10 ** 1000}):
            with self.subTest(bad=type(bad)):
                with self.assertRaises(ValueError):
                    E.residual(**self.BASE, overrides=bad)

    def test_parse_manual_swiss_amounts_and_clear(self):
        for raw, amount in (("CHF −1’234’567", 1_234_567),
                            ("1'234,50", 1234.5), (" 0 ", 0),
                            ("+1 234.5", 1234.5), ("", None), ("  ", None)):
            with self.subTest(raw=raw):
                self.assertEqual(E.parse_override(raw), amount)
        for raw in (None, 123, "NaN", "Infinity", "12xyz", "1e8", "1.2.3",
                    "1000000000001", "1" * 101, "<script>"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    E.parse_override(raw)


if __name__ == "__main__":
    unittest.main()
