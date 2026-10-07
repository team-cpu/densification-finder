import unittest

import amtsblatt as A


class MunicipalLinkTest(unittest.TestCase):
    """The link Scope hands the reader instead of copying the Amtsblatt.

    The format below is the one the Amtsblatt's own search page builds when a
    reader ticks "Gemeinde Seon" and "Bau- und Nutzungsordnung" — checked by
    hand on 2026-10-05, when it listed Seon's eleven planning publications and
    nothing from any other municipality.
    """

    def test_seon_opens_its_own_planning_publications(self):
        self.assertEqual(
            A.municipal_planning_link("Seon"),
            "https://amtsblatt.ag.ch/publikationen/?filter[authority][]=10,398"
            "&filter[category][]=190,192&timerange[type]=4",
        )

    def test_every_publishing_office_of_the_municipality_is_included(self):
        """Wohlen publishes as "Gemeinde Wohlen" and as its planning department.
        Filtering on the first alone would hide what the second publishes."""
        link = A.municipal_planning_link("Wohlen")
        self.assertIn("filter[authority][]=10,168", link)
        self.assertIn("filter[authority][]=10,388", link)

    def test_a_neighbour_with_a_longer_name_is_not_swept_in(self):
        """Wohlenschwil starts with "Wohlen". A prefix match would put its
        publications under Wohlen's parcels."""
        self.assertNotIn("10,451", A.municipal_planning_link("Wohlen"))
        self.assertEqual(A.authorities_for("Wohlenschwil"), ("10,451",))

    def test_register_spellings_find_the_amtsblatt_office(self):
        """The building register says "Hausen (AG)" and hyphenates with ASCII;
        the Amtsblatt says "Hausen AG" and hyphenates with U+2010."""
        self.assertEqual(A.authorities_for("Hausen (AG)"), ("10,178",))
        self.assertEqual(A.authorities_for("Gipf-Oberfrick"), ("10,56",))
        self.assertEqual(A.authorities_for("Beinwil (Freiamt)"), ("10,156",))

    def test_an_unknown_municipality_falls_back_to_a_name_search(self):
        """Better a search that may show a little too much than no link, and
        never another municipality's filter."""
        link = A.municipal_planning_link("Nirgendwo")
        self.assertIn("searchQuery=%22Nirgendwo%22", link)
        self.assertIn("filter[category][]=190,192", link)
        self.assertNotIn("filter[authority]", link)


class CantonLinkTest(unittest.TestCase):
    def test_canton_approvals_are_searched_by_the_municipality_name(self):
        """The canton approves municipal plans under its own name (Regierungsrat,
        Abteilung Raumentwicklung), so the office filter cannot find them; the
        notices name the municipality in their text instead."""
        self.assertEqual(
            A.canton_approvals_link("Seon"),
            "https://amtsblatt.ag.ch/publikationen/?searchQuery=%22Seon%22"
            "&filter[category][]=162,175&timerange[type]=4",
        )

    def test_the_canton_search_drops_the_register_suffix(self):
        """Canton notices write "Gemeinde Reinach", never "Reinach (AG)"."""
        self.assertIn("searchQuery=%22Reinach%22", A.canton_approvals_link("Reinach (AG)"))

    def test_canton_wide_consultations(self):
        self.assertEqual(
            A.canton_consultations_link(),
            "https://amtsblatt.ag.ch/publikationen/?filter[category][]=162,166"
            "&timerange[type]=4",
        )


class MunicipalityKeyTest(unittest.TestCase):
    def test_spellings_that_name_the_same_place(self):
        self.assertEqual(A.municipality_key("Gipf‐Oberfrick"), A.municipality_key("Gipf-Oberfrick"))
        self.assertEqual(A.municipality_key("Hausen AG"), A.municipality_key("Hausen (AG)"))
        self.assertEqual(A.municipality_key(" Seon "), "seon")

    def test_different_places_stay_different(self):
        self.assertNotEqual(A.municipality_key("Wohlen"), A.municipality_key("Wohlenschwil"))
        self.assertNotEqual(A.municipality_key("Beinwil am See"),
                            A.municipality_key("Beinwil (Freiamt)"))


class LinkKindTest(unittest.TestCase):
    """What a link opens decides how the panel labels it: the arrow promises
    the publication itself, a search is labelled as one."""

    def test_kinds(self):
        self.assertEqual(A.link_kind("https://amtsblatt.ag.ch/ekab/00.102.603/pdf/"), "publikation")
        self.assertEqual(A.link_kind("https://amtsblatt.ag.ch/ekab/00.102.603/"), "publikation")
        self.assertEqual(A.link_kind("https://oereblex.ag.ch/api/attachments/7265"), "dokument")
        self.assertEqual(A.link_kind(A.municipal_planning_link("Seon")), "suche")
        self.assertEqual(A.link_kind("https://www.seon.ch/planung"), "link")
        self.assertEqual(A.link_kind(""), "")

    def test_an_address_the_url_parser_rejects_is_just_a_link(self):
        """Never an error: "https://[invalid/" opens no publication."""
        self.assertEqual(A.link_kind("https://[invalid/"), "link")

    def test_a_path_climbing_out_of_a_publication_is_none(self):
        """The arrow promises one publication: "…/ekab/1/../../publikationen/"
        opens the search."""
        for url in ("https://amtsblatt.ag.ch/ekab/1/../../publikationen/?searchQuery=x",
                    "https://amtsblatt.ag.ch/ekab/1/%2e%2e/%2E%2E/publikationen/?searchQuery=x",
                    "https://amtsblatt.ag.ch/ekab/00.102.603/pdf/../../../publikationen/"):
            self.assertEqual(A.link_kind(url), "suche", url)

    def test_every_other_amtsblatt_address_is_not_a_publication(self):
        self.assertEqual(A.link_kind("https://amtsblatt.ag.ch/ekab/00.102.603"), "publikation")
        self.assertEqual(A.link_kind("https://www.amtsblatt.ag.ch/ekab/00.102.603/pdf/"), "publikation")
        for url in ("https://amtsblatt.ag.ch/publikationen?searchQuery=%22Seon%22",
                    "https://www.amtsblatt.ag.ch/publikationen/?searchQuery=x",
                    "https://amtsblatt.ag.ch/?searchQuery=x"):
            self.assertEqual(A.link_kind(url), "suche", url)


if __name__ == "__main__":
    unittest.main()
