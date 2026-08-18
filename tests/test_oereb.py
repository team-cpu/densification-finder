import unittest

import oereb as O


def multilingual(value):
    """Every human-readable field in the extract arrives like this."""
    return [{"Language": "de", "Text": value}]


def provision(title, kind, number="", url="", abbr="", index=0):
    return {
        "Title": multilingual(title),
        "OfficialNumber": multilingual(number) if number else None,
        "Abbreviation": multilingual(abbr) if abbr else None,
        "TextAtWeb": multilingual(url) if url else None,
        "Type": {"Code": kind, "Text": multilingual(kind)},
        "Lawstatus": {"Code": "inForce", "Text": multilingual("Rechtskräftig")},
        "Index": index,
    }


#: One real extract's shape, trimmed: Egliswil parcel 229, whose plan arrives
#: with an annex under the same title and whose legal bases are delivered out of
#: order.
EXTRACT = {
    "GetExtractByIdResponse": {
        "extract": {
            "CreationDate": "2026-08-18T10:33:46",
            "RealEstate": {
                "MunicipalityName": "Egliswil",
                "MunicipalityCode": 4195,
                "Number": "229",
                "LandRegistryArea": 5533,
                "EGRID": "CH757305721124",
                "RestrictionOnLandownership": [
                    {
                        # Arrives first and belongs to the canton, not the
                        # commune — a building line, with no area of its own.
                        "Theme": {"Code": "ch.Nutzungsplanung"},
                        "LegendText": multilingual("Baulinie"),
                        "AreaShare": None,
                        "LengthShare": 47,
                        "ResponsibleOffice": {
                            "Name": multilingual("Abteilung Verkehr"),
                            "OfficeAtWeb": multilingual("https://www.ag.ch/verkehr"),
                        },
                        "LegalProvisions": [],
                    },
                    {
                        "Theme": {"Code": "ch.Nutzungsplanung"},
                        "LegendText": multilingual("Einfamilienhauszone [E]"),
                        "AreaShare": 5406,
                        "PartInPercent": 97.7,
                        "ResponsibleOffice": {
                            "Name": multilingual("Egliswil"),
                            "OfficeAtWeb": multilingual("http://www.egliswil.ch"),
                        },
                        "LegalProvisions": [
                            provision("Bauzonen- und Kulturlandplan", "LegalProvision",
                                      "1996-002045", "https://oereblex.ag.ch/api/attachments/2232"),
                            # Same plan, its annex — one document, two files.
                            provision("Bauzonen- und Kulturlandplan", "LegalProvision",
                                      "1996-002045", "https://oereblex.ag.ch/api/attachments/1333"),
                            provision("Bau- und Nutzungsordnung", "LegalProvision",
                                      "4195", "https://oereblex.ag.ch/api/attachments/1289"),
                            provision("Bauverordnung", "Law", "SAR 713.121",
                                      "https://gesetzessammlungen.ag.ch/api/de/versions/3985",
                                      abbr="BauV", index=930),
                            provision("Bundesgesetz über die Raumplanung", "Law", "SR 700",
                                      "https://www.fedlex.admin.ch/eli/cc/1979/1573_1573_1573/de",
                                      abbr="RPG", index=10),
                        ],
                    },
                    {
                        "Theme": {"Code": "ch.Laermempfindlichkeitsstufen"},
                        "LegendText": multilingual("Empfindlichkeitsstufe II"),
                        "AreaShare": 5406,
                        "PartInPercent": 97.7,
                        "LegalProvisions": [
                            provision("Lärmschutz-Verordnung", "Law", "SR 814.41",
                                      "https://www.fedlex.admin.ch/eli/cc/1987/338_338_338/de",
                                      abbr="LSV", index=620),
                        ],
                    },
                ],
            },
        }
    }
}


