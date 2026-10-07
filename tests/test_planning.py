import json
import os
import tempfile
import unicodedata
import unittest
from datetime import date
from unittest.mock import patch

import planning as P
import regulations as R

TODAY = date(2026, 10, 5)


def record(published_on, authority, title, text="", pub_nr="", rubric=""):
    return {
        "pub_nr": pub_nr or f"00.000.{abs(hash((published_on, title))) % 1000:03d}",
        "published_on": published_on,
        "authority": authority,
        "rubric": rubric,
        "title": title,
        "text": text,
        "url": f"https://amtsblatt.ag.ch/ekab/{pub_nr or '00.000.000'}/pdf/",
    }


#: Modelled on what the Amtsblatt listed for Seon on 2026-10-05: one area plan
#: in Mitwirkung, one that went Mitwirkung → Auflage → Beschluss over three
#: years, a repeal whose Beschluss names the plans only in its text, and the
#: canton's approval notices — one of which approves plans of two municipalities
#: at once and is cut off mid-sentence, the way the search list shows it.
SEON = [
    record("2021-07-01", "Gemeinde Seon", 'Gestaltungsplan "Giessi"; Mitwirkungsverfahren',
           "Im Rahmen der Gesamtrevision der Nutzungsplanung wurde das Gebiet "
           '"Giessi" von der Industriezone in die Wohnzone 3 umgezont.', "00.016.453"),
    record("2024-06-27", "Gemeinde Seon", 'Gestaltungsplan "Giessi"; öffentliche Auflage',
           "Nach Abschluss des Mitwirkungsverfahrens und der kantonalen Vorprüfung "
           "werden die Entwürfe öffentlich aufgelegt.", "00.055.965"),
    record("2024-09-05", "Gemeinde Seon", 'Gestaltungsplan "Giessi"; Beschluss',
           'Der Gemeinderat hat den Gestaltungsplan "Giessi" in Übereinstimmung mit '
           "der öffentlichen Auflage beschlossen.", "00.059.570"),
    record("2024-06-27", "Gemeinde Seon",
           'Aufhebung "Weg- und Baulinienplan Überbauung Breiten" und "Weg- und '
           'Baulinienplan Unterdorf-Giesserei-Breiten"; öffentliche Auflage und '
           "Mitwirkungsverfahren", "", "00.055.967"),
    record("2024-09-05", "Gemeinde Seon", "Aufhebung von Überbauungsplänen; Beschluss",
           "Der Gemeinderat hat die Aufhebung der kommunalen Überbauungspläne "
           '"Weg- und Baulinienplan Überbauung Breiten" und "Weg- und Baulinienplan '
           'Unterdorf-Giesserei-Breiten" beschlossen.', "00.059.572"),
    record("2026-09-10", "Gemeinde Seon",
           "Öffentliche Mitwirkung zum Gestaltungsplan «Heidegrabe»",
           "Das Gebiet Heidegrabe befindet sich zwischen der Aarauerstrasse und der "
           "Lenzburgerstrasse. Zur Durchführung der öffentlichen Mitwirkung (§ 3 BauG) "
           "wird der Entwurf aufgelegt. Die Planentwürfe liegen vom 11. September 2026 "
           "bis zum 12. Oktober 2026 auf.", "00.102.603"),
    record("2025-01-17", "Abteilung Raumentwicklung", "Genehmigung von Sondernutzungsplänen",
           "Das Departement Bau, Verkehr und Umwelt hat am 13. Januar 2025 nachstehende "
           'Sondernutzungspläne genehmigt: | Gemeinde Reinach: Gestaltungsplan "Friedstrasse" '
           "| Gemeinde Seon: Aufhebung Überbauungsplän […]", "00.067.000"),
    record("2020-01-17", "Regierungsrat", "Genehmigung von Nutzungsplänen",
           "Gemeinde Seon; Nutzungsplanung Siedlung und Kulturland, Gesamtrevision; "
           "Genehmigung mit Änderungen […]", "00.001.000"),
]

SEON_BNO = R.Edict(municipality="Seon", title="Bau- und Nutzungsordnung",
                   abbreviation="BNO", in_force=date(2020, 1, 7), syst_nr="4209",
                   document="https://oereblex.ag.ch/api/attachments/7265")


def seon_view(extra=()):
    pubs, problems = P.publications(SEON + list(extra))
    assert problems == [], problems
    return P.view(pubs, "Seon", [SEON_BNO], today=TODAY)


class StepTest(unittest.TestCase):
    """Which step of the procedure a publication announces."""

    def test_the_title_names_the_step(self):
        self.assertEqual(P.step_of("Öffentliche Mitwirkung zum Gestaltungsplan «Heidegrabe»"),
                         "mitwirkung")
        self.assertEqual(P.step_of('Gestaltungsplan "Giessi"; öffentliche Auflage'), "auflage")
        self.assertEqual(P.step_of('Gestaltungsplan "Giessi"; Beschluss'), "beschluss")
        self.assertEqual(P.step_of("Genehmigung von Nutzungsplänen"), "genehmigung")

    def test_the_furthest_step_in_the_title_wins(self):
        """"öffentliche Auflage und Mitwirkungsverfahren" runs both at once; the
        Auflage is the later step, so it is the one that says where things stand."""
        self.assertEqual(P.step_of("Aufhebung X; öffentliche Auflage und Mitwirkungsverfahren"),
                         "auflage")

    def test_the_text_is_not_read_for_the_step(self):
        """Texts look back ("wurde 2010 genehmigt"), ahead ("zur Genehmigung
        unterbreitet") and sideways ("hat beschlossen, den Plan aufzulegen").
        A title without a step word leaves the step unknown."""
        self.assertEqual(P.step_of("Gestaltungsplan Musterwiese",
                                   "Die Entwürfe werden öffentlich aufgelegt."), "unbekannt")
        self.assertEqual(P.step_of('Gestaltungsplan "Giessi"; Mitwirkungsverfahren',
                                   "… wird später beschlossen und genehmigt."), "mitwirkung")

    def test_an_unknown_step_is_admitted(self):
        self.assertEqual(P.step_of("Verschiedenes", "Mitteilung des Gemeinderats"), "unbekannt")


class RevisionTest(unittest.TestCase):
    def setUp(self):
        self.view = seon_view()

    def find(self, bucket, needle):
        hits = [r for r in getattr(self.view, bucket) if needle in r.title]
        self.assertEqual(len(hits), 1, f"{needle!r} in {bucket}: {[r.title for r in getattr(self.view, bucket)]}")
        return hits[0]

    def test_three_publications_of_one_plan_are_one_revision(self):
        giessi = self.find("decided", "Giessi")
        self.assertEqual(giessi.title, "Gestaltungsplan «Giessi»")
        self.assertEqual([p.published_on for p in giessi.steps],
                         [date(2021, 7, 1), date(2024, 6, 27), date(2024, 9, 5)])
        self.assertEqual(giessi.step, "beschluss")
        self.assertEqual(giessi.stage, "beschlossen")
        self.assertEqual(giessi.published_on, date(2024, 9, 5))

    def test_a_follow_up_that_names_the_plans_only_in_its_text_still_joins(self):
        """The Beschluss is titled "Aufhebung von Überbauungsplänen"; the plans
        it repeals are named only in its text. Without reading that, the repeal
        would show up twice — once in Auflage, once decided."""
        repeal = self.find("decided", "Aufhebung «Weg")
        self.assertEqual([p.step for p in repeal.steps], ["auflage", "beschluss"])

    def test_an_approval_that_names_no_plan_is_not_attached_to_one(self):
        """The canton's notice is cut off before the plan names ("Aufhebung
        Überbauungsplän […]"). Attaching it to the one repeal waiting for
        approval would be an inference, not evidence: it keeps a row of its own,
        and the repeal stays decided."""
        approval = self.find("approved", "Überbauungsplän…")
        self.assertEqual(approval.status_label, "Genehmigt, Inkrafttreten nicht belegt")
        self.assertEqual(approval.published_on, date(2025, 1, 17))
        repeal = self.find("decided", "Aufhebung «Weg")
        self.assertEqual(repeal.status_label, "Beschlossen")

    def test_an_area_plan_is_marked_for_its_area_only(self):
        """Heidegrabe is a Gestaltungsplan for one area of Seon. Whether a given
        parcel lies in it is a question for the plan — the panel must not imply
        it applies to every parcel in the municipality."""
        heidegrabe = self.find("ongoing", "Heidegrabe")
        self.assertEqual(heidegrabe.stage, "verfahren")
        self.assertEqual(heidegrabe.scope, "gebiet")
        self.assertEqual(heidegrabe.area, "Heidegrabe")
        self.assertEqual(heidegrabe.scope_label, "Gebiet «Heidegrabe»")

    def test_a_long_area_name_is_left_to_the_title(self):
        """The repeal names two plans of sixty characters each; the title
        carries them, the badge only says it covers part of the
        municipality."""
        repeal = self.find("decided", "Aufhebung «Weg")
        self.assertEqual(repeal.scope, "gebiet")
        self.assertEqual(repeal.scope_label, "Teilgebiet")

    def test_each_item_carries_title_stage_date_and_source(self):
        heidegrabe = self.find("ongoing", "Heidegrabe")
        self.assertEqual(heidegrabe.title, "Gestaltungsplan «Heidegrabe»")
        self.assertEqual(heidegrabe.status_label, "Entwurf in Mitwirkung")
        self.assertEqual(heidegrabe.published_on, date(2026, 9, 10))
        self.assertIsNone(heidegrabe.effective_on)
        self.assertEqual(heidegrabe.url, "https://amtsblatt.ag.ch/ekab/00.102.603/pdf/")


class SourceMergeTest(unittest.TestCase):
    def test_the_approval_of_the_regulation_in_force_is_not_listed_twice(self):
        """Seon's Gesamtrevision was approved in a notice published ten days
        after OEREBlex has it in force. That is the same change seen from two
        sources: it belongs on the in-force line, not as a second entry."""
        view = seon_view()
        approval = view.approvals.get(SEON_BNO)
        self.assertIsNotNone(approval)
        self.assertEqual(approval.published_on, date(2020, 1, 17))
        listed = view.in_force + view.decided + view.ongoing + view.unclear
        self.assertFalse(any("Gesamtrevision" in r.title for r in listed))

    def test_an_approval_with_a_future_in_force_date_is_approved_not_in_force(self):
        later = record("2026-09-25", "Regierungsrat", "Genehmigung von Nutzungsplänen",
                       "Gemeinde Seon; Allgemeine Nutzungsplanung, Teilrevision Zentrum; "
                       "Genehmigung. Die Teilrevision tritt am 1. Januar 2027 in Kraft.")
        view = seon_view([later])
        hit = [r for r in view.approved if "Teilrevision Zentrum" in r.title]
        self.assertEqual(len(hit), 1, [r.title for r in view.all()])
        self.assertEqual(hit[0].effective_on, date(2027, 1, 1))
        self.assertEqual(hit[0].status_label, "Genehmigt, Rechtskraft ausstehend")

    def test_a_procedure_silent_for_years_stays_listed_as_unverified(self):
        """Seon's Beitragspläne "Vorder Zelgli" were on display in 2020 and the
        Amtsblatt has nothing after that. Hiding them would lose the
        information; calling them "in Auflage" would be wrong. Listed, with
        the state left unverified."""
        stale = record("2020-03-19", "Gemeinde Seon",
                       "Öffentliche Auflage Beitragspläne Vorder Zelgli, Mühleweg",
                       "Die Beitragspläne liegen in der Zeit vom 20.03.2020 bis 20.04.2020 auf.")
        view = seon_view([stale])
        zelgli = next(r for r in view.all() if "Vorder Zelgli" in r.title)
        self.assertEqual(zelgli.status_label, "Stand unbestätigt")
        self.assertEqual(zelgli.latest.period_end, date(2020, 4, 20))

    def test_old_approvals_stay_listed_without_a_claim_on_force(self):
        old = record("2019-10-04", "Abteilung Raumentwicklung", "Genehmigung von Sondernutzungsplänen",
                     'Gemeinde Seon: Erschliessungsplan "Milchgasse/Salzweg"')
        view = seon_view([old])
        milchgasse = next(r for r in view.all() if "Milchgasse" in r.title)
        self.assertEqual(milchgasse.status_label, "Genehmigt, Inkrafttreten nicht belegt")


#: The parcel's own ÖREB extract, as `oereb.details` keeps it: Seon parcel 1268
#: lies in the Gestaltungsplan Giessi, and the cadastre said so on 2026-08-18.
GIESSI_PARCEL = {
    "provisions": [{"title": "Bau- und Nutzungsordnung", "number": "4209", "lawstatus": "inForce"},
                   {"title": "Gestaltungsplan Giessi", "number": "21.259", "lawstatus": "inForce"}],
    "created": "2026-08-18T10:49:35",
}


class ParcelTest(unittest.TestCase):
    """The parcel's ÖREB extract says which plans of which names cover this
    parcel. Whether a listing IS the revision the Amtsblatt published needs
    more than the name: a new plan can keep its predecessor's. Seon's Giessi
    was decided on 05.09.2024, no approval notice turned up, and the cadastre
    lists a "Gestaltungsplan Giessi" (Nr. 21.259) in force on parcel 1268."""

    def view(self, extra=(), parcel=GIESSI_PARCEL):
        pubs, _ = P.publications(SEON + list(extra))
        return P.view(pubs, "Seon", [SEON_BNO], today=TODAY, parcel=parcel)

    def test_a_same_name_listing_does_not_make_a_decided_plan_in_force(self):
        view = self.view()
        giessi = next(r for r in view.unclear if "Giessi" in r.title)
        self.assertEqual(giessi.status_label, "Stand unbestätigt")
        self.assertTrue(giessi.on_parcel)
        self.assertEqual((giessi.oereb.number, giessi.oereb.lawstatus, giessi.oereb.checked_on),
                         ("21.259", "inForce", date(2026, 8, 18)))
        self.assertEqual(giessi.scope_label, "gleichnamiger Plan im ÖREB-Auszug")
        self.assertFalse(any("Giessi" in r.title for r in view.in_force + view.decided))

    def test_a_cited_official_number_is_still_not_this_revision(self):
        """The number names the plan, not the version: a new revision can cite
        the very plan it replaces. No field links a notice to an ÖREB document
        version, so the listing stays a hint."""
        pubs, _ = P.publications([record("2024-09-05", "Gemeinde Seon",
                                         'Gestaltungsplan "Giessi"; Beschluss',
                                         "Der neue Plan ersetzt den Gestaltungsplan Giessi "
                                         "(Nr. 21.259).")])
        giessi = P.view(pubs, "Seon", [SEON_BNO], today=TODAY, parcel=GIESSI_PARCEL).all()[0]
        self.assertEqual(giessi.status_label, "Stand unbestätigt")
        self.assertIsNone(giessi.effective_on)

    def test_an_extract_without_a_legal_status_proves_nothing_about_force(self):
        """Extracts cached before `oereb.details` kept the Lawstatus do not say
        whether a listed plan is in force or a change with pre-effect. That
        Aargau has delivered only `inForce` so far is an observation, not this
        document's status — the panel says the status is unverified."""
        parcel = dict(GIESSI_PARCEL, provisions=[{"title": "Gestaltungsplan Giessi"}])
        view = self.view(parcel=parcel)
        giessi = next(r for r in view.unclear if "Giessi" in r.title)
        self.assertTrue(giessi.on_parcel)
        self.assertEqual(giessi.oereb.lawstatus, "")
        self.assertFalse(any("Giessi" in r.title for r in view.in_force + view.decided))

    def test_a_plan_still_in_mitwirkung_is_never_taken_as_in_force(self):
        """A plan of the same name in the cadastre is a predecessor: a plan
        in Mitwirkung cannot be in force. It does say the area covers this
        parcel."""
        parcel = dict(GIESSI_PARCEL, provisions=GIESSI_PARCEL["provisions"] + [
            {"title": "Gestaltungsplan Heidegrabe", "number": "11.100"}], created="2026-10-01")
        heidegrabe = next(r for r in self.view(parcel=parcel).ongoing if "Heidegrabe" in r.title)
        self.assertEqual(heidegrabe.stage, "verfahren")
        self.assertTrue(heidegrabe.on_parcel)

    def test_an_old_plan_in_force_leaves_a_new_revision_in_its_procedure(self):
        """The predecessor is in force; the revision of the same name is still
        in Mitwirkung or on display, and its row says so."""
        parcel = {"created": "2026-10-01", "provisions": [
            {"title": "Gestaltungsplan Rain", "number": "05.120", "lawstatus": "inForce"}]}
        for title, text, label in (
                ("Öffentliche Mitwirkung zum Gestaltungsplan «Rain»",
                 "Mitwirkung vom 1. September 2026 bis 30. Oktober 2026.", "Entwurf in Mitwirkung"),
                ('Gestaltungsplan "Rain"; öffentliche Auflage',
                 "Die Akten liegen vom 2. September 2026 bis 15. Oktober 2026 auf.",
                 "Entwurf in Auflage")):
            pubs, _ = P.publications([record("2026-09-01", "Gemeinde Seon", title, text)])
            view = P.view(pubs, "Seon", [SEON_BNO], today=TODAY, parcel=parcel)
            self.assertEqual([(r.status_label, r.stage) for r in view.ongoing],
                             [(label, "verfahren")], title)
            self.assertEqual(view.in_force + view.unclear, [], title)

    def test_an_amendment_of_a_listed_plan_is_not_taken_as_in_force(self):
        amendment = record("2025-02-01", "Gemeinde Seon",
                           'Gestaltungsplan "Giessi", Teiländerung; Beschluss')
        view = self.view([amendment])
        change = next(r for r in view.all() if "Teiländerung" in r.title)
        self.assertEqual(change.stage, "beschlossen")
        self.assertTrue(change.on_parcel)
        # The plan it amends is a revision of its own; the extract's listing
        # of that name does not settle it either.
        original = next(r for r in view.all() if r.title == "Gestaltungsplan «Giessi»")
        self.assertEqual(original.status_label, "Stand unbestätigt")

    def test_an_extract_older_than_the_decision_proves_nothing(self):
        parcel = dict(GIESSI_PARCEL, created="2024-08-01")
        giessi = next(r for r in self.view(parcel=parcel).decided if "Giessi" in r.title)
        self.assertEqual(giessi.stage, "beschlossen")

    def test_another_plan_in_the_extract_does_not_put_this_one_on_the_parcel(self):
        parcel = dict(GIESSI_PARCEL, provisions=[{"title": "Gestaltungsplan Breiten"}])
        view = self.view(parcel=parcel)
        self.assertFalse(any(r.on_parcel for r in view.all()))
        heidegrabe = next(r for r in view.ongoing if "Heidegrabe" in r.title)
        self.assertEqual(heidegrabe.scope_label, "Gebiet «Heidegrabe»")

    def test_without_an_extract_nothing_changes(self):
        view = self.view(parcel=None)
        self.assertTrue(any("Giessi" in r.title for r in view.decided))
        self.assertFalse(any(r.on_parcel for r in view.all()))


