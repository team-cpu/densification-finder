import csv
import io
import unittest
from datetime import date, timedelta
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

    def test_move_event_writes_the_dropped_stage_and_the_two_dates(self):
        """The dialog no longer asks for a Wiedervorlage, so the move has to
        supply it: dragging a card is the contact, and the follow-up is due
        two weeks later."""
        today = date.today()
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
            last_contact=today.isoformat(),
            due_date=(
                today + timedelta(days=acquisition.FOLLOW_UP_DAYS)
            ).isoformat(),
            db="/tmp/test.sqlite",
        )

    def test_a_declined_lead_is_dated_but_not_scheduled_again(self):
        """"Abgelehnt" ends the conversation. Giving it a follow-up date would
        put a lead nobody intends to call back into Fällige Wiedervorlagen."""
        with patch.object(acquisition.WF, "update") as update:
            acquisition.handle_board_event(
                {"type": "move", "bfs": 4001, "parcel": "1", "stage": "declined"},
                self.leads,
                "/tmp/test.sqlite",
            )

        self.assertEqual(update.call_args.kwargs["due_date"], "")
        self.assertEqual(
            update.call_args.kwargs["last_contact"], date.today().isoformat()
        )

    def test_owner_event_opens_that_cards_contact_dialog(self):
        """The board carries the only way into the owner sheet now that the
        Fällige-Wiedervorlagen row with its button is gone."""
        state = {}

        handled = acquisition.handle_board_event(
            {"type": "owner", "bfs": 4001, "parcel": "1"},
            self.leads,
            "/tmp/test.sqlite",
            state,
        )

        self.assertTrue(handled)
        self.assertEqual(state[acquisition.CONTACT_OPEN], "4001:1")

    def test_an_owner_event_cannot_name_a_parcel_off_the_board(self):
        state = {}

        handled = acquisition.handle_board_event(
            {"type": "owner", "bfs": 9999, "parcel": "2"},
            self.leads,
            "/tmp/test.sqlite",
            state,
        )

        self.assertFalse(handled)
        self.assertEqual(state, {})

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


class DisplayParityTest(unittest.TestCase):
    def test_legacy_phone_is_formatted_on_board_and_export_without_mutating_source(self):
        rows = pd.DataFrame([{
            "bfs": 4001, "parcel": "1", "municipality": "Aarau",
            "address": "", "area": 1000.0, "delta": 500.0,
            "contact_status": "contacted", "due_date": "", "last_contact": "",
            "contact_person": "Test", "phone": "0787778800", "owner_name": "",
            "owner_address": "", "next_step": "", "email": "", "note": "",
        }])
        before = rows.copy(deep=True)
        self.assertEqual(acquisition.contact_list(rows).iloc[0]["Telefon"], "078 777 88 00")
        stages = acquisition.board_data(rows, lambda row: None, "2026-09-09")
        cards = [card for stage in stages for card in stage["cards"]]
        self.assertEqual(cards[0]["contactLine"], "Test · 078 777 88 00")
        pd.testing.assert_frame_equal(rows, before)


    def test_postal_address_survives_csv_export(self):
        rows = pd.DataFrame([
            {
                "address": "Parcel address", "municipality": "Möhlin",
                "parcel": "954", "delta": 500.0, "owner_name": "UI test",
                "owner_address": address, "contact_person": "", "phone": "",
                "email": "", "contact_status": "contacted",
                "last_contact": "2026-09-08", "due_date": "2026-09-22",
                "next_step": "", "note": "First line\nSecond line",
            }
            for address in ('  Müller, "Test"\r\n\r\n Teststrasse 10 \n4313 Möhlin ', '')
        ])
        payload = acquisition.contact_list(rows).to_csv(index=False).encode("utf-8")
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8")))
        exported = list(reader)
        self.assertEqual(reader.fieldnames.count("Postadresse"), 1)
        self.assertEqual(len(exported), 2)
        self.assertEqual(exported[0]["Postadresse"], 'Müller, "Test" · Teststrasse 10 · 4313 Möhlin')
        self.assertEqual(exported[1]["Postadresse"], "")
        self.assertEqual(exported[0]["Adresse"], "Parcel address")
        self.assertEqual(exported[0]["Notiz"], "First line\nSecond line")
        self.assertTrue(all(None not in row for row in exported))

    def test_board_formats_dates_but_compares_iso_across_month_boundaries(self):
        dates = ("2026-08-31", "2026-09-02", "2025-12-31", "2027-01-01", "")
        rows = pd.DataFrame([
            {
                "bfs": 4001, "parcel": str(index), "municipality": "Aarau",
                "address": f"Teststrasse {index}", "area": 1000.0, "delta": 500.0,
                "contact_status": "contacted", "due_date": due,
                "last_contact": "2026-08-21", "contact_person": "", "phone": "",
                "owner_name": "", "owner_address": "", "next_step": "",
                "email": "", "note": "",
            }
            for index, due in enumerate(dates)
        ])
        original = rows.copy(deep=True)
        stages = acquisition.board_data(rows, lambda row: None, "2026-09-01")
        cards = {card["parcel"]: card for stage in stages for card in stage["cards"]}
        self.assertEqual([cards[str(i)]["due"] for i in range(5)], [
            "31.08.2026", "02.09.2026", "31.12.2025", "01.01.2027", "—",
        ])
        self.assertEqual([cards[str(i)]["overdue"] for i in range(5)], [
            True, False, True, False, False,
        ])
        self.assertTrue(all(card["lastContact"] == "21.08.2026" for card in cards.values()))
        pd.testing.assert_frame_equal(rows, original)
        # CSV is an established data interface; keep canonical ISO dates there.
        export = acquisition.contact_list(rows)
        self.assertEqual(export["Wiedervorlage"].tolist(), list(dates))
        self.assertTrue((export["Letzter Kontakt"] == "2026-08-21").all())
        rows["contact_status"] = "declined"
        declined = acquisition.board_data(rows, lambda row: None, "2026-09-01")[-1]
        self.assertFalse(any(card["overdue"] for card in declined["cards"]))

    def test_null_and_existing_swiss_dates_render_without_nan(self):
        for value in (None, pd.NA, float("nan"), ""):
            self.assertEqual(acquisition._contact_date_display(value), "")
        self.assertEqual(acquisition._contact_date_display("02.09.2026"), "02.09.2026")


if __name__ == "__main__":
    unittest.main()
