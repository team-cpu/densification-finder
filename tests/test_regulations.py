import unittest
from datetime import date

import regulations as R

#: The shape OEREBlex actually returns, trimmed. Checked against the live feed
#: on 2026-08-20: a list of towns, each carrying its own edicts, superseded
#: versions included.
TOWNS = [
    {"name": "Möhlin", "edicts": [
        {"title": "Bau- und Nutzungsordnung", "abbreviation": "BNO",
         "syst_nr": "4254", "inaction_date": "2023-12-13", "is_active": True,
         "main_document": {"document_path": "/api/attachments/1"}},
        {"title": "Bau- und Nutzungsordnung", "abbreviation": "BNO",
         "syst_nr": "4254", "inaction_date": "2001-01-01",
         "outaction_date": "2023-12-12", "is_active": False,
         "main_document": {"document_path": "/api/attachments/0"}},
    ]},
    {"name": "Gipf-Oberfrick", "edicts": [
        {"title": "Bau- und Nutzungsordnung", "abbreviation": "BNO",
         "syst_nr": "4165", "inaction_date": "2026-06-03", "is_active": True,
         "main_document": {"document_path": "/api/attachments/3"}},
    ]},
    {"name": "Dintikon", "edicts": [
        {"title": "Bau- und Nutzungsordnung", "abbreviation": "BNO",
         "syst_nr": "4196", "inaction_date": "2022-05-18", "is_active": True,
         "main_document": {}},
    ]},
    {"name": "Ohnedatum", "edicts": [
        {"title": "Bau- und Nutzungsordnung", "abbreviation": "BNO",
         "syst_nr": "4999", "inaction_date": None, "is_active": True,
         "main_document": {}},
    ]},
]


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.edicts = R.parse(TOWNS)

    def test_only_what_is_in_force_and_dated(self):
        """The feed carries the history as well — Möhlin's 2001 edition is in
        there with an `outaction_date`. Answering "what governs this parcel now"
        means dropping it, and dropping the undated entry rather than sorting it
        as though it had a date."""
        self.assertEqual(len(self.edicts), 3)
        self.assertNotIn("Ohnedatum", [e.municipality for e in self.edicts])
        self.assertEqual([e.in_force.year for e in self.edicts
                          if e.municipality == "Möhlin"], [2023])

    def test_newest_first(self):
        self.assertEqual([e.municipality for e in self.edicts],
                         ["Gipf-Oberfrick", "Möhlin", "Dintikon"])

    def test_the_document_link_is_absolute(self):
        """`document_path` is a path; a relative href in the panel would resolve
        against the app's own host and 404."""
        newest = self.edicts[0]
        self.assertEqual(newest.document,
                         "https://oereblex.ag.ch/api/attachments/3")
        # …and an edict with no filed document says so with an empty string,
        # not a link to nowhere.
        self.assertEqual(
            next(e for e in self.edicts if e.municipality == "Dintikon").document, "")

    def test_swiss_date_and_label(self):
        newest = self.edicts[0]
        self.assertEqual(newest.when, "03.06.2026")
        self.assertEqual(newest.in_force, date(2026, 6, 3))
        self.assertEqual(newest.label, "BNO")

    def test_a_missing_or_broken_feed_is_a_value_not_an_exception(self):
        self.assertEqual(R.parse(None), [])
        self.assertEqual(R.parse([{"name": "X", "edicts": None}]), [])
        self.assertEqual(
            R.parse([{"name": "X", "edicts": [
                {"is_active": True, "inaction_date": "not-a-date"}]}]), [])


class JoinTest(unittest.TestCase):
    """The join is by name, and the number is only ever a cross-check."""

    def setUp(self):
        self.edicts = R.parse(TOWNS)

    def test_the_dintikon_trap_is_reported_not_resolved(self):
        """OEREBlex numbers Dintikon 4196; the building register this tool keys
        on says BFS 4194. Keying on the number would attach a neighbour's
        building regulation to those parcels."""
        edict, note = R.for_municipality(self.edicts, "Dintikon", 4194)
        self.assertEqual(edict.municipality, "Dintikon")
        self.assertIn("4196", note)
        self.assertIn("4194", note)

    def test_an_agreeing_number_says_nothing(self):
        edict, note = R.for_municipality(self.edicts, "Möhlin", 4254)
        self.assertEqual(edict.when, "13.12.2023")
        self.assertEqual(note, "")

    def test_a_municipality_the_feed_does_not_carry(self):
        self.assertEqual(R.for_municipality(self.edicts, "Nirgendwo", 1), (None, ""))

    def test_the_newest_edition_wins(self):
        """Möhlin's superseded 2001 edition must never be the one reported."""
        edict, _ = R.for_municipality(self.edicts, "Möhlin", 4254)
        self.assertEqual(edict.in_force.year, 2023)


if __name__ == "__main__":
    unittest.main()
