import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime
from unittest.mock import patch

import amtsblatt_import as I
import planning as P

TODAY = date(2026, 10, 6)
NOW = datetime(2026, 10, 6, 8, 0)
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def row(pub_nr, published_on, title, municipality="Seon", authority=None, **extra):
    record = {"pub_nr": pub_nr, "published_on": published_on,
              "authority": authority or f"Gemeinde {municipality}",
              "rubric": "Gemeinden / Bau- und Nutzungsordnung", "title": title,
              "url": f"https://amtsblatt.ag.ch/ekab/{pub_nr}/pdf/", "municipality": municipality}
    record.update(extra)
    return record


class ImporterTest(unittest.TestCase):
    """The store is replaced only by a valid one: an import that would leave a
    gap, mix up Gemeinden or carry one bad row changes nothing, and says so."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = os.path.join(self.folder.name, "publications.json")
        P.reset_cache()
        self.addCleanup(P.reset_cache)

    def batch(self, rows, covers_from="2026-09-01", covers_until="2026-10-05", municipalities=("Seon",),
              source="manuell aus amtsblatt.ag.ch"):
        return I.Batch.from_rows(rows, date.fromisoformat(covers_from), date.fromisoformat(covers_until),
                                 list(municipalities) if municipalities is not None else None, source)

    def run_import(self, batch, today=TODAY):
        return I.import_batch(self.store, batch, today=today, now=NOW)

    def stored(self):
        with open(self.store, encoding="utf-8") as handle:
            return json.load(handle)

    def raw(self):
        with open(self.store, "rb") as handle:
            return handle.read()

    def two(self):
        return self.batch([row("00.900.801", "2026-09-10", 'Gestaltungsplan "Rain"; öffentliche Auflage'),
                           row("00.900.802", "2026-09-24", 'Gestaltungsplan "Rain"; Beschluss')])

    def test_a_first_import_creates_the_store_with_what_it_covers(self):
        result = self.run_import(self.two())
        self.assertTrue(result.ok, result.message)
        self.assertEqual((result.added, result.updated, result.unchanged), (2, 0, 0))
        store = self.stored()
        self.assertEqual((store["since"], store["stand"], store["municipalities"]),
                         ("2026-09-01", "2026-10-05", ["Seon"]))
        self.assertEqual(store["updated_at"], "2026-10-06T08:00:00")
        self.assertEqual(store["source"], "manuell aus amtsblatt.ag.ch")
        self.assertTrue(P.check(self.store, today=TODAY)[0])
        self.assertEqual(len(P.state_for("Seon", [], path=self.store).publications), 2)
        status = I.read_status(self.store)
        self.assertTrue(status["last_attempt_ok"])
        self.assertEqual(status["last_success_at"], "2026-10-06T08:00:00")

    def test_the_same_import_twice_changes_nothing(self):
        self.run_import(self.two())
        before, stamp = self.raw(), os.stat(self.store).st_mtime_ns
        result = self.run_import(self.two())
        self.assertTrue(result.ok, result.message)
        self.assertEqual((result.added, result.updated, result.unchanged), (0, 0, 2))
        self.assertEqual((self.raw(), os.stat(self.store).st_mtime_ns), (before, stamp))

    def test_a_corrected_row_replaces_the_stored_one(self):
        self.run_import(self.two())
        result = self.run_import(self.batch([row("00.900.802", "2026-09-24",
                                                 'Gestaltungsplan "Rain"; Beschluss des Gemeinderats')]))
        self.assertTrue(result.ok, result.message)
        self.assertEqual((result.added, result.updated), (0, 1))
        titles = sorted(r["title"] for r in self.stored()["records"])
        self.assertEqual(titles, ['Gestaltungsplan "Rain"; Beschluss des Gemeinderats',
                                  'Gestaltungsplan "Rain"; öffentliche Auflage'])

    def test_a_row_twice_in_one_import_is_taken_once_and_a_contradiction_refused(self):
        same = row("00.900.801", "2026-09-10", 'Gestaltungsplan "Rain"; öffentliche Auflage')
        result = self.run_import(self.batch([same, dict(same)]))
        self.assertTrue(result.ok, result.message)
        self.assertEqual((result.added, result.repeated), (1, 1))
        result = self.run_import(self.batch([same, dict(same, title="Etwas anderes")]))
        self.assertFalse(result.ok)
        self.assertIn("00.900.801 (Seon)", result.message)
        self.assertIn("widersprüchlich", result.message)

    def test_two_parts_of_one_notice_for_one_municipality_need_their_numbers(self):
        notice = dict(authority="Abteilung Raumentwicklung", rubric="Kanton / Raumplanung",
                      title="Genehmigung von Sondernutzungsplänen")
        parts = [row("00.900.830", "2026-09-17", municipality="Seon", part="Aufhebung", **notice),
                 row("00.900.830", "2026-09-17", municipality="Seon", part='Gestaltungsplan "Giessi"', **notice)]
        result = self.run_import(self.batch(parts))
        self.assertFalse(result.ok)
        self.assertIn("00.900.830 (Seon)", result.message)
        result = self.run_import(self.batch([dict(parts[0], part_nr="1"), dict(parts[1], part_nr="2")]))
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.added, 2)

    def test_a_json_batch_must_say_which_municipalities_it_covers(self):
        path = os.path.join(self.folder.name, "batch.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"covers_from": "2026-09-01", "covers_until": "2026-10-05", "source": "Adapter",
                       "records": [row("00.900.840", "2026-09-10", "Gestaltungsplan Rain")]}, handle)
        result = self.run_import(I.read_batch(path))
        self.assertFalse(result.ok)
        self.assertIn("Gemeinden fehlen", result.message)
        self.assertFalse(os.path.exists(self.store))

    def json_batch(self, **meta):
        path = os.path.join(self.folder.name, "batch.json")
        payload = {"covers_from": "2026-10-01", "covers_until": "2026-10-05", "source": "Test",
                   "records": [row("00.900.850", "2026-10-05", 'Gestaltungsplan "Rain"')]}
        payload.update(meta)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return path

    def test_blank_or_malformed_coverage_is_refused_never_read_as_all(self):
        """Only the word «alle» covers every Gemeinde. A null or an empty cell
        in an export would otherwise tell Menziken — never collected — that it
        has no publications."""
        for value in (None, "", "  ", ",", [], [""], 5, True, {"Seon": 1}):
            result = self.run_import(I.read_batch(self.json_batch(municipalities=value)))
            self.assertFalse(result.ok, value)
            self.assertIn("Gemeinden fehlen", result.message, value)
            self.assertNotIn("ausserhalb", result.message, value)  # said once, not per row
            self.assertFalse(os.path.exists(self.store), value)
        result = self.run_import(I.read_batch(self.json_batch(municipalities=5)))
        self.assertIn("municipalities «5» ist keine Liste", result.message)
        self.assertTrue(self.run_import(I.read_batch(self.json_batch(municipalities=["Seon"]))).ok)
        before = self.raw()
        result = self.run_import(I.read_batch(self.json_batch(municipalities=None)))
        self.assertFalse(result.ok)
        self.assertIn("Gemeinden des Imports (keine) passen nicht zum Verzeichnis (Seon)", result.message)
        self.assertEqual(self.raw(), before)
        self.assertEqual(P.state_for("Menziken", [], path=self.store).status, "uncovered")

    def test_only_the_word_alle_covers_every_municipality(self):
        for value in ("alle", ["alle"]):
            result = self.run_import(I.read_batch(self.json_batch(municipalities=value)))
            self.assertTrue(result.ok, (value, result.message))
            self.assertNotIn("municipalities", self.stored())
            self.assertEqual(P.state_for("Menziken", [], path=self.store).status, "ok", value)
            os.unlink(self.store)
        result = self.run_import(I.read_batch(self.json_batch(municipalities=["Seon"])))
        self.assertTrue(result.ok, result.message)
        self.assertEqual(P.state_for("Menziken", [], path=self.store).status, "uncovered")

    def cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = I.main([self.store, *args])
        return code, out.getvalue()

    def test_the_command_line_states_coverage_as_strictly_as_a_file(self):
        """A blank `--gemeinden` is refused like a blank file value; an
        explicit «alle» there is taken, and overrides the file's own."""
        batch = os.path.join(self.folder.name, "batch.csv")
        with open(batch, "w", encoding="utf-8") as handle:
            handle.write("pub_nr,published_on,authority,title,url,municipality\n"
                         "00.900.851,2026-10-02,Gemeinde Seon,Gestaltungsplan Rain,"
                         "https://amtsblatt.ag.ch/ekab/00.900.851/pdf/,Seon\n")
        for blank in ("", " ", ","):
            code, out = self.cli(batch, "--von", "2026-10-01", "--bis", "2026-10-05",
                                 "--gemeinden", blank, "--heute", "2026-10-06")
            self.assertEqual(code, 1, (blank, out))
            self.assertIn("Gemeinden fehlen", out, blank)
            self.assertFalse(os.path.exists(self.store), blank)
        code, out = self.cli(self.json_batch(), "--gemeinden", "alle", "--heute", "2026-10-06")
        self.assertEqual(code, 0, out)
        self.assertNotIn("municipalities", self.stored())
        os.unlink(self.store)
        code, out = self.cli(self.json_batch(municipalities="alle"), "--gemeinden", "Seon",
                             "--heute", "2026-10-06")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.stored()["municipalities"], ["Seon"])

    def test_nothing_from_after_today_is_taken(self):
        result = self.run_import(self.batch([row("00.900.841", "2026-09-10", "Plan")],
                                            covers_until="2026-12-31"))
        self.assertFalse(result.ok)
        self.assertIn("Zeitraum endet am 31.12.2026, nach heute", result.message)
        result = self.run_import(self.batch([row("00.900.842", "2026-10-07", "Plan")],
                                            covers_until="2026-10-06"))
        self.assertFalse(result.ok)
        self.assertIn("nach heute", result.message)

    def test_one_municipality_spelt_two_ways_is_one_row(self):
        hausen = row("00.900.843", "2026-09-10", "Gestaltungsplan Hof", municipality="Hausen AG",
                     authority="Gemeinde Hausen AG")
        self.assertTrue(self.run_import(self.batch([hausen], municipalities=("Hausen (AG)",))).ok)
        result = self.run_import(self.batch([dict(hausen, municipality="Hausen (AG)")],
                                            municipalities=("Hausen AG",)))
        self.assertTrue(result.ok, result.message)
        self.assertEqual((result.added, result.unchanged), (0, 1))
        self.assertEqual([r["municipality"] for r in self.stored()["records"]], ["Hausen (AG)"])

    def test_a_municipality_outside_the_batch_is_refused_even_if_it_exists(self):
        result = self.run_import(self.batch([row("00.900.844", "2026-09-10", "Gestaltungsplan Hof",
                                                 municipality="Reinach (AG)")]))
        self.assertFalse(result.ok)
        self.assertIn("ausserhalb der erfassten Gemeinden", result.message)

    def test_only_what_the_panel_shows_is_kept(self):
        self.run_import(self.batch([row("00.900.845", "2026-09-10", "Gestaltungsplan Rain",
                                        text="Der ganze Text der Publikation …")]))
        self.assertNotIn("text", self.stored()["records"][0])

    def test_a_gap_is_refused_and_the_last_valid_store_kept(self):
        self.run_import(self.two())
        before = self.raw()
        result = self.run_import(self.batch([row("00.900.803", "2026-10-12", 'Gestaltungsplan "Hof"')],
                                            covers_from="2026-10-10", covers_until="2026-10-12"))
        self.assertFalse(result.ok)
        self.assertIn("Lücke", result.message)
        self.assertEqual(self.raw(), before)
        status = I.read_status(self.store)
        self.assertFalse(status["last_attempt_ok"])
        self.assertIn("Lücke", status["last_error"])
        self.assertEqual(status["last_success_at"], "2026-10-06T08:00:00")
        store = P.load_store(self.store)
        self.assertEqual(store.status, "ok")
        self.assertIn("Lücke", store.failure)

    def test_another_set_of_municipalities_is_refused(self):
        self.run_import(self.two())
        result = self.run_import(self.batch([row("00.900.804", "2026-09-12", 'Gestaltungsplan "Weid"',
                                                 municipality="Reinach")],
                                            municipalities=("Seon", "Reinach")))
        self.assertFalse(result.ok)
        self.assertIn("Gemeinden", result.message)

    def test_one_bad_row_refuses_the_whole_import(self):
        self.run_import(self.two())
        before = self.raw()
        for bad, words in (
                (row("00.900.805", "2026-09-12", "Plan", municipality="Atlantis", authority="Gemeinde Seon"),
                 "Atlantis"),
                (row("00.900.806", "2026-09-12", "Plan", url="https://amtsblatt.ag.ch/publikationen/?x=1"),
                 "keine Einzelpublikation"),
                (row("00.900.807", "2026-11-12", "Plan"), "ausserhalb"),
                (row("00.900.808", "31.02.2026", "Plan"), "published_on"),
                (row("00.900.809", "2026-09-12", ""), "title"),
                (row("00.900.810", "2026-09-12", "Plan", stage="genehmigt", verified_on="2026-10-06",
                     stage_source="https://amtsblatt.ag.ch/ekab/00.900.810/pdf/",
                     verified_method="Publikation gelesen"), "approved_on")):
            result = self.run_import(self.batch([row("00.900.811", "2026-09-12", "Gut"), bad]))
            self.assertFalse(result.ok, bad)
            self.assertIn(words, result.message, bad)
            self.assertEqual(self.raw(), before, bad)

    def test_a_crash_while_writing_keeps_the_last_valid_store(self):
        self.run_import(self.two())
        before = self.raw()
        with patch.object(I.os, "replace", side_effect=OSError("disk full")):
            result = self.run_import(self.batch([row("00.900.812", "2026-09-30", 'Gestaltungsplan "Hof"')]))
        self.assertFalse(result.ok)
        self.assertIn("disk full", result.message)
        self.assertEqual(self.raw(), before)
        leftovers = [n for n in os.listdir(self.folder.name) if n.startswith(".publications.json.")]
        self.assertEqual(leftovers, [])

    def test_a_spreadsheet_export_with_a_checked_stage(self):
        path = os.path.join(self.folder.name, "batch.csv")
        with open(path, "w", encoding="utf-8-sig") as handle:
            handle.write(
                "pub_nr;published_on;authority;rubric;title;url;municipality;canton_wide;part;"
                "stage;stage_source;verified_on;verified_method;approved_on;appeal_until\n"
                "00.900.813;25.09.2026;Departement Bau, Verkehr und Umwelt;Kanton / Raumplanung;"
                "Genehmigung von Sondernutzungsplänen;https://amtsblatt.ag.ch/ekab/00.900.813/pdf/;Seon;;"
                "Gestaltungsplan «Rain»;genehmigt;https://amtsblatt.ag.ch/ekab/00.900.813/pdf/;06.10.2026;"
                "Publikation gelesen;22.09.2026;27.10.2026\n"
                "00.900.814;30.09.2026;Departement Bau, Verkehr und Umwelt;Kanton / Anhörungs- und "
                "Mitwirkungsverfahren;\"Teilrevision Baugesetz (BauG); Anhörung\";"
                "https://amtsblatt.ag.ch/ekab/00.900.814/pdf/;;ja;;;;;;;\n")
        batch = I.read_batch(path, covers_from=date(2026, 9, 1), covers_until=date(2026, 10, 5),
                             municipalities=["Seon"], source="manuell")
        result = self.run_import(batch)
        self.assertTrue(result.ok, result.message)
        view = P.state_for("Seon", [], path=self.store)
        self.assertEqual([P.stage_badge(p, TODAY)[0] for p in view.publications], ["genehmigt"])
        self.assertEqual([p.title for p in view.canton_publications], ["Teilrevision Baugesetz (BauG); Anhörung"])

    def test_a_json_batch_as_any_other_channel_would_write_it(self):
        path = os.path.join(self.folder.name, "batch.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"covers_from": "2026-09-01", "covers_until": "2026-10-05", "municipalities": ["Seon"],
                       "source": "Suchabo-E-Mail (Adapter)",
                       "records": [row("00.900.815", "2026-09-10", 'Gestaltungsplan "Rain"')]}, handle)
        result = self.run_import(I.read_batch(path))
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.stored()["source"], "Suchabo-E-Mail (Adapter)")

    def test_the_command_line(self):
        batch = os.path.join(self.folder.name, "batch.csv")
        with open(batch, "w", encoding="utf-8") as handle:
            handle.write("pub_nr,published_on,authority,title,url,municipality\n"
                         "00.900.816,2026-09-10,Gemeinde Seon,Gestaltungsplan Rain,"
                         "https://amtsblatt.ag.ch/ekab/00.900.816/pdf/,Seon\n")
        run = lambda *args: subprocess.run(  # noqa: E731
            [sys.executable, "-m", "amtsblatt_import", self.store, batch, *args],
            cwd=HERE, capture_output=True, text=True, timeout=60)
        done = run("--von", "2026-09-01", "--bis", "2026-10-05", "--gemeinden", "Seon",
                   "--quelle", "manuell", "--heute", "2026-10-06")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("1 neu", done.stdout)
        gap = run("--von", "2026-10-10", "--bis", "2026-10-12", "--gemeinden", "Seon", "--heute", "2026-10-12")
        self.assertEqual(gap.returncode, 1)
        self.assertIn("Lücke", gap.stdout + gap.stderr)


if __name__ == "__main__":
    unittest.main()