class CrossLocationTest(unittest.TestCase):
    """A notice that approves plans of two municipalities must not lend one
    municipality's plan to the other's parcels."""

    def test_the_neighbours_plan_in_a_shared_notice_stays_with_the_neighbour(self):
        pubs, _ = P.publications(SEON)
        seon = P.view(pubs, "Seon", [SEON_BNO], today=TODAY)
        reinach = P.view(pubs, "Reinach (AG)", [], today=TODAY)
        seon_titles = " ".join(r.title for r in seon.all())
        reinach_titles = " ".join(r.title for r in reinach.all())
        self.assertNotIn("Friedstrasse", seon_titles)
        self.assertIn("Friedstrasse", reinach_titles)
        for name in ("Giessi", "Heidegrabe", "Aufhebung"):
            self.assertNotIn(name, reinach_titles)

    def test_a_name_that_starts_like_another_is_a_different_municipality(self):
        pubs, _ = P.publications([
            record("2026-05-01", "Gemeinde Wohlenschwil",
                   "Öffentliche Auflage Gestaltungsplan «Dorf»"),
        ])
        self.assertEqual(P.view(pubs, "Wohlen", [], today=TODAY).all(), [])
        self.assertEqual(len(P.view(pubs, "Wohlenschwil", [], today=TODAY).all()), 1)

    def test_canton_wide_planning_changes_reach_every_municipality(self):
        """A Baugesetz revision applies to every parcel in the canton; a health
        law consultation in the same category applies to none of them here."""
        pubs, _ = P.publications([
            record("2026-08-20", "Departement Bau, Verkehr und Umwelt",
                   "Teilrevision Baugesetz; Anhörung", "Anhörung zur Teilrevision des Baugesetzes.",
                   rubric="Kanton / Anhörungs- und Mitwirkungsverfahren"),
            record("2026-08-21", "Departement Gesundheit und Soziales",
                   "Revision Gesundheitsgesetz; Anhörung",
                   rubric="Kanton / Anhörungs- und Mitwirkungsverfahren"),
        ])
        for name in ("Seon", "Möhlin"):
            canton = P.view(pubs, name, [], today=TODAY).canton
            self.assertEqual([r.title for r in canton], ["Teilrevision Baugesetz"])
            self.assertEqual(canton[0].scope_label, "Kanton Aargau")


