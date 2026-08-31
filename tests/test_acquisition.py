import unittest

import pandas as pd

import acquisition
import workflow


def leads_frame(rows):
    """A saved-leads frame with the columns the board reads.

    Built by hand rather than through the database so that the ordering rules
    can be stated as data: the point of these tests is the sort, not the join.
    """
    return pd.DataFrame(
        rows,
        columns=[
            "bfs",
            "parcel",
            "municipality",
            "contact_status",
            "due_date",
        ],
    )


class OverdueTest(unittest.TestCase):
    def test_a_lead_due_today_is_not_yet_overdue(self):
        leads = leads_frame(
            [
                (4001, "1", "Aarau", "contacted", "2026-08-30"),
                (4002, "2", "Baden", "contacted", "2026-08-31"),
                (4003, "3", "Brugg", "contacted", "2026-09-02"),
            ]
        )

        due = acquisition.overdue(leads, "2026-08-31")

        self.assertEqual(list(due["parcel"]), ["1"])

    def test_a_lead_without_a_date_is_never_chased(self):
        leads = leads_frame(
            [
                (4001, "1", "Aarau", "contacted", ""),
                (4002, "2", "Baden", "contacted", "2026-08-01"),
            ]
        )

        due = acquisition.overdue(leads, "2026-08-31")

        self.assertEqual(list(due["parcel"]), ["2"])

    def test_overdue_leads_come_back_earliest_first(self):
        leads = leads_frame(
            [
                (4001, "1", "Aarau", "contacted", "2026-08-27"),
                (4002, "2", "Baden", "contacted", "2026-08-12"),
                (4003, "3", "Brugg", "contacted", "2026-08-21"),
            ]
        )

        due = acquisition.overdue(leads, "2026-08-31")

        self.assertEqual(
            list(due["due_date"]), ["2026-08-12", "2026-08-21", "2026-08-27"]
        )

    def test_an_empty_shortlist_returns_an_empty_frame(self):
        due = acquisition.overdue(leads_frame([]), "2026-08-31")

        self.assertTrue(due.empty)


class ByStageTest(unittest.TestCase):
    def test_every_stage_is_present_in_board_order_even_when_empty(self):
        stages = acquisition.by_stage(leads_frame([]))

        self.assertEqual(list(stages), list(workflow.CONTACT_STATUS_LABELS))
        self.assertTrue(all(frame.empty for frame in stages.values()))

    def test_leads_land_in_their_own_stage(self):
        leads = leads_frame(
            [
                (4001, "1", "Aarau", "in_discussion", "2026-09-04"),
                (4002, "2", "Baden", "declined", ""),
                (4003, "3", "Brugg", "in_discussion", "2026-09-01"),
            ]
        )

        stages = acquisition.by_stage(leads)

        self.assertEqual(list(stages["in_discussion"]["parcel"]), ["3", "1"])
        self.assertEqual(list(stages["declined"]["parcel"]), ["2"])
        self.assertTrue(stages["not_contacted"].empty)

    def test_an_undated_lead_sorts_below_a_dated_one(self):
        """An empty date sorts before every real one as a string, which is the
        opposite of what the column wants: a lead nobody has scheduled is not
        more urgent than one that is due next week."""
        leads = leads_frame(
            [
                (4001, "1", "Aarau", "contacted", ""),
                (4002, "2", "Baden", "contacted", "2026-09-10"),
                (4003, "3", "Brugg", "contacted", "2026-09-01"),
            ]
        )

        stages = acquisition.by_stage(leads)

        self.assertEqual(list(stages["contacted"]["parcel"]), ["3", "2", "1"])

    def test_an_unknown_stored_status_falls_back_to_the_first_stage(self):
        """A status written by a newer release and read by this one. The lead
        appears on the board rather than vanishing from it."""
        leads = leads_frame([(4001, "1", "Aarau", "gone_quiet", "")])

        stages = acquisition.by_stage(leads)

        self.assertEqual(list(stages["not_contacted"]["parcel"]), ["1"])


if __name__ == "__main__":
    unittest.main()
