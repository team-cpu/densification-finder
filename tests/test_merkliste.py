import unittest

import pandas as pd

import merkliste


def shortlist_frame(rows):
    return pd.DataFrame(
        rows,
        columns=["bfs", "parcel", "area", "delta", "contact_status"],
    )


class SummaryTest(unittest.TestCase):
    def test_the_tiles_agree_with_the_rows_beneath_them(self):
        """A tile that disagrees with its own table is worse than no tile: it
        is the number someone quotes without scrolling."""
        leads = shortlist_frame(
            [
                (4001, "1", 1000.0, 500.0, "contacted"),
                (4002, "2", 2000.0, 700.0, "in_discussion"),
            ]
        )

        summary = merkliste.summary(leads, land_value=lambda row: row["area"] * 10)

        self.assertEqual(summary["parcels"], 2)
        self.assertEqual(summary["potential"], 1200.0)
        self.assertEqual(summary["land_value"], 30000.0)

    def test_in_dialog_counts_only_the_stages_that_are_conversations(self):
        """Neither an untouched lead nor a refusal is a dialogue; counting
        either would make the tile read as progress that has not happened."""
        leads = shortlist_frame(
            [
                (4001, "1", 100.0, 1.0, "not_contacted"),
                (4002, "2", 100.0, 1.0, "contacted"),
                (4003, "3", 100.0, 1.0, "in_discussion"),
                (4004, "4", 100.0, 1.0, "meeting_scheduled"),
                (4005, "5", 100.0, 1.0, "declined"),
            ]
        )

        summary = merkliste.summary(leads, land_value=lambda row: 0.0)

        self.assertEqual(summary["in_dialog"], 3)

    def test_an_empty_shortlist_reports_zeroes_rather_than_raising(self):
        summary = merkliste.summary(shortlist_frame([]), land_value=lambda row: 0.0)

        self.assertEqual(
            (summary["parcels"], summary["potential"], summary["land_value"],
             summary["in_dialog"]),
            (0, 0.0, 0.0, 0),
        )

    def test_a_parcel_without_a_price_reference_does_not_poison_the_total(self):
        """`price_of` returns None where no reference matches. Summing that as
        NaN would blank the whole tile because of one unpriced parcel."""
        leads = shortlist_frame(
            [
                (4001, "1", 1000.0, 100.0, "contacted"),
                (4002, "2", 2000.0, 100.0, "contacted"),
            ]
        )

        summary = merkliste.summary(
            leads,
            land_value=lambda row: None if row["bfs"] == 4002 else 5000.0,
        )

        self.assertEqual(summary["land_value"], 5000.0)


if __name__ == "__main__":
    unittest.main()