class RecordTest(unittest.TestCase):
    def test_incomplete_records_are_reported_not_guessed(self):
        pubs, problems = P.publications([
            {"title": "Ohne Datum", "authority": "Gemeinde Seon",
             "url": "https://amtsblatt.ag.ch/ekab/1/pdf/"},
            {"published_on": "2026-01-01", "authority": "Gemeinde Seon", "title": "Ohne Link"},
            {"published_on": "2026-01-01", "authority": "Gemeinde Seon", "title": "Kein Web-Link",
             "url": "javascript:alert(1)"},
        ])
        self.assertEqual(pubs, [])
        self.assertEqual(len(problems), 3)

    def test_a_canton_notice_that_names_no_municipality_in_the_usual_form_is_reported(self):
        """Attributing by a loose mention ("im Gebiet der Gemeinden Seon und
        Seengen") is exactly how a plan ends up on the wrong parcels."""
        pubs, problems = P.publications([
            record("2026-03-01", "Abteilung Raumentwicklung", "Genehmigung von Sondernutzungsplänen",
                   "Genehmigt wurde ein Plan im Gebiet der Gemeinden Seon und Seengen."),
        ])
        self.assertEqual(pubs, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("keiner Gemeinde zugeordnet", problems[0])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.path = os.path.join(self.tempdir.name, "publications.json")
        P.reset_cache()
        self.addCleanup(P.reset_cache)

    def test_switched_off_unless_a_store_is_configured(self):
        self.assertEqual(P.load_store(path="").status, "off")
        self.assertEqual(P.state_for("Seon", [], path="").status, "off")

    def test_a_missing_store_is_an_error_not_an_empty_list(self):
        """"No ongoing procedures" and "could not read the list" must never
        look the same."""
        view = P.state_for("Seon", [], path=self.path, today=TODAY)
        self.assertEqual(view.status, "error")
        self.assertTrue(view.error)

    def test_a_malformed_store_is_an_error(self):
        with open(self.path, "w") as handle:
            handle.write("{not json")
        self.assertEqual(P.state_for("Seon", [], path=self.path).status, "error")

    def test_a_readable_store_gives_its_date(self):
        with open(self.path, "w") as handle:
            json.dump({"stand": "2026-10-05", "records": SEON}, handle)
        view = P.state_for("Seon", [SEON_BNO], path=self.path, today=TODAY)
        self.assertEqual(view.status, "ok")
        self.assertEqual(view.stand, date(2026, 10, 5))
        self.assertTrue(any("Heidegrabe" in p.title for p in view.publications))

    def test_a_store_with_nothing_for_this_municipality_is_ok_and_empty(self):
        with open(self.path, "w") as handle:
            json.dump({"stand": "2026-10-05", "records": SEON}, handle)
        view = P.state_for("Möhlin", [], path=self.path, today=TODAY)
        self.assertEqual(view.status, "ok")
        self.assertEqual(view.all(), [])


def canton(published_on, text, title="Genehmigung von Sondernutzungsplänen",
           authority="Abteilung Raumentwicklung", rubric="Kanton / Raumplanung"):
    return record(published_on, authority, title, text, rubric=rubric)


def titles(view):
    return [r.title for r in view.all()]


class SegmentTest(unittest.TestCase):
    """A canton notice is split into one part per municipality; whatever cannot
    be attributed to exactly one is reported, never appended to a neighbour."""

    def test_a_plural_part_does_not_run_into_the_previous_municipality(self):
        pubs, problems = P.publications([canton(
            "2026-03-01", 'Das Departement hat genehmigt: | Gemeinde Seon: Gestaltungsplan '
            '"Giessi" | Gemeinden Reinach und Menziken: Gestaltungsplan "Friedstrasse"')])
        self.assertEqual(titles(P.view(pubs, "Seon", today=TODAY)), ["Gestaltungsplan «Giessi»"])
        self.assertEqual(titles(P.view(pubs, "Reinach (AG)", today=TODAY)), [])
        self.assertEqual(len(problems), 1)
        self.assertIn("Reinach", problems[0])

    def test_a_plural_without_a_paragraph_break_still_ends_the_part(self):
        pubs, problems = P.publications([canton(
            "2026-03-01", 'Gemeinde Seon: Gestaltungsplan "Giessi" Gemeinden Reinach und '
            'Menziken: Gestaltungsplan "Friedstrasse"')])
        self.assertEqual(titles(P.view(pubs, "Seon", today=TODAY)), ["Gestaltungsplan «Giessi»"])
        self.assertEqual(len(problems), 1)

    def test_a_part_the_search_list_cut_off_says_so(self):
        """The teaser ends mid-word ("Erschliessungsp […]"); the title shows
        that it is cut rather than presenting the fragment as the name."""
        pubs, _ = P.publications([canton("2019-10-18", "Gemeinde Seon: Erschliessungsp […]")])
        self.assertEqual(titles(P.view(pubs, "Seon", today=TODAY)), ["Erschliessungsp…"])

    def test_an_einwohnergemeinde_part_is_its_own_municipality(self):
        pubs, _ = P.publications([canton(
            "2026-03-01", '| Gemeinde Reinach: Gestaltungsplan "Friedstrasse" | '
            'Einwohnergemeinde Seon: Gestaltungsplan "Bahnhof"')])
        self.assertEqual(titles(P.view(pubs, "Reinach (AG)", today=TODAY)),
                         ["Gestaltungsplan «Friedstrasse»"])
        self.assertEqual(titles(P.view(pubs, "Seon", today=TODAY)), ["Gestaltungsplan «Bahnhof»"])

    def test_a_municipality_named_mid_sentence_ends_the_part_before_it(self):
        pubs, problems = P.publications([canton(
            "2026-03-01", 'Gemeinde Reinach: Gestaltungsplan "Friedstrasse", im Anschluss an '
            "den Plan der Gemeinde Seon")])
        self.assertEqual(titles(P.view(pubs, "Reinach (AG)", today=TODAY)),
                         ["Gestaltungsplan «Friedstrasse»"])
        self.assertEqual(titles(P.view(pubs, "Seon", today=TODAY)), [])
        self.assertTrue(any("Seon" in p for p in problems))


class CantonWideTest(unittest.TestCase):
    """Canton-wide means "applies to every parcel in Aargau" — only on positive
    evidence, never because nothing else matched."""

    def test_a_canton_instrument_naming_a_municipality_is_not_canton_wide(self):
        """"Richtplananpassung Seon" is the canton's instrument, about Seon: on
        every parcel in Aargau it would be another municipality's change."""
        pubs, problems = P.publications([canton(
            "2026-09-15", "Anhörung zur Richtplananpassung.", title="Richtplananpassung Seon; Anhörung",
            authority="Departement Bau, Verkehr und Umwelt",
            rubric="Kanton / Anhörungs- und Mitwirkungsverfahren")])
        self.assertEqual([p for p in pubs if not p.municipality], [])
        self.assertEqual(len(problems), 1)

    def assert_nowhere(self, rec, mentioned=None):
        pubs, problems = P.publications([rec])
        for name in ("Seon", "Möhlin"):
            self.assertEqual(P.view(pubs, name, today=TODAY).all(), [], name)
        self.assertEqual(len(problems), 1, problems)
        if mentioned:
            self.assertIn(mentioned, problems[0])

    def test_a_cantonal_plan_located_in_one_municipality_is_not_canton_wide(self):
        self.assert_nowhere(canton("2026-09-01", "Der Plan liegt öffentlich auf.",
                                   title="Kantonaler Nutzungsplan Deponie Babilon, Seon; "
                                         "öffentliche Auflage"), mentioned="Seon")

    def test_an_approval_cut_off_before_its_municipalities_is_not_canton_wide(self):
        self.assert_nowhere(canton("2026-09-01", "Das Departement Bau, Verkehr und Umwelt hat "
                                   "gestützt auf § 27 BauG nachstehende Sondernutzungspläne "
                                   "genehmigt: […]"))

    def test_an_approval_citing_the_baugesetz_in_its_title_is_not_canton_wide(self):
        """Canton approvals of municipal plans cite their legal basis in the
        title. Cut off before its municipalities, such a notice is reported —
        not shown on every parcel in Aargau as a change of the Baugesetz."""
        self.assert_nowhere(canton("2026-09-01", "Das Departement Bau, Verkehr und Umwelt hat "
                                   "nachstehende Nutzungspläne genehmigt: […]",
                                   title="Genehmigung von Nutzungsplänen gemäss § 27 BauG"))

    def test_an_approval_naming_only_the_law_it_applies_is_not_canton_wide(self):
        """The Baugesetz is enacted, not approved: an approval citing it
        approves something of a municipality's."""
        self.assert_nowhere(canton("2026-09-01", "Genehmigt wurden: […]",
                                   title="Genehmigungen gemäss § 27 BauG"))

    def test_an_approved_richtplan_change_reaches_every_parcel(self):
        pubs, problems = P.publications([canton(
            "2026-09-01", "Der Bundesrat hat die Anpassung des Richtplans genehmigt.",
            title="Richtplananpassung Siedlungsgebiet; Genehmigung durch den Bundesrat",
            authority="Departement Bau, Verkehr und Umwelt")])
        self.assertEqual(problems, [])
        self.assertEqual([(r.title, r.status_label) for r in P.view(pubs, "Möhlin",
                                                                     today=TODAY).canton],
                         [("Richtplananpassung Siedlungsgebiet",
                           "Genehmigt, Inkrafttreten nicht belegt")])

    def test_a_cantonal_planungszone_is_never_canton_wide(self):
        """A zone covers a designated area (§ 29 Abs. 1 BauG), never Aargau."""
        self.assert_nowhere(canton("2026-09-01", "Die Planungszone liegt öffentlich auf.",
                                   title="Planungszone für das Richtplanvorhaben Hochwasserschutz",
                                   authority="Regierungsrat"))

    def test_an_approval_or_refusal_is_canton_wide_by_its_title_only(self):
        """The text cannot turn an approval notice into a canton-wide change."""
        for title, text in (("Genehmigungsentscheid gemäss § 27 BauG",
                             "Die Genehmigung für die Gemeinde Seon wird verweigert."),
                            ("Nichtgenehmigung gemäss § 27 BauG",
                             "Die Gemeinde Seon hat die Teiländerung zur Genehmigung eingereicht.")):
            self.assert_nowhere(canton("2026-09-01", text, title=title), mentioned="Seon")

    def test_a_municipal_category_from_an_unknown_office_is_not_canton_wide(self):
        self.assert_nowhere(record("2026-09-01", "Abteilung Planung und Bau",
                                   "Öffentliche Auflage Gestaltungsplan «Rain»",
                                   rubric="Gemeinden / Bau- und Nutzungsordnung"))

    def test_a_planning_topic_outside_the_canton_wide_list_is_reported(self):
        """A Gewässerraum consultation concerns the parcels along one river,
        not every parcel in Aargau."""
        self.assert_nowhere(canton("2026-09-01", "Anhörung zum Gewässerraum der Aare.",
                                   title="Gewässerraum Aare; Anhörung",
                                   authority="Departement Bau, Verkehr und Umwelt",
                                   rubric="Kanton / Anhörungs- und Mitwirkungsverfahren"))

    def test_a_richtplan_consultation_naming_no_municipality_is_canton_wide(self):
        pubs, problems = P.publications([canton(
            "2026-08-01", "Anhörung zur Anpassung des Richtplans, Kapitel Siedlungsgebiet.",
            title="Richtplananpassung Siedlungsgebiet; Anhörung",
            authority="Departement Bau, Verkehr und Umwelt",
            rubric="Kanton / Anhörungs- und Mitwirkungsverfahren")])
        self.assertEqual(problems, [])
        self.assertEqual(titles(P.view(pubs, "Möhlin", today=TODAY)),
                         ["Richtplananpassung Siedlungsgebiet"])

    def test_an_old_canton_consultation_stays_listed_as_unverified(self):
        pubs, _ = P.publications([canton(
            "2019-05-01", "Anhörung zur Teilrevision des Baugesetzes, vom 1. Mai 2019 bis "
            "31. Juli 2019.", title="Teilrevision Baugesetz; Anhörung",
            authority="Departement Bau, Verkehr und Umwelt",
            rubric="Kanton / Anhörungs- und Mitwirkungsverfahren")])
        self.assertEqual([r.status_label for r in P.view(pubs, "Möhlin", today=TODAY).canton],
                         ["Stand unbestätigt"])

    def test_relevant_canton_wide_changes_name_no_municipality(self):
        """A Baugesetz consultation under way, a Bauverordnung change with its
        in-force date set, a Richtplan decision of the Grosser Rat: none of
        them names a municipality, all of them reach every parcel."""
        pubs, problems = P.publications([
            canton("2026-09-15", "Anhörung zur Teilrevision des Baugesetzes vom 15. September "
                   "2026 bis 15. Dezember 2026.", title="Teilrevision Baugesetz (BauG); Anhörung",
                   authority="Departement Bau, Verkehr und Umwelt",
                   rubric="Kanton / Anhörungs- und Mitwirkungsverfahren"),
            canton("2026-08-20", "Die Änderung der Bauverordnung tritt am 1. Januar 2027 in Kraft.",
                   title="Bauverordnung (BauV), Änderung; Inkraftsetzung", authority="Regierungsrat",
                   rubric="Kanton / Verschiedenes"),
            canton("2026-06-10", "Der Grosse Rat hat die Anpassung des Richtplans beschlossen.",
                   title="Richtplan, Kapitel Siedlungsgebiet; Beschluss", authority="Grosser Rat",
                   rubric="Kanton / Raumplanung"),
        ])
        self.assertEqual(problems, [])
        for name in ("Seon", "Möhlin", "Dintikon"):
            canton_rows = {r.title: r.status_label for r in P.view(pubs, name, today=TODAY).canton}
            self.assertEqual(canton_rows, {
                "Teilrevision Baugesetz (BauG)": "Entwurf in Anhörung",
                "Bauverordnung (BauV), Änderung": "Inkrafttreten festgelegt",
                "Richtplan, Kapitel Siedlungsgebiet": "Beschlossen",
            }, name)


class ChainFindingTest(unittest.TestCase):
    """Publications join one revision only when they are the same procedure."""

    def view(self, recs, parcel=None):
        pubs, _ = P.publications(recs)
        return P.view(pubs, "Seon", today=TODAY, parcel=parcel)

    def test_an_approval_naming_another_plan_does_not_close_this_one(self):
        view = self.view([
            record("2024-09-05", "Gemeinde Seon", 'Gestaltungsplan "Giessi"; Beschluss'),
            canton("2025-03-14", 'Gemeinde Seon: Gestaltungsplan "Bahnhofareal"'),
        ])
        self.assertEqual([r.title for r in view.decided], ["Gestaltungsplan «Giessi»"])
        self.assertEqual([r.title for r in view.approved], ["Gestaltungsplan «Bahnhofareal»"])

    def test_every_part_of_the_title_names_the_revision(self):
        view = self.view([
            record("2026-06-01", "Gemeinde Seon",
                   "Nutzungsplanung; Teiländerung Zentrum; Beschluss der Gemeindeversammlung"),
            record("2026-08-01", "Gemeinde Seon",
                   "Nutzungsplanung; Gesamtrevision; öffentliche Auflage"),
        ])
        self.assertEqual([r.title for r in view.decided], ["Nutzungsplanung, Teiländerung Zentrum"])
        self.assertEqual(view.decided[0].scope_label, "Teilbereich «Zentrum»")
        self.assertEqual([r.title for r in view.unclear], ["Nutzungsplanung, Gesamtrevision"])

    def test_a_later_approval_of_the_same_name_is_a_new_revision(self):
        """Approved and in force in 2015; approved again in 2026 without a date.
        One chain would lend the new approval the old date."""
        old = canton("2015-06-01", 'Gemeinde Seon: Gestaltungsplan "Rain"; Genehmigung. Der Plan '
                                   'tritt am 1. Juli 2015 in Kraft.')
        for later, label in (
                (canton("2026-09-25", 'Gemeinde Seon: Gestaltungsplan "Rain"; Genehmigung.'),
                 "Genehmigt, Inkrafttreten nicht belegt"),
                (record("2026-09-25", "Gemeinde Seon", 'Gestaltungsplan "Rain"; Beschluss'),
                 "Beschlossen")):
            pubs, _ = P.publications([old, later])
            view = P.view(pubs, "Seon", today=TODAY)
            self.assertEqual(sorted((r.published_on.year, r.status_label) for r in view.all()),
                             [(2015, "In Kraft getreten"), (2026, label)], label)

    def test_an_inkraftsetzung_years_after_an_approval_is_another_procedure(self):
        pubs, _ = P.publications([
            canton("2015-06-01", 'Gemeinde Seon: Gestaltungsplan "Rain"; Genehmigung. Der Plan tritt am '
                                 '1. Juli 2015 in Kraft.'),
            record("2026-09-25", "Gemeinde Seon", 'Gestaltungsplan "Rain"; Inkraftsetzung',
                   "Inkrafttreten: 1. Januar 2027.")])
        view = P.view(pubs, "Seon", today=TODAY)
        self.assertEqual(sorted((r.published_on.year, r.status_label) for r in view.all()),
                         [(2015, "In Kraft getreten"), (2026, "Stand unbestätigt")])

    def test_a_decision_years_after_a_decision_is_another_procedure(self):
        pubs, _ = P.publications([
            record("2015-06-01", "Gemeinde Seon", 'Gestaltungsplan "Rain"; Beschluss',
                   "Der Plan tritt am 1. Juli 2015 in Kraft."),
            record("2026-09-25", "Gemeinde Seon", 'Gestaltungsplan "Rain"; Beschluss')])
        view = P.view(pubs, "Seon", today=TODAY)
        newer = next(r for r in view.all() if r.published_on.year == 2026)
        self.assertEqual(len(view.all()), 2)
        self.assertIsNone(newer.planned_on)

    def test_an_approval_months_after_its_decision_is_the_same_procedure(self):
        pubs, _ = P.publications([
            record("2025-10-01", "Gemeinde Seon", 'Gestaltungsplan "Rain"; Beschluss'),
            canton("2026-06-01", 'Gemeinde Seon: Gestaltungsplan "Rain"; Genehmigung.')])
        self.assertEqual(len(P.view(pubs, "Seon", today=TODAY).all()), 1)

    def test_two_notices_of_one_approval_stay_one_revision(self):
        pubs, _ = P.publications([
            canton("2026-09-01", 'Gemeinde Seon: Gestaltungsplan "Rain"; Genehmigung.'),
            record("2026-09-10", "Gemeinde Seon",
                   'Gestaltungsplan "Rain"; Genehmigung durch den Regierungsrat')])
        self.assertEqual(len(P.view(pubs, "Seon", today=TODAY).all()), 1)

    def test_an_amendment_is_its_own_revision(self):
        view = self.view([
            canton("2020-05-01", 'Gemeinde Seon: Gestaltungsplan "Giessi"; Inkrafttreten am 1. Juli 2020'),
            record("2026-03-01", "Gemeinde Seon", 'Gestaltungsplan "Giessi", Teiländerung; Mitwirkung'),
        ])
        # The amendment's Mitwirkung states no period: listed, state unverified.
        self.assertEqual([r.title for r in view.unclear], ["Gestaltungsplan «Giessi», Teiländerung"])
        self.assertEqual([r.title for r in view.in_force], ["Gestaltungsplan «Giessi»"])

    def test_an_amendment_decided_without_mitwirkung_is_still_its_own_revision(self):
        """Small amendments go straight to a Beschluss. Nothing about the order
        of steps tells them apart from the plan — only the word "Teiländerung"
        does, and the approved plan must stay approved."""
        view = self.view([
            canton("2023-05-01", 'Gemeinde Seon: Gestaltungsplan "Giessi"'),
            record("2026-02-01", "Gemeinde Seon", 'Gestaltungsplan "Giessi", Teiländerung; Beschluss'),
        ])
        self.assertEqual([r.title for r in view.decided], ["Gestaltungsplan «Giessi», Teiländerung"])
        self.assertEqual([r.title for r in view.approved], ["Gestaltungsplan «Giessi»"])

    def test_a_repeal_is_not_the_plan_it_repeals(self):
        view = self.view([
            record("2021-07-01", "Gemeinde Seon", 'Gestaltungsplan "Giessi"; Mitwirkungsverfahren'),
            record("2026-05-01", "Gemeinde Seon", 'Aufhebung Gestaltungsplan "Giessi"; öffentliche Auflage'),
        ])
        self.assertIn("Aufhebung Gestaltungsplan «Giessi»", [r.title for r in view.all()])
        self.assertIn("Gestaltungsplan «Giessi»", [r.title for r in view.all()])

    def test_the_same_plan_quoted_and_unquoted_is_one_revision(self):
        view = self.view([
            record("2024-02-01", "Gemeinde Seon", "Gestaltungsplan Giessi; Mitwirkung"),
            record("2024-09-05", "Gemeinde Seon", 'Gestaltungsplan "Giessi"; Beschluss'),
        ])
        self.assertEqual(len(view.all()), 1, titles(view))

    def test_an_incidental_quote_in_the_text_does_not_merge_two_plans(self):
        text = "Grundlage ist das «Räumliche Entwicklungsleitbild» der Gemeinde."
        view = self.view([
            record("2026-02-01", "Gemeinde Seon", "Teiländerung Nutzungsplanung Dorf; Mitwirkung", text),
            record("2026-03-01", "Gemeinde Seon", "Teiländerung Nutzungsplanung Bahnhof; Mitwirkung", text),
        ])
        self.assertEqual(len(view.all()), 2, titles(view))


class StepFindingTest(unittest.TestCase):
    def test_a_future_step_in_the_text_is_not_where_things_stand(self):
        self.assertEqual(P.step_of("Gestaltungsplan Musterwiese",
                                   "Der Plan wird öffentlich aufgelegt. Anschliessend wird er "
                                   "dem Kanton zur Genehmigung unterbreitet."), "unbekannt")

    def test_a_decision_reported_only_in_the_text_is_not_taken_as_the_step(self):
        for text in ("Der Gemeinderat hat beschlossen, den Plan öffentlich aufzulegen.",
                     "Der ursprüngliche Plan wurde 2010 genehmigt.",
                     "Der Plan wird zur Genehmigung eingereicht."):
            self.assertEqual(P.step_of("Gestaltungsplan Musterwiese", text), "unbekannt", text)

    def test_approval_by_the_municipal_assembly_is_a_decision(self):
        self.assertEqual(P.step_of("Gestaltungsplan Musterwiese; Genehmigung durch die "
                                   "Gemeindeversammlung"), "beschluss")

    def test_a_refused_approval_is_not_an_approval(self):
        self.assertEqual(P.step_of("Nichtgenehmigung des Gestaltungsplans Musterwiese"), "verweigert")
        self.assertEqual(P.step_of("Gestaltungsplan Musterwiese; nicht genehmigt"), "verweigert")

    def test_a_refusal_is_named_and_claims_no_state(self):
        pubs, _ = P.publications([record("2026-08-01", "Gemeinde Seon",
                                         'Gestaltungsplan "Rain"; Nichtgenehmigung')])
        revision = P.view(pubs, "Seon", today=TODAY).all()[0]
        self.assertEqual(revision.history, "Nichtgenehmigung, publiziert 01.08.2026")
        self.assertEqual(revision.status_label, "Stand unbestätigt")

    def test_the_usual_wordings_of_an_in_force_date(self):
        for text in ("Die Teiländerung wird auf den 1. Januar 2027 in Kraft gesetzt.",
                     "Die Pläne werden per 01.01.2027 in Kraft gesetzt.",
                     "Inkrafttreten per 1. Januar 2027."):
            pubs, _ = P.publications([record("2026-09-01", "Gemeinde Seon",
                                             "Teiländerung Zentrum; Inkraftsetzung", text)])
            self.assertEqual(pubs[0].effective_on, date(2027, 1, 1), text)
            self.assertEqual(P.view(pubs, "Seon", today=TODAY).approved[0].status_label,
                             "Inkrafttreten festgelegt")

    def test_a_planungszone_binds_from_its_publication(self):
        """§ 29 Abs. 2 BauG: a planning zone restricts building from its
        public display — it is not a step on the way, it is in force."""
        pubs, _ = P.publications([record("2026-08-01", "Gemeinde Seon",
                                         "Erlass einer Planungszone «Oberdorf»",
                                         "Der Plan wird öffentlich aufgelegt.")])
        view = P.view(pubs, "Seon", today=TODAY)
        self.assertEqual([(r.title, r.status_label) for r in view.pending()],
                         [("Planungszone «Oberdorf»", "Planungszone in Kraft")])
        self.assertEqual(view.in_force, [])


class DateTest(unittest.TestCase):
    """Four dates, never one for another: when a notice was published, when
    the act it reports was decided, when a plan takes force, and when the
    cadastre was read."""

    def test_a_decision_date_is_read_apart_from_the_publication(self):
        pubs, _ = P.publications([record(
            "2024-09-05", "Gemeinde Seon", "Aufhebung von Überbauungsplänen; Beschluss",
            'Der Gemeinderat hat am 2. September 2024 die Aufhebung der kommunalen Überbauungspläne '
            '"Weg- und Baulinienplan Überbauung Breiten" vom 6. Juli 1973 beschlossen.')])
        self.assertEqual((pubs[0].published_on, pubs[0].decided_on),
                         (date(2024, 9, 5), date(2024, 9, 2)))
        self.assertIsNone(pubs[0].effective_on)

    def test_a_municipal_assembly_date_is_the_decision_date(self):
        pubs, _ = P.publications([record("2026-06-20", "Gemeinde Seon",
                                         "Gemeindeversammlung vom 12.06.2026: Genehmigung "
                                         "Teiländerung Dorf")])
        self.assertEqual(pubs[0].decided_on, date(2026, 6, 12))

    def test_the_canton_approval_date_is_each_listed_plans_decision_date(self):
        pubs, _ = P.publications([canton(
            "2025-01-17", "Das Departement Bau, Verkehr und Umwelt hat am 13. Januar 2025 nachstehende "
            'Sondernutzungspläne genehmigt: | Gemeinde Reinach: Gestaltungsplan "Friedstrasse" | '
            'Gemeinde Seon: Erschliessungsplan "Milchgasse"')])
        self.assertEqual([(p.municipality, p.decided_on, p.effective_on) for p in pubs],
                         [("Reinach", date(2025, 1, 13), None), ("Seon", date(2025, 1, 13), None)])

    def test_a_date_after_the_list_is_not_every_plans_decision_date(self):
        pubs, _ = P.publications([canton(
            "2026-04-01", 'Genehmigt wurden: | Gemeinde Seon: Gestaltungsplan "Rain" | Gemeinde '
            'Reinach: Gestaltungsplan "Hof" | Der Regierungsrat hat am 3. März 2026 die Änderung '
            'genehmigt.')])
        self.assertEqual([p.decided_on for p in pubs], [None, None])

    def test_an_in_force_date_of_another_act_is_not_this_ones(self):
        """Only an announcement counts, and only one the act can make: not a
        date a parenthesis or a "seit dem" refers to, not a date before the
        approval that would set it. Each case below is caught by one rule."""
        for text in (
                # a parenthesis about another plan
                'Gemeinde Seon: Gestaltungsplan "Rain", Teiländerung; Genehmigung (Gestaltungsplan '
                'Rain, Inkrafttreten: 1. September 2026)',
                # "seit dem" looks back
                'Gemeinde Seon: Gestaltungsplan "Rain"; Genehmigung. Seit dem Inkrafttreten am '
                '1. September 2026 gilt der bisherige Plan.',
                # before the approval's own decision
                'Der Regierungsrat hat am 9. September 2026 genehmigt: | Gemeinde Seon; Gestaltungsplan '
                '"Rain"; Genehmigung. Der Plan tritt am 1. September 2026 in Kraft.',
                # long before the notice, no decision date given
                'Gemeinde Seon: Gestaltungsplan "Rain"; Genehmigung. Der Plan tritt am 1. Juli 2010 '
                'in Kraft.'):
            pubs, _ = P.publications([canton("2026-09-20", text)])
            self.assertIsNone(pubs[0].effective_on, text)

    def test_a_looking_back_in_force_date_of_any_form_is_not_this_ones(self):
        for text in ('Gemeinde Seon: Gestaltungsplan "Rain"; Genehmigung. Seit seinem Inkrafttreten am '
                     '1. September 2026 gilt der Plan.',
                     'Gemeinde Seon: Gestaltungsplan "Rain"; Genehmigung. Nach Inkrafttreten am '
                     '1. September 2026 gilt der Plan.'):
            pubs, _ = P.publications([canton("2026-09-20", text)])
            self.assertIsNone(pubs[0].effective_on, text)

    def test_a_dropped_date_does_not_hide_the_announced_one(self):
        pubs, _ = P.publications([canton(
            "2026-09-20", 'Gemeinde Seon: Gestaltungsplan "Rain"; Genehmigung. Der frühere Plan '
            '(Inkrafttreten per 1. Juli 2010) wird ersetzt. Der neue Plan tritt am 1. November 2026 '
            'in Kraft.')])
        self.assertEqual(pubs[0].effective_on, date(2026, 11, 1))

    def test_a_decisions_planned_date_is_not_the_approvals_in_force_date(self):
        """The municipality planned 1 January 2026; the canton approved in
        September 2026 and gave no date. Approved, in force unshown."""
        pubs, _ = P.publications([
            record("2025-10-01", "Gemeinde Seon", 'Gestaltungsplan "Rain"; Beschluss',
                   "Der Plan tritt am 1. Januar 2026 in Kraft."),
            canton("2026-09-25", 'Gemeinde Seon: Gestaltungsplan "Rain"; Genehmigung.')])
        rain = P.view(pubs, "Seon", today=TODAY).all()[0]
        self.assertEqual(rain.status_label, "Genehmigt, Inkrafttreten nicht belegt")
        self.assertIsNone(rain.effective_on)
        self.assertEqual(rain.planned_on, date(2026, 1, 1))

    def test_the_decision_date_is_the_reported_acts(self):
        pubs, _ = P.publications([record(
            "2026-09-05", "Gemeinde Seon", "Aufhebung des Überbauungsplans; Beschluss",
            "Die Gemeindeversammlung vom 6. Juli 1973 hat den Überbauungsplan erlassen. Der "
            "Gemeinderat hat an seiner Sitzung vom 2. September 2026 die Aufhebung beschlossen.")])
        self.assertEqual(pubs[0].decided_on, date(2026, 9, 2))
        pubs, _ = P.publications([canton(
            "2026-09-15", "Gemeinde Seon: Die von der Gemeindeversammlung vom 12. Juni 2026 "
            "beschlossene Teiländerung Dorf hat der Regierungsrat am 9. September 2026 genehmigt.",
            title="Genehmigung von Nutzungsplänen", authority="Regierungsrat")])
        self.assertEqual(pubs[0].decided_on, date(2026, 9, 9))

    def test_a_date_inside_the_object_of_the_sentence_is_not_the_decision_date(self):
        for text, title, kind in (
                ("Gemeinde Seon: Der Regierungsrat hat die von der Gemeindeversammlung am 12. Juni "
                 "2026 beschlossene Teiländerung Dorf genehmigt.", "Genehmigung von Nutzungsplänen",
                 "canton"),
                ("Der Gemeinderat hat die Aufhebung des Überbauungsplans vom 6. Juli 1973 beschlossen.",
                 "Aufhebung des Überbauungsplans; Beschluss", "record")):
            rec = (canton("2026-09-15", text, title=title, authority="Regierungsrat") if kind == "canton"
                   else record("2026-09-15", "Gemeinde Seon", title, text))
            pubs, _ = P.publications([rec])
            self.assertIsNone(pubs[0].decided_on, text)

    def test_a_decision_to_put_a_plan_on_display_is_not_the_plans_decision(self):
        pubs, _ = P.publications([record(
            "2026-09-01", "Gemeinde Seon", 'Gestaltungsplan "Rain"; öffentliche Auflage',
            "Der Gemeinderat hat am 25. August 2026 beschlossen, den Plan öffentlich aufzulegen.")])
        self.assertIsNone(pubs[0].decided_on)

    def test_the_history_says_which_date_is_which(self):
        pubs, _ = P.publications([
            record("2024-06-27", "Gemeinde Seon", 'Gestaltungsplan "Breiten"; öffentliche Auflage'),
            record("2024-09-05", "Gemeinde Seon", 'Gestaltungsplan "Breiten"; Beschluss',
                   "Der Gemeinderat hat am 2. September 2024 den Plan beschlossen."),
        ])
        self.assertEqual(P.view(pubs, "Seon", today=TODAY).all()[0].history,
                         "Öffentliche Auflage, publiziert 27.06.2024 → Beschluss vom 02.09.2024, "
                         "publiziert 05.09.2024")

    def test_a_partial_revision_near_an_edition_is_not_its_approval_without_a_date(self):
        """Gesamtrevision and edition: one change. A partial revision approved
        around the time of an edition may be another change — merged only
        when its decision date is the edition's in-force date."""
        approval = "Gemeinde Seon; Nutzungsplanung, Teilrevision Zentrum; Genehmigung"
        bno = R.Edict(municipality="Seon", title="Bau- und Nutzungsordnung", abbreviation="BNO",
                      in_force=date(2026, 9, 1), syst_nr="4209", document="")
        pubs, _ = P.publications([canton("2026-09-10", approval, title="Genehmigung von "
                                         "Nutzungsplänen", authority="Regierungsrat")])
        view = P.view(pubs, "Seon", [bno], today=TODAY)
        self.assertEqual(view.approvals, {})
        self.assertEqual([r.status_label for r in view.approved],
                         ["Genehmigt, Inkrafttreten nicht belegt"])
        pubs, _ = P.publications([canton(
            "2026-09-10", "Der Regierungsrat hat am 1. September 2026 folgende Nutzungsplanung "
            "genehmigt: | " + approval, title="Genehmigung von Nutzungsplänen",
            authority="Regierungsrat")])
        self.assertIn(bno, P.view(pubs, "Seon", [bno], today=TODAY).approvals)


class PublicationListTest(unittest.TestCase):
    """Version 1 shows publications as they were printed: each its own item,
    none merged, no status inferred. The inference path is a switch, OFF."""

    def test_the_municipalitys_publications_are_listed_apart_newest_first(self):
        pubs, _ = P.publications([
            record("2024-06-27", "Gemeinde Seon", 'Gestaltungsplan "Rain"; öffentliche Auflage'),
            record("2024-09-05", "Gemeinde Seon", 'Gestaltungsplan "Rain"; Beschluss'),
            record("2025-01-01", "Gemeinde Möhlin", 'Gestaltungsplan "Hof"; Beschluss'),
            canton("2026-09-15", "Anhörung zur Teilrevision des Baugesetzes.",
                   title="Teilrevision Baugesetz (BauG); Anhörung",
                   authority="Departement Bau, Verkehr und Umwelt",
                   rubric="Kanton / Anhörungs- und Mitwirkungsverfahren"),
        ])
        view = P.view(pubs, "Seon", today=TODAY)
        self.assertEqual([(p.published_on, p.step) for p in view.publications],
                         [(date(2024, 9, 5), "beschluss"), (date(2024, 6, 27), "auflage")])
        self.assertEqual([p.title for p in view.canton_publications],
                         ["Teilrevision Baugesetz (BauG); Anhörung"])
        self.assertFalse(view.inference)

    def test_the_inference_path_is_off_unless_switched_on(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "store.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"stand": "2026-10-05", "since": "2020-01-01", "records": []}, handle)
            saved = os.environ.pop(P.ENV_INFERENCE, None)
            try:
                P.reset_cache()
                self.assertFalse(P.state_for("Seon", [], path=path).inference)
                for value in ("0", "", "nein"):
                    os.environ[P.ENV_INFERENCE] = value
                    self.assertFalse(P.state_for("Seon", [], path=path).inference, value)
                os.environ[P.ENV_INFERENCE] = "1"
                self.assertTrue(P.state_for("Seon", [], path=path).inference)
            finally:
                os.environ.pop(P.ENV_INFERENCE, None)
                if saved is not None:
                    os.environ[P.ENV_INFERENCE] = saved
                P.reset_cache()


class PublicationTitleTest(unittest.TestCase):
    """Version 1 reads no step from a publication. Its title is the notice's
    own — for a canton notice split per municipality, the part naming this
    one, its own words kept: "Genehmigung verweigert" included."""

    def one(self, record_):
        pubs, problems = P.publications([record_])
        self.assertEqual((len(pubs), problems), (1, []), record_["text"])
        return pubs[0]

    def test_a_canton_notices_part_keeps_its_own_words(self):
        for part, title in (
                ('Gemeinde Seon: Gestaltungsplan "Giessi"; Genehmigung verweigert',
                 'Gestaltungsplan "Giessi"; Genehmigung verweigert'),
                ('Gemeinde Seon: Gestaltungsplan "Giessi". Die Genehmigung wird verweigert.',
                 'Gestaltungsplan "Giessi". Die Genehmigung wird verweigert.'),
                ("Gemeinde Seon: Verweigerung der Genehmigung der Teiländerung Dorf",
                 "Verweigerung der Genehmigung der Teiländerung Dorf"),
                ('Gemeinde Seon: Gestaltungsplan "Rain"', 'Gestaltungsplan "Rain"'),
                ("Gemeinde Seon; Allgemeine Nutzungsplanung, Gesamtrevision; Genehmigung",
                 "Allgemeine Nutzungsplanung, Gesamtrevision; Genehmigung")):
            self.assertEqual(P.publication_title(self.one(canton("2026-03-05", part))), title, part)

    def test_any_other_notice_keeps_its_title(self):
        own = self.one(record("2026-09-01", "Gemeinde Seon", "Öffentliche Auflage Gestaltungsplan «Giessi»",
                              "Die erste Fassung wurde 2023 nicht genehmigt."))
        self.assertEqual(P.publication_title(own), "Öffentliche Auflage Gestaltungsplan «Giessi»")
        wide = self.one(canton("2026-09-15", "Anhörung zur Teilrevision des Baugesetzes.",
                               title="Teilrevision Baugesetz (BauG); Anhörung",
                               authority="Departement Bau, Verkehr und Umwelt",
                               rubric="Kanton / Anhörungs- und Mitwirkungsverfahren"))
        self.assertEqual(P.publication_title(wide), "Teilrevision Baugesetz (BauG); Anhörung")

    def test_a_neighbour_named_in_decomposed_or_hyphenated_form_is_still_named(self):
        """"Möhlin" stored as "Mo" + "¨" + "hlin", or with a soft hyphen in it,
        is another municipality all the same: reported, not given to Seon."""
        import unicodedata
        for neighbour in (unicodedata.normalize("NFD", "Möhlin"), "Möh\u00adlin"):
            pubs, problems = P.publications([record(
                "2026-03-05", "Gemeinde Seon", f"Gestaltungsplan an der Grenze zu {neighbour}")])
            self.assertEqual((pubs, len(problems)), ([], 1), ascii(neighbour))


def explicit(pub_nr, published_on, authority, title, municipality="", canton_wide=False, part="",
             verified=None, rubric="", url=None):
    """A record as the importer writes it: attributed by the operator, no text."""
    rec = {"pub_nr": pub_nr, "published_on": published_on, "authority": authority, "rubric": rubric,
           "title": title, "url": url or f"https://amtsblatt.ag.ch/ekab/{pub_nr}/pdf/"}
    if municipality:
        rec["municipality"] = municipality
    if canton_wide:
        rec["canton_wide"] = True
    if part:
        rec["part"] = part
    if verified is not None:
        rec["verified"] = verified
    return rec


def proof(stage, verified_on="2026-10-06", **dates):
    return dict({"stage": stage, "source": "https://amtsblatt.ag.ch/ekab/00.100.001/pdf/",
                 "verified_on": verified_on, "method": "Publikation gelesen"}, **dates)


class ExplicitRecordTest(unittest.TestCase):
    """A record the importer wrote names its Gemeinde, or says it is
    canton-wide: no text is read to attribute it, and one Gemeinde's row of a
    canton notice lends nothing to another's."""

    def test_a_municipalitys_own_notice_is_its_own(self):
        pubs, problems = P.publications([explicit("00.900.701", "2026-09-01", "Gemeinde Seon",
                                                  'Gestaltungsplan "Rain"; öffentliche Auflage',
                                                  municipality="Seon")])
        self.assertEqual(problems, [])
        self.assertEqual([(p.municipality, p.level) for p in pubs], [("Seon", "gemeinde")])

    def test_a_canton_notice_is_one_row_per_municipality_in_its_own_words(self):
        approval = dict(authority="Departement Bau, Verkehr und Umwelt", rubric="Kanton / Raumplanung",
                        title="Genehmigung von Sondernutzungsplänen")
        pubs, problems = P.publications([
            explicit("00.900.702", "2026-09-02", municipality="Reinach",
                     part='Gestaltungsplan "Friedstrasse"', verified=proof("genehmigt", approved_on="2026-08-28",
                                                                         appeal_until="2026-10-02"),
                     **approval),
            explicit("00.900.702", "2026-09-02", municipality="Menziken",
                     part='Gestaltungsplan "Friedstrasse", Genehmigung verweigert',
                     verified=proof("verweigert", decided_on="2026-08-28"), **approval)])
        self.assertEqual(problems, [])
        reinach = P.listing(pubs, "Reinach").publications
        menziken = P.listing(pubs, "Menziken").publications
        self.assertEqual([(p.level, P.publication_title(p), p.verified.stage) for p in reinach],
                         [("kanton", 'Gestaltungsplan "Friedstrasse"', "genehmigt")])
        self.assertEqual([p.verified.stage for p in menziken], ["verweigert"])
        self.assertEqual(P.listing(pubs, "Seon").publications, [])

    def test_a_canton_wide_change_reaches_every_municipality(self):
        pubs, problems = P.publications([explicit("00.900.703", "2026-09-15", "Departement Bau, Verkehr und Umwelt",
                                                  "Teilrevision Baugesetz (BauG); Anhörung", canton_wide=True,
                                                  rubric="Kanton / Anhörungs- und Mitwirkungsverfahren")])
        self.assertEqual(problems, [])
        self.assertEqual(len(P.listing(pubs, "Seon").canton_publications), 1)
        self.assertEqual(len(P.listing(pubs, "Möhlin").canton_publications), 1)

    def test_no_text_is_read_to_attribute_it(self):
        rec = dict(explicit("00.900.704", "2026-09-02", "Departement Bau, Verkehr und Umwelt",
                            "Genehmigung von Sondernutzungsplänen", municipality="Seon",
                            part='Gestaltungsplan "Rain"', rubric="Kanton / Raumplanung"),
                   text='Gemeinde Möhlin: Gestaltungsplan "Hof" | Gemeinde Reinach: Gestaltungsplan "Weid"')
        pubs, problems = P.publications([rec])
        self.assertEqual(([(p.municipality, P.publication_title(p)) for p in pubs], problems),
                         ([("Seon", 'Gestaltungsplan "Rain"')], []))

    def test_what_cannot_be_attributed_as_stated_is_a_problem(self):
        cases = (
            (explicit("00.900.705", "2026-09-01", "Gemeinde Seon", "Gestaltungsplan", municipality="Atlantis"),
             "nicht im Verzeichnis"),
            (explicit("00.900.706", "2026-09-01", "Gemeinde Seon", "Gestaltungsplan", municipality="Seon",
                      canton_wide=True), "kantonsweit"),
            (explicit("00.900.707", "2026-09-01", "Gemeinde Seon", "Gestaltungsplan", municipality="Reinach"),
             "gehört zu Seon"),
        )
        for rec, words in cases:
            pubs, problems = P.publications([rec])
            self.assertEqual((pubs, len(problems)), ([], 1), rec)
            self.assertIn(words, problems[0])

    def test_canton_wide_is_a_canton_office_in_a_canton_rubric(self):
        """A municipality's own notice flagged canton-wide would reach every
        parcel in Aargau — with its checked stage."""
        for rec in (explicit("00.900.709", "2026-09-01", "Gemeinde Seon", "Gestaltungsplan «Rain»; Genehmigung",
                             canton_wide=True, rubric="Kanton / Raumplanung"),
                    explicit("00.900.710", "2026-09-01", "Departement Bau, Verkehr und Umwelt",
                             "Gestaltungsplan «Rain»; Genehmigung", canton_wide=True,
                             rubric="Gemeinden / Bau- und Nutzungsordnung")):
            pubs, problems = P.publications([rec])
            self.assertEqual((pubs, len(problems)), ([], 1), rec)
            self.assertIn("kantonsweit", problems[0])

    def test_a_source_address_a_browser_would_not_take_as_it_stands_is_refused(self):
        for url in ('https://amtsblatt.ag.ch/ekab/00.1/"href="javascript:alert%281%29',
                    "https://amtsblatt.ag.ch/ekab/00 1/pdf/", "https://amtsblatt.ag.ch/ekab/<1>/pdf/"):
            pubs, problems = P.publications([explicit("00.900.711", "2026-09-01", "Gemeinde Seon", "Plan",
                                                      municipality="Seon", url=url)])
            self.assertEqual((pubs, len(problems)), ([], 1), url)

    def test_one_publication_for_two_municipalities_is_no_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "store.json")
            approval = dict(authority="Departement Bau, Verkehr und Umwelt", rubric="Kanton / Raumplanung",
                            title="Genehmigung von Sondernutzungsplänen")
            twice = explicit("00.900.708", "2026-09-02", municipality="Reinach", **approval)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"stand": "2026-10-06", "since": "2026-01-01", "records": [
                    twice, explicit("00.900.708", "2026-09-02", municipality="Menziken", **approval)]}, handle)
            P.reset_cache()
            self.addCleanup(P.reset_cache)
            self.assertEqual(P.load_store(path).duplicates, [])
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"stand": "2026-10-06", "since": "2026-01-01", "records": [twice, twice]}, handle)
            self.assertEqual(P.load_store(path).duplicates, ["00.900.708 (Reinach (AG))"])


