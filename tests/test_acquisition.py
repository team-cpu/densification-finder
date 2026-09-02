import unittest
from unittest.mock import patch

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
    def test_a_lead_due_today_is_overdue(self):
        """The design prototype's own rule is `dd <= TODAY`; a `<` boundary
        here meant a lead due today did not show up as needing a call until
        tomorrow, by which point it was already a day late."""
        leads = leads_frame(
            [
                (4001, "1", "Aarau", "contacted", "2026-08-30"),
                (4002, "2", "Baden", "contacted", "2026-08-31"),
                (4003, "3", "Brugg", "contacted", "2026-09-02"),
            ]
        )

        due = acquisition.overdue(leads, "2026-08-31")

        self.assertEqual(list(due["parcel"]), ["1", "2"])

    def test_a_declined_lead_is_never_overdue(self):
        """`Abgelehnt` already means the owner said no; a lead marked
        no-interest and given a distant revisit date must not nag every day
        until that date arrives."""
        leads = leads_frame(
            [
                (4001, "1", "Aarau", "declined", "2020-01-01"),
                (4002, "2", "Baden", "contacted", "2020-01-01"),
            ]
        )

        due = acquisition.overdue(leads, "2026-08-31")

        self.assertEqual(list(due["parcel"]), ["2"])

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


class DueItemsTest(unittest.TestCase):
    def test_overdue_leads_come_before_not_yet_due_dated_leads(self):
        """Mirrors the design prototype's `dueItems`: overdue first, then a
        look-ahead at what's coming — not the overdue set alone, which made
        the preview go quiet the moment the last overdue lead cleared."""
        leads = leads_frame(
            [
                (4001, "1", "Aarau", "contacted", "2026-09-10"),  # not yet due
                (4002, "2", "Baden", "contacted", "2026-08-20"),  # overdue
                (4003, "3", "Brugg", "contacted", "2026-09-05"),  # not yet due
            ]
        )

        items = acquisition.due_items(leads, "2026-08-31")

        self.assertEqual(list(items["parcel"]), ["2", "3", "1"])

    def test_due_items_caps_at_four_rows(self):
        """The preview is a glance at what needs chasing, not a second copy
        of the board — an uncapped list defeats that."""
        leads = leads_frame(
            [
                (4000 + i, str(i), "Aarau", "contacted", f"2026-09-0{i}")
                for i in range(1, 7)
            ]
        )

        items = acquisition.due_items(leads, "2026-08-31")

        self.assertEqual(len(items), 4)

    def test_a_declined_lead_is_absent_even_when_dated(self):
        """Declined is excluded from both halves of `dueItems` in the
        prototype, not just from the overdue half."""
        leads = leads_frame(
            [
                (4001, "1", "Aarau", "declined", "2020-01-01"),
                (4002, "2", "Baden", "declined", "2026-09-10"),
            ]
        )

        items = acquisition.due_items(leads, "2026-08-31")

        self.assertTrue(items.empty)

    def test_a_lead_with_no_date_is_absent(self):
        leads = leads_frame([(4001, "1", "Aarau", "contacted", "")])

        items = acquisition.due_items(leads, "2026-08-31")

        self.assertTrue(items.empty)

    def test_overdue_leads_within_due_items_are_earliest_first(self):
        leads = leads_frame(
            [
                (4001, "1", "Aarau", "contacted", "2026-08-27"),
                (4002, "2", "Baden", "contacted", "2026-08-12"),
                (4003, "3", "Brugg", "contacted", "2026-08-21"),
            ]
        )

        items = acquisition.due_items(leads, "2026-08-31")

        self.assertEqual(
            list(items["due_date"]), ["2026-08-12", "2026-08-21", "2026-08-27"]
        )

    def test_an_empty_shortlist_returns_an_empty_frame(self):
        items = acquisition.due_items(leads_frame([]), "2026-08-31")

        self.assertTrue(items.empty)


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


class BoardEventTest(unittest.TestCase):
    def setUp(self):
        self.leads = pd.DataFrame({"bfs": [4001], "parcel": ["1"]})

    def test_move_event_writes_the_dropped_stage(self):
        with patch.object(acquisition.WF, "update") as update:
            handled = acquisition.handle_board_event(
                {
                    "type": "move",
                    "bfs": 4001,
                    "parcel": "1",
                    "stage": "in_discussion",
                },
                self.leads,
                "/tmp/test.sqlite",
            )

        self.assertTrue(handled)
        update.assert_called_once_with(
            [(4001, "1")],
            contact_status="in_discussion",
            db="/tmp/test.sqlite",
        )

    def test_move_event_rejects_an_unknown_stage(self):
        with patch.object(acquisition.WF, "update") as update:
            handled = acquisition.handle_board_event(
                {
                    "type": "move",
                    "bfs": 4001,
                    "parcel": "1",
                    "stage": "invented",
                },
                self.leads,
                "/tmp/test.sqlite",
            )

        self.assertFalse(handled)
        update.assert_not_called()

    def test_event_rejects_a_parcel_outside_the_board(self):
        with patch.object(acquisition.WF, "update") as update:
            handled = acquisition.handle_board_event(
                {
                    "type": "move",
                    "bfs": 9999,
                    "parcel": "9",
                    "stage": "contacted",
                },
                self.leads,
                "/tmp/test.sqlite",
            )

        self.assertFalse(handled)
        update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