class ExtractDetailTest(unittest.TestCase):
    def setUp(self):
        self.details = O.details(EXTRACT)

    def test_only_zoning_objects_become_the_zone_split(self):
        """The extract's "Legende beteiligter Objekte" is the Nutzungsplanung
        theme, in its own order. Noise sensitivity covers the same ground and is
        not a zone; a building line is, and has no area."""
        self.assertEqual(
            self.details["zones"],
            [
                {"text": "Baulinie", "area": None, "percent": None},
                {"text": "Einfamilienhauszone [E]", "area": 5406, "percent": 97.7},
            ],
        )

    def test_the_commune_is_the_responsible_office_when_it_is_among_them(self):
        """Several offices answer for one parcel. Taking whichever came first put
        the cantonal roads department on a parcel whose zoning question belongs
        to the commune."""
        self.assertEqual(self.details["office"]["name"], "Egliswil")

    def test_a_plan_and_its_annex_are_one_document_with_two_files(self):
        """Keying on the URL would list the same plan twice and read as two
        different plans."""
        plans = [p for p in self.details["provisions"]
                 if p["title"] == "Bauzonen- und Kulturlandplan"]
        self.assertEqual(len(plans), 1)
        self.assertEqual(len(plans[0]["urls"]), 2)

    def test_provisions_and_laws_are_split_the_way_the_extract_splits_them(self):
        self.assertEqual(
            [p["title"] for p in self.details["provisions"]],
            ["Bauzonen- und Kulturlandplan", "Bau- und Nutzungsordnung"],
        )
        self.assertEqual([law["abbr"] for law in self.details["laws"]],
                         ["RPG", "LSV", "BauV"])

    def test_laws_keep_the_cadastre_ordering(self):
        """Index 10 before 620 before 930 — federal before cantonal, by subject,
        as the printed extract lists them."""
        self.assertEqual([law["index"] for law in self.details["laws"]],
                         sorted(law["index"] for law in self.details["laws"]))

    def test_the_municipality_document_carries_the_bfs_number(self):
        """The BNO's official number is the municipality's BFS number, which is
        why this needs no name matching to be attached to the right parcel."""
        bno = next(p for p in self.details["provisions"]
                   if p["title"] == "Bau- und Nutzungsordnung")
        self.assertEqual(bno["number"], str(self.details["bfs"]))

    def test_parcel_identity_and_registry_area_survive(self):
        self.assertEqual(self.details["municipality"], "Egliswil")
        self.assertEqual(self.details["parcel"], "229")
        self.assertEqual(self.details["land_registry_area"], 5533)
        self.assertEqual(self.details["office"],
                         {"name": "Egliswil", "url": "http://www.egliswil.ch"})
        self.assertEqual(self.details["created"], "2026-08-18T10:33:46")

    def test_an_empty_extract_does_not_raise(self):
        empty = O.details({"GetExtractByIdResponse": {"extract": {}}})
        self.assertEqual(empty["zones"], [])
        self.assertEqual(empty["provisions"], [])
        self.assertIsNone(empty["land_registry_area"])


class AssessTest(unittest.TestCase):
    def test_classification_and_details_come_out_of_one_request(self):
        """Two answers, one call: the extract is the expensive part, and a second
        request could return a different one."""
        calls = []
        original = O.fetch
        O.fetch = lambda egrid, timeout=60: (calls.append(egrid), EXTRACT)[1]
        try:
            hard, notable, error, details = O.assess("CH757305721124")
        finally:
            O.fetch = original
        self.assertEqual(calls, ["CH757305721124"])
        self.assertIsNone(error)
        self.assertEqual(hard, [])
        self.assertEqual(details["parcel"], "229")

    def test_a_failed_request_yields_no_details_rather_than_half(self):
        original = O.fetch

        def boom(egrid, timeout=60):
            raise OSError("HTTP 502")

        O.fetch = boom
        try:
            hard, notable, error, details = O.assess("CH0")
        finally:
            O.fetch = original
        self.assertEqual((hard, notable), ([], []))
        self.assertIn("502", error)
        self.assertIsNone(details)


if __name__ == "__main__":
    unittest.main()