class PartNumberTest(unittest.TestCase):
    """A canton notice can name one Gemeinde twice — "Gemeinde Seon: Aufhebung
    … | Gemeinde Seon: Gestaltungsplan «Giessi»". Each part is its own row,
    numbered, and its own check."""

    def test_two_parts_for_one_municipality_are_two_rows(self):
        notice = dict(authority="Abteilung Raumentwicklung", rubric="Kanton / Raumplanung",
                      title="Genehmigung von Sondernutzungsplänen")
        first = dict(explicit("00.066.473", "2025-01-17", municipality="Seon",
                              part='Aufhebung Überbauungspläne "Unterdorf-Giesserei-Breiten" und "Breiten"',
                              **notice), part_nr=1)
        second = dict(explicit("00.066.473", "2025-01-17", municipality="Seon",
                               part='Gestaltungsplan "Giessi"', **notice), part_nr=2)
        self.assertEqual([P.record_key(r) for r in (first, second)],
                         ["00.066.473 (Seon #1)", "00.066.473 (Seon #2)"])
        pubs, problems = P.publications([first, second])
        self.assertEqual(problems, [])
        self.assertEqual([P.publication_title(p) for p in pubs],
                         ['Aufhebung Überbauungspläne "Unterdorf-Giesserei-Breiten" und "Breiten"',
                          'Gestaltungsplan "Giessi"'])
        for bad in (0, -1, "zwei", 1.5):
            pubs, problems = P.publications([dict(first, part_nr=bad)])
            self.assertEqual(len(problems), 1, bad)
            self.assertIn("part_nr", problems[0])


class VerifiedStageTest(unittest.TestCase):
    """Philipp's three badges, each only while a checked source supports it on
    the day the card is drawn — never from a word in a title."""

    def badge(self, verified, day, **record):
        rec = explicit("00.900.720", "2026-09-01", "Gemeinde Seon", record.pop("title", "Gestaltungsplan «Rain»"),
                       municipality="Seon", verified=verified, **record)
        pubs, problems = P.publications([rec])
        self.assertEqual(problems, [])
        return P.stage_badge(pubs[0], date.fromisoformat(day))

    def test_a_display_shows_entwurf_in_auflage_only_while_it_runs(self):
        auflage = proof("auflage", verified_on="2026-09-02", auflage_from="2026-09-03", auflage_to="2026-10-02")
        self.assertEqual(self.badge(auflage, "2026-09-20")[0], "entwurf_auflage")
        self.assertEqual(self.badge(auflage, "2026-10-02")[0], "entwurf_auflage")
        status, note = self.badge(auflage, "2026-10-03")
        self.assertIsNone(status)
        self.assertIn("Öffentliche Auflage 03.09.2026–02.10.2026 beendet", note)
        self.assertIsNone(self.badge(auflage, "2026-09-02")[0])  # announced, not yet open

    def test_an_approval_is_pending_only_as_long_as_the_evidence_reaches(self):
        appeal = proof("genehmigt", approved_on="2026-09-25", appeal_until="2026-10-27")
        self.assertEqual(self.badge(appeal, "2026-10-06")[0], "genehmigt")
        status, note = self.badge(appeal, "2026-10-28")
        self.assertIsNone(status)
        self.assertIn("Genehmigt am 25.09.2026", note)
        self.assertIn("nicht geprüft", note)
        # Without a deadline or a date of legal force from the source, pending
        # is shown on no day — not even the day of the check: a 2019 approval
        # checked today would read as pending.
        bare = proof("genehmigt", verified_on="2026-10-01", approved_on="2019-10-14")
        status, note = self.badge(bare, "2026-10-01")
        self.assertIsNone(status)
        self.assertIn("Genehmigt am 14.10.2019 — Rechtskraft nicht belegt", note)
        binding = proof("genehmigt", approved_on="2026-09-25", appeal_until="2026-10-27",
                        rechtskraft_on="2026-10-05")
        status, note = self.badge(binding, "2026-10-06")
        self.assertIsNone(status)
        self.assertIn("rechtskräftig seit 05.10.2026", note)

    def test_a_date_of_legal_force_announced_is_not_told_as_reached(self):
        """Checked on 06.10. with legal force announced for 15.11.: pending until
        then, and afterwards announced — not "rechtskräftig seit", which nobody
        checked."""
        ahead = proof("genehmigt", approved_on="2026-09-25", rechtskraft_on="2026-11-15")
        self.assertEqual(self.badge(ahead, "2026-11-10")[0], "genehmigt")
        status, note = self.badge(ahead, "2026-11-20")
        self.assertIsNone(status)
        self.assertNotIn("rechtskräftig seit", note)
        self.assertIn("Rechtskraft auf 15.11.2026 angekündigt — nicht geprüft", note)

    def test_in_force_needs_a_date_that_has_passed_when_it_was_checked(self):
        self.assertEqual(self.badge(proof("in_kraft", effective_on="2026-07-01"), "2026-10-06")[0], "in_kraft")

    def test_a_refusal_is_said_and_earns_no_badge(self):
        status, note = self.badge(proof("verweigert", decided_on="2026-08-28"), "2026-10-06")
        self.assertIsNone(status)
        self.assertIn("Genehmigung verweigert am 28.08.2026", note)

    def test_no_check_no_badge(self):
        self.assertEqual(self.badge(None, "2026-10-06"), (None, ""))

    def test_a_check_dated_after_the_day_shown_is_no_evidence_for_it(self):
        status, note = self.badge(proof("in_kraft", verified_on="2026-10-06", effective_on="2026-07-01"),
                                  "2026-10-05")
        self.assertIsNone(status)
        self.assertIn("Prüfung datiert nach", note)

    def test_a_check_that_does_not_hold_together_is_a_problem_not_a_badge(self):
        cases = (
            (proof("auflage", auflage_from="2026-09-03"), "auflage_to"),
            (proof("auflage", auflage_from="2026-10-03", auflage_to="2026-09-03"), "auflage_from"),
            (proof("genehmigt"), "approved_on"),
            (proof("genehmigt", verified_on="2026-09-01", approved_on="2026-09-25"), "approved_on"),
            (proof("in_kraft", verified_on="2026-06-01", effective_on="2026-07-01"), "effective_on"),
            (proof("verweigert"), "decided_on"),
            (proof("beschlossen"), "stage"),
            (dict(proof("in_kraft", effective_on="2026-07-01"), source="javascript:alert(1)"), "source"),
            (dict(proof("in_kraft", effective_on="2026-07-01"), method=""), "method"),
            (dict(proof("in_kraft", effective_on="2026-07-01"), verified_on="gestern"), "verified_on"),
        )
        for verified, field_name in cases:
            rec = explicit("00.900.721", "2026-09-01", "Gemeinde Seon", "Gestaltungsplan «Rain»",
                           municipality="Seon", verified=verified)
            pubs, problems = P.publications([rec])
            self.assertEqual(len(problems), 1, verified)
            self.assertIn(field_name, problems[0], verified)
            self.assertEqual(problems[0].mentions, ())  # a data error, not an unattributed notice
            self.assertEqual([(p.municipality, p.verified) for p in pubs], [("Seon", None)])

    def test_a_check_needs_a_row_that_says_which_municipality_it_is_for(self):
        rec = dict(record("2026-09-02", "Abteilung Raumentwicklung", "Genehmigung von Sondernutzungsplänen",
                          'Gemeinde Seon: Gestaltungsplan "Rain" | Gemeinde Reinach: Gestaltungsplan "Hof"',
                          rubric="Kanton / Raumplanung"),
                   verified=proof("genehmigt", approved_on="2026-08-28", appeal_until="2026-10-02"))
        pubs, problems = P.publications([rec])
        self.assertEqual(len(problems), 1)
        self.assertIn("Gemeinde", problems[0])
        self.assertTrue(all(p.verified is None for p in pubs))


class StoreSafetyTest(unittest.TestCase):
    def test_a_record_the_parser_trips_on_leaves_the_page_up(self):
        """Fields only the inference path uses are still parsed as the store
        loads. Should that fail, the card and the check say the list cannot be
        read — with the inference path OFF, the Analyse page must not fail."""
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "store.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"stand": "2026-10-05", "since": "2020-01-01", "records": [
                    record("2026-09-01", "Gemeinde Seon", 'Gestaltungsplan "Rain"; öffentliche Auflage')]},
                    handle)
            P.reset_cache()
            self.addCleanup(P.reset_cache)
            with patch.object(P, "_period", side_effect=RuntimeError("boom")):
                result = P.state_for("Seon", [], path=path)
                ok, lines = P.check(path, today=TODAY)
        self.assertEqual((result.status, result.error),
                         ("error", "Verzeichnis nicht auswertbar (RuntimeError)"))
        self.assertEqual((ok, lines), (False, ["Verzeichnis nicht auswertbar (RuntimeError)"]))

    def test_a_source_the_url_parser_rejects_is_a_problem_not_a_crash(self):
        """"https://[invalid/" starts like a web link and is none. It is
        reported — the card counts it, the check fails — and nothing raises,
        with the inference path off as on."""
        good = record("2026-09-01", "Gemeinde Seon", 'Gestaltungsplan "Rain"; öffentliche Auflage',
                      pub_nr="00.900.261")
        bad = dict(record("2026-09-02", "Gemeinde Seon", 'Gestaltungsplan "Rain"; Beschluss',
                          pub_nr="00.900.262"), url="https://[invalid/")
        for url in ("https://[invalid/", "https://", "https:///ekab/1/pdf/"):
            pubs, problems = P.publications([dict(bad, url=url)])
            self.assertEqual((pubs, len(problems)), ([], 1), url)
            self.assertIn("kein Web-Link", problems[0], url)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "store.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"stand": "2026-10-05", "since": "2020-01-01", "records": [good, bad]}, handle)
            P.reset_cache()
            self.addCleanup(P.reset_cache)
            result = P.state_for("Seon", [], path=path)
            ok, lines = P.check(path, today=TODAY)
        self.assertEqual((result.status, len(result.publications), result.unattributed), ("ok", 1, 1))
        self.assertFalse(ok)
        self.assertTrue(any("kein Web-Link" in line for line in lines), lines)

    def test_text_is_read_in_one_form_whatever_the_source_stored(self):
        """An umlaut stored as two code points, a soft hyphen inside a name:
        the office is still Böttstein or Seengen, and the title prints as one
        letter per letter. A zero-width space still separates two words."""
        nfd = lambda text: unicodedata.normalize("NFD", text)  # noqa: E731
        for office, title, name in (
                (nfd("Gemeinde Böttstein"), nfd('Gestaltungsplan "Rüti"; Beschluss'), "Böttstein"),
                ("Gemeinde Seen\u00adgen", 'Gestaltungsplan "Rü\u00adti"; Beschluss', "Seengen")):
            pubs, problems = P.publications([record("2026-03-05", office, title)])
            self.assertEqual(([p.municipality for p in pubs], problems), ([name], []), ascii(office))
            self.assertEqual(pubs[0].title, 'Gestaltungsplan "Rüti"; Beschluss')
        pubs, problems = P.publications([canton("2026-03-05",
                                                "Gemeinde Seon: Gestaltungsplan «J» Seengen\u200bReinach")])
        self.assertEqual((pubs, len(problems)), ([], 1))

    def test_a_date_outside_any_plausible_range_is_a_problem(self):
        for day in ("0001-01-01", "9999-12-20", "1850-05-01"):
            pubs, problems = P.publications([record(day, "Gemeinde Seon", 'Gestaltungsplan "Rain"; Beschluss')])
            self.assertEqual((pubs, len(problems)), ([], 1), day)

    def test_a_date_in_the_text_outside_any_plausible_range_is_no_date(self):
        pubs, _ = P.publications([canton("2026-09-01", 'Gemeinde Seon: Gestaltungsplan "Rain"; '
                                                       'Genehmigung. Der Plan tritt am 1. Januar 9999 in Kraft.')])
        self.assertIsNone(pubs[0].effective_on)

    def test_an_evaluation_error_is_shown_as_an_unreadable_list(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "store.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"stand": "2026-10-05", "since": "2020-01-01", "records": []}, handle)
            saved = os.environ.get(P.ENV_INFERENCE)
            os.environ[P.ENV_INFERENCE] = "1"
            real = P.view
            P.view = lambda *args, **kwargs: (_ for _ in ()).throw(OverflowError("date value out of range"))
            try:
                P.reset_cache()
                result = P.state_for("Seon", [], path=path)
            finally:
                P.view = real
                os.environ.pop(P.ENV_INFERENCE, None)
                if saved is not None:
                    os.environ[P.ENV_INFERENCE] = saved
                P.reset_cache()
        self.assertEqual((result.status, result.error),
                         ("error", "Verzeichnis nicht auswertbar (OverflowError)"))

    def test_the_page_never_fails_on_a_store_it_cannot_evaluate(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "store.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"stand": "2026-10-05", "since": "2020-01-01", "records": [
                    record("2026-09-01", "Gemeinde Seon", "Erlass einer Planungszone «Oberdorf»",
                           "Die Planungszone gilt längstens bis 31. Dezember 9999.")]}, handle)
            saved = os.environ.get(P.ENV_INFERENCE)
            try:
                for flag in ("", "1"):
                    os.environ[P.ENV_INFERENCE] = flag
                    P.reset_cache()
                    self.assertIn(P.state_for("Seon", [], path=path).status, ("ok", "error"), flag)
            finally:
                os.environ.pop(P.ENV_INFERENCE, None)
                if saved is not None:
                    os.environ[P.ENV_INFERENCE] = saved
                P.reset_cache()

    def test_version_one_does_not_run_the_inference_path(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "store.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"stand": "2026-10-05", "since": "2020-01-01", "records": [
                    record("2026-09-01", "Gemeinde Seon", 'Gestaltungsplan "Rain"; Beschluss')]}, handle)
            saved = os.environ.pop(P.ENV_INFERENCE, None)
            try:
                P.reset_cache()
                result = P.state_for("Seon", [], path=path)
                self.assertEqual([p.title for p in result.publications], ['Gestaltungsplan "Rain"; Beschluss'])
                self.assertEqual(result.all(), [])
            finally:
                if saved is not None:
                    os.environ[P.ENV_INFERENCE] = saved
                P.reset_cache()


class ParcelFindingTest(unittest.TestCase):
    def view(self, recs, provisions, created="2026-08-18"):
        pubs, _ = P.publications(recs)
        return P.view(pubs, "Seon", today=TODAY,
                      parcel={"provisions": [{"title": t} for t in provisions], "created": created})

    def test_a_longer_plan_name_in_the_extract_is_another_plan(self):
        view = self.view([record("2025-01-10", "Gemeinde Seon", 'Gestaltungsplan "Zentrum"; Beschluss')],
                         ["Gestaltungsplan Zentrumszone", "Gestaltungsplan Zentrum Hirschthal"])
        self.assertEqual([r.title for r in view.decided], ["Gestaltungsplan «Zentrum»"])
        self.assertFalse(view.decided[0].on_parcel)

    def test_an_annotated_extract_title_still_names_the_plan(self):
        pubs, _ = P.publications([record("2025-01-10", "Gemeinde Seon",
                                         'Gestaltungsplan "Giessi"; Beschluss')])
        view = P.view(pubs, "Seon", today=TODAY, parcel={"created": "2026-08-18", "provisions": [
            {"title": "Gestaltungsplan Giessi, 1. Änderung", "lawstatus": "inForce"}]})
        self.assertTrue(view.unclear[0].on_parcel)

    def test_a_future_in_force_date_is_not_overridden_by_the_extract(self):
        view = self.view([record("2025-01-10", "Gemeinde Seon", 'Gestaltungsplan "Giessi"; Beschluss',
                                 "Der Plan tritt am 1. Januar 2027 in Kraft.")],
                         ["Gestaltungsplan Giessi"])
        self.assertEqual(view.decided[0].stage, "beschlossen")

    def test_a_pending_change_in_the_extract_is_not_in_force(self):
        pubs, _ = P.publications([record("2025-01-10", "Gemeinde Seon",
                                         'Gestaltungsplan "Giessi"; Beschluss')])
        view = P.view(pubs, "Seon", today=TODAY, parcel={"created": "2026-08-18", "provisions": [
            {"title": "Gestaltungsplan Giessi", "lawstatus": "changeWithPreEffect"}]})
        self.assertEqual(view.unclear[0].stage, "beschlossen")
        self.assertEqual(view.unclear[0].status_label, "Stand unbestätigt")
        self.assertTrue(view.unclear[0].on_parcel)

    def test_a_later_partial_revision_is_not_the_approval_of_the_regulation_in_force(self):
        edict = R.Edict(municipality="Seon", title="Bau- und Nutzungsordnung", abbreviation="BNO",
                        in_force=date(2026, 1, 1), syst_nr="4209", document="")
        pubs, _ = P.publications([canton(
            "2026-03-15", "Gemeinde Seon; Allgemeine Nutzungsplanung, Teilrevision Kern; Genehmigung",
            title="Genehmigung von Nutzungsplänen", authority="Regierungsrat")])
        view = P.view(pubs, "Seon", [edict], today=TODAY)
        self.assertEqual(view.approvals, {})
        self.assertEqual([r.title for r in view.approved], ["Allgemeine Nutzungsplanung, Teilrevision Kern"])


class CoverageTest(unittest.TestCase):
    """A store that says "nothing here" must also say since when it looks, and
    a municipality it does not cover is not tracked rather than quiet."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.path = os.path.join(self.tempdir.name, "publications.json")
        P.reset_cache()
        self.addCleanup(P.reset_cache)

    def write(self, **payload):
        with open(self.path, "w") as handle:
            json.dump(dict({"stand": "2026-10-05", "records": SEON}, **payload), handle)

    def test_a_municipality_outside_the_store_is_not_tracked(self):
        self.write(since="2026-10-01", municipalities=["Seon"])
        self.assertEqual(P.state_for("Möhlin", [], path=self.path, today=TODAY).status, "uncovered")
        seon = P.state_for("Seon", [], path=self.path, today=TODAY)
        self.assertEqual((seon.status, seon.since), ("ok", date(2026, 10, 1)))

    def test_coverage_that_is_no_list_is_unreadable_not_all(self):
        """Absent means every Gemeinde — what the importer writes for «alle».
        A null, a blank or a single name is no list: the card says the list
        cannot be read rather than covering Menziken with nothing."""
        for value in (None, "", "Seon", 5):
            self.write(since="2026-10-01", municipalities=value)
            P.reset_cache()
            view = P.state_for("Menziken", [], path=self.path, today=TODAY)
            self.assertEqual(view.status, "error", value)
            self.assertIn("municipalities", view.error, value)
            self.assertFalse(P.check(self.path, today=TODAY)[0], value)
        self.write(since="2026-10-01")
        P.reset_cache()
        self.assertEqual(P.state_for("Menziken", [], path=self.path, today=TODAY).status, "ok")

    def test_check_wants_to_know_since_when(self):
        self.write()
        ok, lines = P.check(self.path, today=TODAY)
        self.assertFalse(ok)
        self.assertIn("since", "\n".join(lines))

    def test_an_unattributable_mention_is_counted_where_it_belongs(self):
        self.write(since="2020-01-01", records=SEON + [canton(
            "2026-03-01", "Genehmigt wurde ein Plan im Gebiet der Gemeinden Seon und Seengen.")])
        self.assertEqual(P.state_for("Seon", [], path=self.path, today=TODAY).unattributed, 1)
        self.assertEqual(P.state_for("Möhlin", [], path=self.path, today=TODAY).unattributed, 0)


class StatusTest(unittest.TestCase):
    """Phil's labels (2026-10-05): "Entwurf in Auflage" amber, "Genehmigt,
    Rechtskraft ausstehend" yellow, "In Kraft getreten" grey — each only on
    the evidence for it, and an accurate label of its own where none of the
    three fits."""

    def status(self, recs, parcel=None):
        pubs, _ = P.publications(recs)
        return {r.title: r.status_label
                for r in P.view(pubs, "Seon", today=TODAY, parcel=parcel).all()}

    def test_auflage_is_the_draft_on_display(self):
        self.assertEqual(self.status([record("2026-09-01", "Gemeinde Seon",
                                             'Gestaltungsplan "Rain"; öffentliche Auflage',
                                             "Die Akten liegen vom 2. September 2026 bis 15. Oktober "
                                             "2026 auf.")]),
                         {"Gestaltungsplan «Rain»": "Entwurf in Auflage"})

    def test_mitwirkung_is_not_auflage(self):
        self.assertEqual(self.status([record("2026-09-01", "Gemeinde Seon",
                                             "Öffentliche Mitwirkung zum Gestaltungsplan «Rain»",
                                             "Mitwirkung vom 2. September 2026 bis zum 20. Oktober 2026.")]),
                         {"Gestaltungsplan «Rain»": "Entwurf in Mitwirkung"})

    def test_a_decision_is_not_an_approval(self):
        self.assertEqual(self.status([record("2026-09-01", "Gemeinde Seon",
                                             'Gestaltungsplan "Rain"; Beschluss')]),
                         {"Gestaltungsplan «Rain»": "Beschlossen"})

    def test_an_approval_without_evidence_is_neither_in_force_nor_pending(self):
        """No source shows the plan in force — and none shows its legal force
        still pending. "Rechtskraft ausstehend" would be a guess too."""
        self.assertEqual(self.status([canton("2026-08-01", 'Gemeinde Seon: Gestaltungsplan "Rain"')]),
                         {"Gestaltungsplan «Rain»": "Genehmigt, Inkrafttreten nicht belegt"})

    def test_an_approval_in_its_appeal_period_is_pending(self):
        """The notice states the appeal period; while it runs, the approval is
        not yet legally binding."""
        text = ('Gemeinde Seon: Gestaltungsplan "Rain" | Gegen diesen Genehmigungsentscheid kann '
                "innert 30 Tagen seit der Publikation Beschwerde erhoben werden.")
        self.assertEqual(self.status([canton("2026-09-25", text)]),
                         {"Gestaltungsplan «Rain»": "Genehmigt, Rechtskraft ausstehend"})
        self.assertEqual(self.status([canton("2026-06-01", text)]),
                         {"Gestaltungsplan «Rain»": "Genehmigt, Inkrafttreten nicht belegt"})

    def test_a_draft_is_on_display_only_within_its_stated_period(self):
        ended = record("2026-03-01", "Gemeinde Seon", 'Gestaltungsplan "Rain"; öffentliche Auflage',
                       "Die Akten liegen vom 2. März 2026 bis 2. April 2026 auf.")
        unstated = record("2026-09-30", "Gemeinde Seon", 'Gestaltungsplan "Hang"; öffentliche Auflage')
        self.assertEqual(self.status([ended, unstated]), {
            "Gestaltungsplan «Rain»": "Stand unbestätigt",
            "Gestaltungsplan «Hang»": "Stand unbestätigt",
        })

    def test_an_approval_whose_in_force_date_has_passed_is_in_force(self):
        self.assertEqual(self.status([canton(
            "2026-06-01", 'Gemeinde Seon: Gestaltungsplan "Rain"; Inkrafttreten am 1. Juli 2026')]),
            {"Gestaltungsplan «Rain»": "In Kraft getreten"})

    def test_the_cadastre_listing_is_no_evidence_for_this_revision(self):
        parcel = {"created": "2026-08-18", "provisions": [
            {"title": "Gestaltungsplan Rain", "number": "26.050", "lawstatus": "inForce"}]}
        self.assertEqual(self.status([canton("2026-03-01", 'Gemeinde Seon: Gestaltungsplan "Rain"')],
                                     parcel=parcel),
                         {"Gestaltungsplan «Rain»": "Genehmigt, Inkrafttreten nicht belegt"})
        self.assertEqual(self.status([canton("2026-03-01", 'Gemeinde Seon: Gestaltungsplan "Rain". '
                                                           'Plan Nr. 26.050.')], parcel=parcel),
                         {"Gestaltungsplan «Rain»": "Genehmigt, Inkrafttreten nicht belegt"})

    def test_a_canton_consultation_is_an_anhoerung(self):
        pubs, _ = P.publications([canton(
            "2026-08-01", "Anhörung zur Teilrevision des Baugesetzes vom 1. August 2026 bis "
            "31. Oktober 2026.", title="Teilrevision Baugesetz; Anhörung",
            authority="Departement Bau, Verkehr und Umwelt",
            rubric="Kanton / Anhörungs- und Mitwirkungsverfahren")])
        self.assertEqual([r.status_label for r in P.view(pubs, "Seon", today=TODAY).canton],
                         ["Entwurf in Anhörung"])

    def test_colours_follow_phils_choice(self):
        self.assertEqual(P.STATUS_TONES["entwurf_auflage"], "amber")
        self.assertEqual(P.STATUS_TONES["genehmigt"], "yellow")
        self.assertEqual(P.STATUS_TONES["in_kraft"], "grey")
        self.assertEqual(P.STATUS_TONES["genehmigt_unbelegt"], "open")
        self.assertEqual(P.STATUS_TONES["unbestaetigt"], "open")
        self.assertEqual(set(P.STATUS_TONES), set(P.STATUS_LABELS))


class EvidenceTest(unittest.TestCase):
    """Second review, 2026-10-05: each case is an input that produced a label
    no source supported."""

    def status(self, recs, municipality="Seon", parcel=None):
        pubs, _ = P.publications(recs)
        return {r.title: r.status_label
                for r in P.view(pubs, municipality, today=TODAY, parcel=parcel).all()}

    def test_a_municipal_approval_with_a_date_in_between_is_a_decision(self):
        for title in ("Gemeindeversammlung vom 12.06.2026: Genehmigung Teiländerung Dorf",
                      "Teiländerung Dorf; Genehmigung durch den Einwohnerrat vom 12. Juni 2026"):
            self.assertEqual(self.status([record("2026-06-20", "Gemeinde Seon", title)]),
                             {P.publications([record("2026-06-20", "Gemeinde Seon", title)])[0][0]
                              .subject: "Beschlossen"}, title)

    def test_a_municipal_notice_saying_genehmigung_without_an_approver_is_a_decision(self):
        """A municipality does not approve its own plans; its notice reporting
        a "Genehmigung" without naming the canton reports its own decision."""
        self.assertEqual(self.status([record("2026-06-20", "Gemeinde Seon",
                                             "Teiländerung Dorf; Genehmigung")]),
                         {"Teiländerung Dorf": "Beschlossen"})

    def test_a_shared_date_in_a_notice_for_several_municipalities_is_not_lent(self):
        pubs, _ = P.publications([canton(
            "2026-09-01", 'Genehmigt wurden: | Gemeinde Seon: Gestaltungsplan "Rain" | '
            'Gemeinde Reinach: Gestaltungsplan "Hof" | Die Pläne treten am 1. Januar 2027 in Kraft.')])
        self.assertEqual([p.effective_on for p in pubs], [None, None])

    def test_a_date_beside_the_shared_appeal_clause_is_not_lent(self):
        pubs, _ = P.publications([canton(
            "2026-09-01", 'Genehmigt wurden: | Gemeinde Seon: Gestaltungsplan "Rain" | '
            'Gemeinde Reinach: Gestaltungsplan "Hof" | Die Pläne treten am 1. Januar 2027 in '
            'Kraft. Gegen diesen Entscheid kann innert 30 Tagen Beschwerde erhoben werden.')])
        self.assertEqual([(p.effective_on, p.appeal_days) for p in pubs],
                         [(None, 30), (None, 30)])

    def test_a_municipality_publishing_the_cantons_approval_is_an_approval(self):
        self.assertEqual(self.status([record(
            "2026-06-20", "Gemeinde Seon", 'Gestaltungsplan "Rain"; Genehmigung durch den Regierungsrat')]),
            {"Gestaltungsplan «Rain»": "Genehmigt, Inkrafttreten nicht belegt"})

    def test_a_lifted_planungszone_is_not_in_force(self):
        self.assertEqual(self.status([record("2026-05-01", "Gemeinde Seon",
                                             "Aufhebung der Planungszone «Oberdorf»")]),
                         {"Aufhebung der Planungszone «Oberdorf»": "Planungszone aufgehoben"})

    def test_a_planungszone_past_its_legal_term_has_ended(self):
        """§ 29 Abs. 2 BauG: a planning zone lasts at most five years — the
        law is the evidence that it ended."""
        self.assertEqual(self.status([record("2016-05-01", "Gemeinde Seon",
                                             "Erlass einer Planungszone «Oberdorf»")]),
                         {"Planungszone «Oberdorf»": "Planungszone beendet"})

    def zone(self, recs, edicts=(), parcel=None):
        pubs, _ = P.publications(recs)
        view = P.view(pubs, "Seon", edicts, today=TODAY, parcel=parcel)
        return next(r for r in view.all() if r.step == "planungszone")

    ZONE = ("2026-03-01", "Gemeinde Seon", "Erlass einer Planungszone «Oberdorf»")
    SECURES_PLAN = "Die Planungszone dient der Sicherung des Gestaltungsplans «Oberdorf»."
    SECURES_NUTZUNGSPLANUNG = ("Die Planungszone dient der Sicherung der Gesamtrevision der "
                               "Nutzungsplanung.")

    def zone_record(self, text=""):
        return record(*self.ZONE, text)

    def bno(self, in_force, municipality="Seon"):
        return R.Edict(municipality=municipality, title="Bau- und Nutzungsordnung",
                       abbreviation="BNO", in_force=in_force, syst_nr="4210", document="")

    def oberdorf_approval(self, published_on="2026-07-01", tail=" Der Plan tritt am 1. August 2026 "
                                                                 "in Kraft."):
        return canton(published_on, 'Gemeinde Seon: Gestaltungsplan "Oberdorf"; Genehmigung.' + tail)

    # Another plan of the municipality says nothing about the zone.
    def test_an_unrelated_edition_does_not_touch_a_zone(self):
        zone = self.zone([self.zone_record()], [self.bno(date(2026, 9, 1))])
        self.assertEqual(zone.status_label, "Planungszone in Kraft")
        self.assertIsNone(zone.zone_end)

    def test_an_unrelated_plan_in_force_does_not_touch_a_zone(self):
        zone = self.zone([self.zone_record(), canton(
            "2026-07-01", 'Gemeinde Seon: Gestaltungsplan "Rain"; Genehmigung. Der Plan tritt am '
                          '1. August 2026 in Kraft.')])
        self.assertEqual(zone.status_label, "Planungszone in Kraft")

    def test_without_oereblex_an_unrelated_zone_stays_in_force(self):
        pubs, _ = P.publications([self.zone_record()])
        self.assertEqual(P.view(pubs, "Seon", None, today=TODAY).all()[0].status_label,
                         "Planungszone in Kraft")

    # The plan the zone secures, by what a notice says: evidence of the end.
    def test_the_plan_the_zone_secures_in_force_ends_it(self):
        zone = self.zone([self.zone_record(self.SECURES_PLAN), self.oberdorf_approval()])
        self.assertEqual(zone.status_label, "Planungszone beendet")
        self.assertEqual(zone.zone_end[:2], ("in_kraft", date(2026, 8, 1)))
        self.assertEqual(zone.zone_end[3], "explicit")

    def test_a_plan_whose_notice_names_the_zone_ends_it_once_in_force(self):
        zone = self.zone([self.zone_record(), self.oberdorf_approval(
            tail=" Mit dem Inkrafttreten fällt die Planungszone «Oberdorf» dahin. Der Plan tritt "
                 "am 1. August 2026 in Kraft.")])
        self.assertEqual(zone.status_label, "Planungszone beendet")

    def test_a_secured_plan_in_force_before_the_zone_does_not_end_it(self):
        zone = self.zone([self.zone_record(self.SECURES_PLAN), self.oberdorf_approval(
            "2026-01-10", " Der Plan tritt am 1. Februar 2026 in Kraft.")])
        self.assertEqual(zone.status_label, "Planungszone in Kraft")

    def test_an_unrelated_change_of_the_nutzungsplanung_does_not_end_a_zone(self):
        """The zone secures the Gesamtrevision; a BNO change about the
        Mehrwertabgabe is in force — also as the edition OEREBlex lists."""
        change = canton("2026-09-10", "Gemeinde Seon; Änderung der BNO (Mehrwertabgabe); Genehmigung. "
                                      "Die Änderung tritt am 1. August 2026 in Kraft.",
                        title="Genehmigung von Nutzungsplänen", authority="Regierungsrat")
        for edicts in ((), [self.bno(date(2026, 9, 1))]):
            zone = self.zone([self.zone_record(self.SECURES_NUTZUNGSPLANUNG), change], edicts)
            self.assertEqual(zone.status_label, "Planungszone in Kraft", edicts)

    def test_a_passing_mention_of_the_nutzungsplanung_is_not_what_the_zone_secures(self):
        zone = self.zone([self.zone_record(
            "Die Planungszone dient der Sicherung des Gestaltungsplans «Oberdorf», der die "
            "Nutzungsplanung ergänzt.")], [self.bno(date(2026, 9, 1))])
        self.assertEqual(zone.status_label, "Planungszone in Kraft")

    def test_what_follows_the_clause_is_not_what_the_zone_secures(self):
        zone = self.zone([self.zone_record(
            "Die Planungszone dient der Sicherung der Planung, welche die Nutzungsplanung später "
            "ergänzen soll.")], [self.bno(date(2026, 9, 1))])
        self.assertEqual(zone.status_label, "Planungszone in Kraft")

    def test_the_revision_of_the_nutzungsplanung_is_its_gesamtrevision(self):
        zone = self.zone([self.zone_record("Die Planungszone dient der Sicherung der Revision der "
                                           "Nutzungsplanung."),
                          canton("2026-07-01", "Gemeinde Seon; Nutzungsplanung, Gesamtrevision; Genehmigung. "
                                               "Die Nutzungsplanung tritt am 1. August 2026 in Kraft.",
                                 title="Genehmigung von Nutzungsplänen", authority="Regierungsrat")])
        self.assertEqual(zone.status_label, "Planungszone beendet")

    def test_the_secured_plan_is_read_up_to_the_verb(self):
        """"…der Gesamtrevision der Nutzungsplanung erlässt der Gemeinderat
        eine Planungszone für das Gebiet «Oberdorf»": «Oberdorf» is the zone's
        area, not the name of the Gesamtrevision."""
        zone = self.zone([self.zone_record(
            "Zur Sicherung der Gesamtrevision der Nutzungsplanung erlässt der Gemeinderat eine "
            "Planungszone für das Gebiet «Oberdorf»."),
            canton("2026-07-01", "Gemeinde Seon; Nutzungsplanung, Gesamtrevision; Genehmigung. Die "
                                 "Nutzungsplanung tritt am 1. August 2026 in Kraft.",
                   title="Genehmigung von Nutzungsplänen", authority="Regierungsrat")])
        self.assertEqual(zone.status_label, "Planungszone beendet")

    def test_a_plan_named_for_the_zones_area_is_not_the_secured_one(self):
        zone = self.zone([self.zone_record(
            "Zur Sicherung der laufenden Planung erlässt der Gemeinderat die Planungszone «Oberdorf» "
            "für das Gebiet südlich des Gestaltungsplans «Rain»."),
            canton("2026-07-01", 'Gemeinde Seon: Gestaltungsplan "Rain"; Genehmigung. Der Plan tritt '
                                 'am 1. August 2026 in Kraft.')])
        self.assertEqual(zone.status_label, "Planungszone in Kraft")

    def test_a_plan_ending_the_unnamed_zone_ends_the_only_zone(self):
        zone = self.zone([self.zone_record(), canton(
            "2026-07-01", 'Gemeinde Seon: Gestaltungsplan "Oberdorf West"; Genehmigung. Mit dem '
                          'Inkrafttreten des Gestaltungsplans «Oberdorf West» fällt die Planungszone '
                          'dahin. Der Plan tritt am 1. August 2026 in Kraft.')])
        self.assertEqual(zone.status_label, "Planungszone beendet")

    def test_an_unnamed_plan_of_the_secured_kind_leaves_the_zone_open(self):
        """The zone secures "a Teilrevision"; one is approved and in force.
        It may be that one — open, not ended and not in force."""
        zone = self.zone([self.zone_record("Die Planungszone dient der Sicherung einer Teilrevision der "
                                           "Nutzungsplanung."),
                          canton("2026-07-01", "Gemeinde Seon; Nutzungsplanung, Teilrevision Bahnhof; "
                                               "Genehmigung. Die Teilrevision tritt am 1. August 2026 in Kraft.",
                                 title="Genehmigung von Nutzungsplänen", authority="Regierungsrat")])
        self.assertEqual(zone.status_label, "Stand unbestätigt")
        merged = canton("2026-09-10", "Der Regierungsrat hat am 1. September 2026 genehmigt: | Gemeinde "
                                      "Seon; Nutzungsplanung, Teilrevision Bahnhof; Genehmigung",
                        title="Genehmigung von Nutzungsplänen", authority="Regierungsrat")
        zone = self.zone([self.zone_record("Die Planungszone dient der Sicherung einer Teilrevision der "
                                           "Nutzungsplanung."), merged], [self.bno(date(2026, 9, 1))])
        self.assertEqual(zone.status_label, "Stand unbestätigt")
        self.assertEqual(zone.zone_end[3], "kind")

    def test_the_zone_named_beside_its_plan_does_not_hide_the_plan(self):
        zone = self.zone([self.zone_record(
            "Zur Sicherung des Gestaltungsplans «Oberdorf» erlässt der Gemeinderat die Planungszone "
            "«Oberdorf»."), self.oberdorf_approval()])
        self.assertEqual(zone.status_label, "Planungszone beendet")

    def test_the_zone_named_inside_the_phrase_does_not_hide_the_plan(self):
        zone = self.zone([self.zone_record(
            "Die Planungszone dient der Sicherung des Gestaltungsplans «Oberdorf» mit der Planungszone "
            "«Oberdorf»."), self.oberdorf_approval()])
        self.assertEqual(zone.status_label, "Planungszone beendet")

    def test_the_zones_own_name_is_not_the_plan_it_secures(self):
        zone = self.zone([self.zone_record(
            "Zur Sicherung der laufenden Planung erlässt der Gemeinderat die Planungszone «Oberdorf»."),
            canton("2026-07-01", 'Gemeinde Seon: Erschliessungsplan "Oberdorf"; Genehmigung. Der Plan '
                                 'tritt am 1. August 2026 in Kraft.')])
        self.assertEqual(zone.status_label, "Stand unbestätigt")
        self.assertEqual(zone.zone_end[3], "name")

    def test_a_plan_of_the_secured_kind_but_another_name_does_not_end_it(self):
        zone = self.zone([self.zone_record(self.SECURES_PLAN), canton(
            "2026-07-01", 'Gemeinde Seon: Gestaltungsplan "Rain"; Genehmigung. Der Plan tritt am '
                          '1. August 2026 in Kraft.')])
        self.assertEqual(zone.status_label, "Planungszone in Kraft")

    def test_a_plan_of_another_kind_but_the_same_name_is_not_the_secured_one(self):
        zone = self.zone([
            record("2026-03-01", "Gemeinde Seon", "Erlass einer Planungszone «Zentrum»",
                   "Die Planungszone dient der Sicherung der Teilrevision Nutzungsplanung «Zentrum»."),
            canton("2026-07-01", 'Gemeinde Seon: Erschliessungsplan "Zentrum"; Genehmigung. Der Plan '
                                 'tritt am 1. August 2026 in Kraft.')])
        self.assertEqual(zone.status_label, "Stand unbestätigt")
        self.assertEqual(zone.zone_end[3], "name")

    def test_a_plan_saying_the_zone_stays_does_not_end_it(self):
        for remark in (" Die Planungszone «Oberdorf» bleibt von diesem Entscheid unberührt.",
                       " Die Planungszone bleibt bestehen.",
                       " Die Planungszone «Oberdorf» wird im Plan dargestellt.",
                       " Die Planungszone «Oberdorf» wird nicht aufgehoben.",
                       " Es besteht kein Anlass, die Planungszone «Oberdorf» aufzuheben."):
            zone = self.zone([self.zone_record(), canton(
                "2026-07-01", 'Gemeinde Seon: Gestaltungsplan "Rain"; Genehmigung. Der Plan tritt am '
                              '1. August 2026 in Kraft.' + remark)])
            self.assertEqual(zone.status_label, "Planungszone in Kraft", remark)

    def test_a_plan_the_zone_is_to_secure_is_read_before_the_verb(self):
        zone = self.zone([self.zone_record(
            "Die Planungszone wird erlassen, um den Gestaltungsplan «Oberdorf West» zu sichern."),
            canton("2026-07-01", 'Gemeinde Seon: Gestaltungsplan "Oberdorf West"; Genehmigung. Der '
                                 'Plan tritt am 1. August 2026 in Kraft.')])
        self.assertEqual(zone.status_label, "Planungszone beendet")

    def test_a_zone_notice_saying_when_it_will_be_lifted_is_no_lifting(self):
        zone = self.zone([self.zone_record("Die Planungszone wird aufgehoben, sobald die revidierte "
                                           "Nutzungsplanung in Kraft tritt.")])
        self.assertEqual(zone.status_label, "Planungszone in Kraft")

    def test_a_partial_lifting_does_not_lift_the_whole_zone(self):
        for title in ("Teilaufhebung der Planungszone «Oberdorf»",
                      "Teilweise Aufhebung der Planungszone «Oberdorf»",
                      "Planungszone «Oberdorf»; Aufhebung für das Teilgebiet Nord"):
            zone = self.zone([self.zone_record(), record("2026-08-01", "Gemeinde Seon", title)])
            self.assertEqual(zone.status_label, "Stand unbestätigt", title)
            self.assertEqual(zone.zone_end[:2], ("teilaufhebung", date(2026, 8, 1)), title)

    def test_a_deadline_in_another_sentence_is_not_the_zones_term(self):
        for text in ("Die Planungszone gilt ab sofort. Die Pläne können bis 30. Oktober 2026 "
                     "eingesehen werden.",
                     "Die Planungszone gilt ab sofort. Allfällige Beschwerden sind bis 30. Oktober 2026 "
                     "einzureichen."):
            zone = self.zone([record("2026-09-01", "Gemeinde Seon", "Erlass einer Planungszone «Oberdorf»",
                                     text)])
            self.assertIsNone(zone.term_end, text)

    def test_a_term_is_read_within_its_sentence_and_not_from_a_deadline(self):
        for text in ("Die Planungszone gilt ab sofort. Die Unterlagen sind bis 30. Oktober 2026 auf "
                     "der Gemeindekanzlei erhältlich.",
                     "Die Planungszone gilt; Beschwerden sind bis 30. Oktober 2026 einzureichen.",
                     "Die Planungszone gilt ab 21. September 2026. Die Unterlagen sind bis "
                     "20. Oktober 2026 auf der Gemeindekanzlei erhältlich.",
                     "Die Planungszone gilt ab sofort; Einsichtnahme bis 30. Oktober 2026."):
            zone = self.zone([record("2026-09-01", "Gemeinde Seon", "Erlass einer Planungszone «Oberdorf»",
                                     text)])
            self.assertIsNone(zone.term_end, text)

    # Uncertainty, said as such.
    def test_a_plan_of_the_zones_name_in_force_leaves_the_zone_unconfirmed(self):
        """Same name, no notice linking the two: the zone may secure it."""
        zone = self.zone([self.zone_record(), self.oberdorf_approval()])
        self.assertEqual(zone.status_label, "Stand unbestätigt")
        self.assertEqual(zone.zone_end[0], "in_kraft")
        self.assertEqual(zone.zone_end[3], "name")

    def test_a_secured_plan_approved_without_in_force_evidence_leaves_the_zone_unconfirmed(self):
        zone = self.zone([self.zone_record(self.SECURES_PLAN), self.oberdorf_approval(tail="")])
        self.assertEqual(zone.status_label, "Stand unbestätigt")
        self.assertEqual(zone.zone_end[:2], ("genehmigung", date(2026, 7, 1)))

    def test_a_secured_plan_still_pending_keeps_the_zone_in_force(self):
        for later in (self.oberdorf_approval(tail=" Der Plan tritt am 1. Januar 2027 in Kraft."),
                      record("2026-06-01", "Gemeinde Seon", 'Gestaltungsplan "Oberdorf"; Beschluss')):
            zone = self.zone([self.zone_record(self.SECURES_PLAN), later])
            self.assertEqual(zone.status_label, "Planungszone in Kraft", later["title"])

    def test_an_unconfirmed_listing_of_the_secured_plan_leaves_the_zone_unconfirmed(self):
        parcel = {"created": "2026-09-20", "provisions": [
            {"title": "Gestaltungsplan Oberdorf", "number": "26.100", "lawstatus": "inForce"}]}
        zone = self.zone([self.zone_record(self.SECURES_PLAN), record(
            "2026-05-01", "Gemeinde Seon", 'Gestaltungsplan "Oberdorf"; Beschluss')], parcel=parcel)
        self.assertEqual(zone.status_label, "Stand unbestätigt")
        self.assertEqual(zone.zone_end[:2], ("oereb_unbestaetigt", date(2026, 9, 20)))

    # A zone securing the municipality's Nutzungsplanung and OEREBlex.
    def test_a_new_edition_leaves_a_zone_securing_the_nutzungsplanung_unconfirmed(self):
        zone = self.zone([self.zone_record(self.SECURES_NUTZUNGSPLANUNG)],
                         [self.bno(date(2026, 9, 1))])
        self.assertEqual(zone.status_label, "Stand unbestätigt")
        self.assertEqual(zone.zone_end[0], "ausgabe")
        self.assertEqual(zone.zone_end[3], "general")

    def test_the_approved_edition_of_the_secured_nutzungsplanung_ends_the_zone(self):
        zone = self.zone([self.zone_record(self.SECURES_NUTZUNGSPLANUNG), canton(
            "2026-09-10", "Gemeinde Seon; Nutzungsplanung, Gesamtrevision; Genehmigung",
            title="Genehmigung von Nutzungsplänen", authority="Regierungsrat")],
            [self.bno(date(2026, 9, 1))])
        self.assertEqual(zone.status_label, "Planungszone beendet")
        self.assertEqual(zone.zone_end[:2], ("in_kraft", date(2026, 9, 1)))

    def test_without_oereblex_a_zone_securing_the_nutzungsplanung_is_unconfirmed(self):
        pubs, _ = P.publications([self.zone_record(self.SECURES_NUTZUNGSPLANUNG)])
        zone = P.view(pubs, "Seon", None, today=TODAY).all()[0]
        self.assertEqual(zone.status_label, "Stand unbestätigt")
        self.assertEqual(zone.zone_end[0], "offen")

    def test_editions_that_cannot_be_the_secured_one_do_not_touch_the_zone(self):
        for edition in (self.bno(date(2099, 1, 1)), self.bno(date(2020, 1, 1)),
                        self.bno(date(2026, 9, 1), "Möhlin")):
            zone = self.zone([self.zone_record(self.SECURES_NUTZUNGSPLANUNG)], [edition])
            self.assertEqual(zone.status_label, "Planungszone in Kraft", edition)

    # Its term.
    def test_a_zone_with_its_display_date_ends_five_years_after_it(self):
        zone = self.zone([record("2021-09-20", "Gemeinde Seon", "Erlass einer Planungszone «Oberdorf»",
                                 "Die Planungszone liegt vom 21. September 2021 bis 20. Oktober 2021 "
                                 "öffentlich auf.")])
        self.assertEqual(zone.status_label, "Planungszone beendet")
        self.assertEqual(zone.zone_end[:2], ("frist", date(2026, 9, 21)))

    def test_a_zone_at_its_term_without_a_display_date_is_unconfirmed(self):
        """Published on 20.09.2021, display start not stated: its five years
        end on a day no source gives."""
        zone = self.zone([record("2021-09-20", "Gemeinde Seon", "Erlass einer Planungszone «Oberdorf»")])
        self.assertEqual(zone.status_label, "Stand unbestätigt")
        self.assertEqual(zone.zone_end[:2], ("frist_offen", date(2026, 9, 20)))

    def test_the_zone_runs_from_its_first_notice(self):
        """Five years at most from when it took effect — a later change of the
        zone does not start them again."""
        zone = self.zone([
            record("2020-01-15", "Gemeinde Seon", "Erlass einer Planungszone «Oberdorf»"),
            record("2024-01-15", "Gemeinde Seon", "Änderung der Planungszone «Oberdorf»"),
        ])
        self.assertEqual(zone.status_label, "Planungszone beendet")

    def test_a_zone_enacted_again_after_its_lifting_is_a_new_zone(self):
        pubs, _ = P.publications([
            record("2015-03-01", "Gemeinde Seon", "Erlass einer Planungszone «Oberdorf»"),
            record("2018-03-01", "Gemeinde Seon", "Aufhebung der Planungszone «Oberdorf»"),
            record("2024-03-01", "Gemeinde Seon", "Erlass einer Planungszone «Oberdorf»"),
        ])
        view = P.view(pubs, "Seon", [self.bno(date(2019, 7, 1))], today=TODAY)
        self.assertEqual(sorted((r.published_on.year, r.status_label) for r in view.all()),
                         [(2018, "Planungszone aufgehoben"), (2024, "Planungszone in Kraft")])

    def test_a_term_the_notice_states_ends_the_zone(self):
        for text in ("Die Planungszone gilt längstens bis 31. Dezember 2024.",
                     "Die Planungszone gilt vom 10. Januar 2023 bis 31. Dezember 2024."):
            zone = self.zone([record("2023-01-10", "Gemeinde Seon",
                                     "Erlass einer Planungszone «Oberdorf»", text)])
            self.assertEqual(zone.status_label, "Planungszone beendet", text)
            self.assertEqual(zone.term_end, date(2024, 12, 31), text)
            self.assertIsNone(zone.latest.period_end, text)

    def test_a_planungszone_not_yet_on_display_is_not_in_force(self):
        """It takes effect with the public display (§ 29 Abs. 2 BauG)."""
        zone = self.zone([record("2026-10-01", "Gemeinde Seon",
                                 "Erlass einer Planungszone «Oberdorf»",
                                 "Die Planungszone liegt vom 2. November 2026 bis "
                                 "1. Dezember 2026 öffentlich auf.")])
        self.assertEqual(zone.status_label, "Stand unbestätigt")
        self.assertEqual(zone.zone_end[:2], ("ab", date(2026, 11, 2)))

    def test_an_ended_zone_is_not_under_way(self):
        pubs, _ = P.publications([record("2016-05-01", "Gemeinde Seon",
                                         "Erlass einer Planungszone «Oberdorf»")])
        view = P.view(pubs, "Seon", today=TODAY)
        self.assertEqual([r.title for r in view.ended], ["Planungszone «Oberdorf»"])
        self.assertEqual(view.pending(), [])

    def test_a_planungszone_lifted_later_is_one_row_lifted(self):
        """The lifting belongs to the zone: one row, and not "in Kraft"."""
        self.assertEqual(self.status([
            record("2026-03-01", "Gemeinde Seon", "Erlass einer Planungszone «Oberdorf»"),
            record("2026-08-01", "Gemeinde Seon", "Aufhebung der Planungszone «Oberdorf»"),
        ]), {"Planungszone «Oberdorf»": "Planungszone aufgehoben"})

    def test_another_steps_period_is_not_this_ones(self):
        """The text recalls the Mitwirkung's dates; the Auflage states none."""
        pubs, _ = P.publications([record(
            "2026-09-20", "Gemeinde Seon", 'Gestaltungsplan "Rain"; öffentliche Auflage',
            "Die Mitwirkung fand vom 1. März 2026 bis 30. April 2026 statt. Die Akten liegen "
            "ab sofort während 30 Tagen auf.")])
        self.assertIsNone(pubs[0].period_end)

    def test_a_refusal_inside_a_shared_approval_is_not_an_approval(self):
        for part in ('Gestaltungsplan "Rain"; Genehmigung verweigert',
                     'Gestaltungsplan "Rain", Nichtgenehmigung'):
            pubs, _ = P.publications([canton("2026-08-01", f"Gemeinde Seon: {part}")])
            labels = [r.status_label for r in P.view(pubs, "Seon", today=TODAY).all()]
            self.assertEqual(labels, ["Stand unbestätigt"], part)

    def test_a_teilrevision_of_a_plan_is_its_own_revision(self):
        parcel = dict(GIESSI_PARCEL)
        view = P.view(P.publications(SEON + [record(
            "2026-06-01", "Gemeinde Seon", 'Teilrevision Gestaltungsplan "Giessi"; Beschluss')])[0],
            "Seon", [SEON_BNO], today=TODAY, parcel=parcel)
        titles_ = {r.title: r.status_label for r in view.all()}
        self.assertEqual(titles_["Teilrevision Gestaltungsplan «Giessi»"], "Beschlossen")
        self.assertEqual(titles_["Gestaltungsplan «Giessi»"], "Stand unbestätigt")

    def test_a_citation_or_an_office_name_does_not_make_a_notice_canton_wide(self):
        for rec in (
            canton("2026-09-01", "Anhörung gestützt auf § 127 BauG, vom 1. September 2026 bis "
                   "1. Dezember 2026.", title="Gewässerraum Bünz; Anhörung",
                   authority="Departement Bau, Verkehr und Umwelt",
                   rubric="Kanton / Anhörungs- und Mitwirkungsverfahren"),
            canton("2026-09-01", "Die Unterlagen liegen bei der Abteilung Raumentwicklung auf.",
                   title="Kantonaler Nutzungsplan Wildtierkorridor; öffentliche Auflage",
                   authority="Departement Bau, Verkehr und Umwelt", rubric="Kanton / Raumplanung"),
            canton("2026-09-01", "Anhörung zur Teilrevision des Baugesetzes.",
                   title="Teilrevision Baugesetz; Anhörung", authority="Abteilung Bau",
                   rubric=""),
        ):
            pubs, _ = P.publications([rec])
            self.assertEqual(P.view(pubs, "Möhlin", today=TODAY).canton, [], rec["title"])

    def test_an_address_in_a_canton_wide_notice_does_not_stop_it(self):
        pubs, problems = P.publications([canton(
            "2026-09-15", "Anhörung zur Teilrevision des Baugesetzes vom 15. September 2026 bis "
            "15. Dezember 2026. Departement BVU, Entfelderstrasse 22, 5001 Aarau.",
            title="Teilrevision Baugesetz (BauG); Anhörung",
            authority="Departement Bau, Verkehr und Umwelt",
            rubric="Kanton / Anhörungs- und Mitwirkungsverfahren")])
        self.assertEqual(problems, [])
        for name in ("Aarau", "Seon"):
            self.assertEqual([r.title for r in P.view(pubs, name, today=TODAY).canton],
                             ["Teilrevision Baugesetz (BauG)"], name)

    def test_shared_text_naming_another_plan_does_not_lend_its_date(self):
        pubs, _ = P.publications([canton(
            "2026-09-01", 'Genehmigt wurden: | Gemeinde Seon: Gestaltungsplan "Rain" | '
            'Gemeinde Reinach: Gestaltungsplan "Hof" | Der Gestaltungsplan "Hof" tritt am '
            "1. Januar 2027 in Kraft.")])
        rain = next(r for r in P.view(pubs, "Seon", today=TODAY).all())
        self.assertIsNone(rain.effective_on)
        self.assertEqual(rain.status_label, "Genehmigt, Inkrafttreten nicht belegt")

    def test_a_date_that_belongs_to_something_else_is_not_the_in_force_date(self):
        for text in ('Gemeinde Seon: Aufhebung Gestaltungsplan "Zentrum" (in Kraft seit 1. Januar 2015)',
                     "Gemeinde Seon; Allgemeine Nutzungsplanung, Teilrevision Kern; Genehmigung. "
                     "Im Übrigen bleibt die BNO vom 12. Juni 2015 in Kraft."):
            pubs, _ = P.publications([canton("2026-09-01", text)])
            self.assertIsNone(pubs[0].effective_on, text)

    def test_more_ways_to_write_an_in_force_date(self):
        for text in ("Die Änderung wird per 1. Jan. 2027 in Kraft gesetzt.",
                     "Die Änderung wird per 01.01.27 in Kraft gesetzt."):
            pubs, _ = P.publications([record("2026-09-01", "Gemeinde Seon",
                                             "Teiländerung Zentrum; Inkraftsetzung", text)])
            self.assertEqual(pubs[0].effective_on, date(2027, 1, 1), text)

    def test_an_inkraftsetzung_without_a_readable_date_is_not_in_force(self):
        self.assertEqual(self.status([record(
            "2026-09-01", "Gemeinde Seon", "Teiländerung Zentrum; Inkraftsetzung",
            "Die Änderung wird auf Beginn des Jahres 2027 in Kraft gesetzt.")]),
            {"Teiländerung Zentrum": "Stand unbestätigt"})

    def test_a_mitwirkung_display_is_not_an_auflage(self):
        period = " Die Unterlagen liegen vom 1. Oktober 2026 bis 30. Oktober 2026 auf."
        for title in ("Mitwirkungsauflage Gestaltungsplan «Rain»",
                      "Gestaltungsplan «Rain»; Entwurf zur Mitwirkung aufgelegt"):
            self.assertEqual(list(self.status([record("2026-09-30", "Gemeinde Seon", title,
                                                      period)]).values()),
                             ["Entwurf in Mitwirkung"], title)

    def test_only_a_stated_range_is_a_display_period(self):
        def label(text, published="2026-09-01"):
            return list(self.status([record(published, "Gemeinde Seon",
                                             'Gestaltungsplan "Rain"; öffentliche Auflage',
                                             text)]).values())[0]
        self.assertEqual(label("Die Behörde entscheidet bis spätestens 31. Dezember 2026."),
                         "Stand unbestätigt")
        self.assertEqual(label("Die Akten liegen vom 2. November 2026 bis 1. Dezember 2026 auf."),
                         "Stand unbestätigt")
        self.assertEqual(label("Auflage: 21.09.2026 – 20.10.2026."), "Entwurf in Auflage")
        self.assertEqual(label("Mitwirkung vom 1. Mai 2026 bis 30. Juni 2026; Auflage vom "
                               "21. September 2026 bis 20. Oktober 2026."), "Stand unbestätigt")

    def test_an_office_name_with_a_department_is_its_municipality(self):
        pubs, problems = P.publications([record("2026-09-01", "Gemeinde Seon - Bauverwaltung",
                                                'Gestaltungsplan "Rain"; Beschluss')])
        self.assertEqual(problems, [])
        self.assertEqual(pubs[0].municipality, "Seon")

    def test_an_unreadable_record_counts_for_the_municipality_it_names(self):
        _, problems = P.publications([{"published_on": "2026-09-01", "authority": "Gemeinde Seon",
                                       "title": 'Gestaltungsplan "Rain"; Beschluss'}])
        self.assertEqual([list(p.mentions) for p in problems], [["Seon"]])

    def test_a_title_stops_at_the_first_sentence(self):
        pubs, _ = P.publications([canton(
            "2026-09-01", 'Gemeinde Seon: Gestaltungsplan "Rain". Mit Entscheid vom 13. Juli 2026 '
            "hat das Departement den Plan genehmigt. Die Auflagen betreffen die Zufahrt.")])
        self.assertEqual(pubs[0].subject, "Gestaltungsplan «Rain»")
        pubs, _ = P.publications([record("2024-02-15", "Gemeinde Seon",
                                         "Strassenklassifizierungsplan inkl. Strassenverzeichnis; "
                                         "Mitwirkungsverfahren")])
        self.assertEqual(pubs[0].subject, "Strassenklassifizierungsplan inkl. Strassenverzeichnis")

    def test_a_neighbours_plan_inside_a_part_is_reported_not_attributed(self):
        pubs, problems = P.publications([canton(
            "2026-09-01", 'Gemeinde Seon: Gestaltungsplan "A"; Erschliessungsplan "Seeblick" '
            "der Nachbargemeinde Seengen")])
        self.assertEqual(P.view(pubs, "Seon", today=TODAY).all(), [])
        self.assertEqual(len(problems), 1)
        self.assertIn("Seengen", problems[0])

    def test_a_municipal_notice_about_another_municipality_is_reported(self):
        pubs, problems = P.publications([record("2026-09-01", "Gemeinde Seon",
                                                "Mitwirkung zur Nutzungsplanung der Gemeinde Seengen")])
        self.assertEqual(pubs, [])
        self.assertIn("Seengen", problems[0])

    def test_a_deadline_that_is_not_an_appeal_period_proves_nothing(self):
        self.assertEqual(self.status([canton(
            "2026-09-25", 'Gemeinde Seon: Gestaltungsplan "Rain" | Die bereinigten Pläne sind '
            "innert 30 Tagen einzureichen.")]),
            {"Gestaltungsplan «Rain»": "Genehmigt, Inkrafttreten nicht belegt"})

    def test_a_title_mention_of_an_attributed_municipality_is_not_reported_twice(self):
        _, problems = P.publications([canton(
            "2026-09-01", 'Gemeinde Seon: Gestaltungsplan "Rain"',
            title="Genehmigung von Sondernutzungsplänen (Seon)")])
        self.assertEqual(problems, [])


class CheckTest(unittest.TestCase):
    """`python -m planning check <file>` — what an import has to pass before
    the panel is switched to it: complete records, every canton notice
    attributed, no page fetched twice, a current date."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.path = os.path.join(self.tempdir.name, "publications.json")

    def write(self, records, stand="2026-10-05"):
        with open(self.path, "w") as handle:
            json.dump({"stand": stand, "since": "2019-01-01", "records": records}, handle)

    def test_a_check_dated_after_today_or_a_stand_ahead_fails_the_check(self):
        self.write([explicit("00.900.712", "2026-09-01", "Gemeinde Seon", "Gestaltungsplan «Rain»",
                             municipality="Seon",
                             verified=proof("in_kraft", verified_on="2026-10-07", effective_on="2026-07-01"))])
        ok, lines = P.check(self.path, today=TODAY)
        self.assertFalse(ok)
        self.assertTrue(any("geprüft am 07.10.2026, nach heute" in line for line in lines), lines)
        self.write([], stand="2026-12-31")
        ok, lines = P.check(self.path, today=TODAY)
        self.assertFalse(ok)
        self.assertTrue(any("Stand 31.12.2026 liegt nach heute" in line for line in lines), lines)

    def test_a_clean_store_passes_and_says_what_it_holds(self):
        self.write(SEON)
        ok, lines = P.check(self.path, today=TODAY)
        report = "\n".join(lines)
        self.assertTrue(ok, report)
        self.assertIn("8 Publikationen", report)
        self.assertIn("Seon", report)
        # Version 1: publications counted, no step read from them.
        self.assertRegex(report, r"Seon: \d+ Publikationen")
        self.assertNotIn("Mitwirkung:", report)
        self.assertNotIn("Entwurf in Mitwirkung", report)
        saved = os.environ.get(P.ENV_INFERENCE)
        os.environ[P.ENV_INFERENCE] = "1"
        try:
            self.assertIn("Entwurf in Mitwirkung: 1", "\n".join(P.check(self.path, today=TODAY)[1]))
        finally:
            if saved is None:
                os.environ.pop(P.ENV_INFERENCE, None)
            else:
                os.environ[P.ENV_INFERENCE] = saved

    def test_by_default_the_check_counts_publications_not_steps(self):
        """As on the card: a canton notice refusing this municipality's plan
        is a publication, no Genehmigung — nor is the one approving it."""
        self.write([
            record("2026-03-05", "Abteilung Raumentwicklung", "Genehmigung von Sondernutzungsplänen",
                   'Gemeinde Seon: Gestaltungsplan "Giessi"; Genehmigung verweigert',
                   pub_nr="00.900.241", rubric="Kanton / Raumplanung"),
            record("2026-03-04", "Abteilung Raumentwicklung", "Genehmigung von Sondernutzungsplänen",
                   'Gemeinde Seon: Gestaltungsplan "Rain"', pub_nr="00.900.242",
                   rubric="Kanton / Raumplanung")])
        report = "\n".join(P.check(self.path, today=TODAY)[1])
        self.assertIn("Seon: 2 Publikationen", report)
        self.assertNotIn("Genehmigung:", report)

    def test_a_publication_fetched_twice_fails(self):
        """Overlapping pages in an import show up as the same publication
        number twice."""
        self.write(SEON + [SEON[0]])
        ok, lines = P.check(self.path, today=TODAY)
        self.assertFalse(ok)
        self.assertIn("00.016.453", "\n".join(lines))

    def test_an_unattributed_canton_notice_fails(self):
        self.write(SEON + [record("2026-03-01", "Abteilung Raumentwicklung",
                                  "Genehmigung von Sondernutzungsplänen",
                                  "im Gebiet der Gemeinden Seon und Seengen", "00.999.001")])
        ok, lines = P.check(self.path, today=TODAY)
        self.assertFalse(ok)
        self.assertIn("keiner Gemeinde zugeordnet", "\n".join(lines))

    def test_a_search_link_as_source_fails(self):
        """The arrow is meant to open the publication itself; a search link in
        its place has to be caught before the panel shows it."""
        self.write(SEON + [dict(record("2026-09-02", "Gemeinde Seon",
                                       'Gestaltungsplan "Rand"; Beschluss', pub_nr="00.999.002"),
                                url="https://amtsblatt.ag.ch/publikationen/?searchQuery=%22Seon%22")])
        ok, lines = P.check(self.path, today=TODAY)
        self.assertFalse(ok)
        self.assertIn("keine Einzelpublikation", "\n".join(lines))

    def test_a_stale_store_fails(self):
        self.write(SEON, stand="2026-08-01")
        ok, lines = P.check(self.path, today=TODAY)
        self.assertFalse(ok)
        self.assertIn("veraltet", "\n".join(lines))

    def test_an_unreadable_store_fails(self):
        ok, lines = P.check(self.path, today=TODAY)
        self.assertFalse(ok)
        self.assertIn("nicht lesbar", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
