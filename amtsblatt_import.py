"""Fill the publication store from a batch an operator wrote — or, once its
format has been seen, an adapter for another channel. Nothing here fetches
anything: `robots.txt` keeps automated readers off the Amtsblatt's search and
publications, and its legal notice reserves every use beyond the legally
permitted cases to the Staatskanzlei's written consent (`amtsblatt.py`,
NEWSFEED.md §5). The channels the Amtsblatt
documents are its website, a search subscription mailed daily or weekly —
whose format nobody here has seen, so no parser for it exists — and a PDF
export of selected publications.

A batch is one CSV (comma or semicolon, UTF-8) or JSON file, one row per
publication and Gemeinde it concerns, with only what block E shows — never
the notice's text:

    pub_nr           Publikationsnummer as printed, 00.102.603          required
    published_on     2026-09-10 or 10.09.2026                            required
    authority        publizierende Stelle, as printed                    required
    rubric           e.g. "Kanton / Raumplanung"                          optional
    title            as printed                                           required
    url              the publication itself, amtsblatt.ag.ch/ekab/…       required
    municipality     the Gemeinde the row concerns (stored in the         } exactly
                     directory's spelling: "Hausen AG" → "Hausen (AG)")  } one
    canton_wide      "ja" for a change that applies to every Gemeinde —
                     only from a canton office in a "Kanton / …" rubric
    part             a canton notice naming several Gemeinden: the part    optional
                     about this one, as printed
    part_nr          1, 2, … when the notice names this Gemeinde twice     optional
    stage            a checked stage — auflage, genehmigt, in_kraft or     optional
                     verweigert — with stage_source, verified_on,
                     verified_method and the dates it needs: auflage_from,
                     auflage_to, decided_on, approved_on, appeal_until,
                     rechtskraft_on, effective_on (`planning.Verified`)

A batch states what it covers: every publication of its Gemeinden — all of
them only with an explicit `alle` — published from `covers_from` to
`covers_until`, which cannot lie after the day of the import. A JSON batch
carries these beside `records` and `source`; for a CSV they are given on the
command line, which also overrides a JSON batch's own. Coverage that is
missing, blank (`null`, `""`, `[]`) or no list is refused, never read as
`alle`:

    python -m amtsblatt_import STORE BATCH --von 2026-09-01 --bis 2026-10-05 \\
        --gemeinden Seon,Reinach --quelle "manuell aus amtsblatt.ag.ch"

The store is replaced only by a valid one, atomically and under a lock. Rows
are keyed by publication number and Gemeinde: an import run twice adds
nothing, a corrected row replaces its predecessor. A batch that would leave a
gap after the store's `stand`, covers other Gemeinden, contradicts itself or
carries one invalid row changes nothing. Every attempt is recorded in
`STORE.status.json`, which the panel reads: a failed update is shown beside
the last valid list, which stays.
"""
import argparse
import csv
import fcntl
import io
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import amtsblatt as A
import planning as P

#: A batch row's columns, in the order a spreadsheet template lists them.
COLUMNS = ("pub_nr", "published_on", "authority", "rubric", "title", "url", "municipality",
           "canton_wide", "part", "part_nr", "stage", "stage_source", "verified_on", "verified_method",
           *P._STAGE_DATES)
_REQUIRED = ("pub_nr", "published_on", "authority", "title", "url")
_CHECK_COLUMNS = ("stage_source", "verified_on", "verified_method", *P._STAGE_DATES)
_YES, _NO = {"ja", "yes", "true", "1", "x"}, {"", "nein", "no", "false", "0"}
#: How many past imports the store's log keeps.
LOG_LENGTH = 50


@dataclass
class Batch:
    records: list
    covers_from: Optional[date]
    covers_until: Optional[date]
    #: The Gemeinden it covers; None: all of them, from an explicit «alle»
    #: only. An empty list names none and is refused.
    municipalities: Optional[list]
    source: str
    #: What could not be read, row by row. One is enough to refuse the batch.
    errors: list = field(default_factory=list)

    @classmethod
    def from_rows(cls, rows, covers_from, covers_until, municipalities, source):
        records, errors = [], []
        for number, raw in enumerate(rows, start=1):
            record, problems = normalise(raw, f"Zeile {number}")
            errors += problems
            if record is not None:
                records.append(record)
        return cls(records, covers_from, covers_until, municipalities, source or "", errors)


@dataclass
class Result:
    ok: bool
    message: str
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    #: Identical rows within the batch, taken once.
    repeated: int = 0


def _day(value):
    """An ISO or Swiss date as ISO text, "" when empty; ValueError otherwise."""
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    swiss = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
    if swiss:
        day, month, year = (int(part) for part in swiss.groups())
        return date(year, month, day).isoformat()
    return date.fromisoformat(text).isoformat()


def normalise(raw, where="Zeile"):
    """(record in the store's form, problems). A row from a spreadsheet or an
    adapter: dates made ISO, "ja" made True, a checked stage nested under
    `verified`, empty fields dropped."""
    if not isinstance(raw, dict):
        return None, [f"{where}: kein Datensatz"]
    text = lambda name: str(raw.get(name) if raw.get(name) is not None else "").strip()  # noqa: E731
    label = f"{where} ({text('pub_nr') or '?'})"
    problems = [f"{label}: {name} fehlt" for name in _REQUIRED if not text(name)]
    record = {name: text(name) for name in ("pub_nr", "authority", "rubric", "title", "url",
                                            "municipality", "part")}
    try:
        record["published_on"] = _day(raw.get("published_on"))
    except ValueError:
        problems.append(f"{label}: published_on «{text('published_on')}» ist kein Datum")
    wide = raw.get("canton_wide")
    if not isinstance(wide, bool):
        flag = text("canton_wide").casefold()
        wide = flag in _YES
        if flag not in _YES | _NO:
            problems.append(f"{label}: canton_wide «{flag}» — «ja» oder leer")
    if wide:
        record["canton_wide"] = True
    if record["municipality"]:
        record["municipality"] = A.canonical_name(record["municipality"])
    if bool(record["municipality"]) == bool(wide):
        problems.append(f"{label}: genau eines von municipality und canton_wide angeben")
    if text("part_nr"):
        number = P._part_nr(raw.get("part_nr") if isinstance(raw.get("part_nr"), int) else text("part_nr"))
        if number is None:
            problems.append(f"{label}: part_nr «{text('part_nr')}» ist keine Zahl ab 1")
        else:
            record["part_nr"] = number
    checked = raw.get("verified")
    if checked is None and text("stage"):
        checked = {"stage": text("stage"), "source": text("stage_source"),
                   "verified_on": text("verified_on"), "method": text("verified_method"),
                   **{name: text(name) for name in P._STAGE_DATES}}
    elif checked is None and any(text(name) for name in _CHECK_COLUMNS):
        problems.append(f"{label}: Prüfangaben ohne stage")
    if checked is not None:
        if not isinstance(checked, dict):
            problems.append(f"{label}: verified ist kein Objekt")
        else:
            checked = {key: value for key, value in checked.items() if value not in (None, "")}
            for name in ("verified_on", *P._STAGE_DATES):
                if name in checked:
                    try:
                        checked[name] = _day(checked[name])
                    except ValueError:
                        problems.append(f"{label}: {name} «{checked[name]}» ist kein Datum")
            record["verified"] = checked
    return {key: value for key, value in record.items() if value not in ("", None)}, problems


def read_batch(path, covers_from=None, covers_until=None, municipalities=None, source=""):
    """A batch from a CSV or JSON file. Coverage given here — the command
    line's, as typed — overrides the file's own; a CSV has none of its own.
    `municipalities=None` means none given here, never «alle»."""
    covered, problem = ([], "") if municipalities is None else _municipalities(municipalities, "--gemeinden")
    try:
        if path.lower().endswith(".json"):
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
            rows = payload.get("records") if isinstance(payload, dict) else payload
            meta = payload if isinstance(payload, dict) else {}
            covers_from = covers_from or _date_or_none(meta.get("covers_from"))
            covers_until = covers_until or _date_or_none(meta.get("covers_until"))
            if municipalities is None:
                covered, problem = _municipalities(meta.get("municipalities"))
            source = source or str(meta.get("source") or "")
        else:
            with open(path, encoding="utf-8-sig", newline="") as handle:
                content = handle.read()
            header = content.split("\n", 1)[0]
            delimiter = ";" if header.count(";") > header.count(",") else ","
            rows = list(csv.DictReader(io.StringIO(content), delimiter=delimiter))
    except (OSError, ValueError) as exc:
        return Batch([], covers_from, covers_until, covered, source,
                     [f"Datei nicht lesbar: {type(exc).__name__}: {exc}"])
    if not isinstance(rows, list):
        return Batch([], covers_from, covers_until, covered, source, ["keine Liste von Zeilen"])
    batch = Batch.from_rows(rows, covers_from, covers_until, covered, source)
    if problem:
        batch.errors.insert(0, problem)
    return batch


def _date_or_none(value):
    try:
        return date.fromisoformat(_day(value)) if _day(value) else None
    except ValueError:
        return None


#: The one word that makes a batch cover every Gemeinde.
ALL = "alle"


def _municipalities(value, where="municipalities"):
    """(the Gemeinden named, in the directory's spelling; a problem). None
    stands for the explicit «alle» and only for it: a missing, blank or
    malformed value names none, which the import refuses — an export's empty
    cell must not declare every Gemeinde checked."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)):
        if value is None:
            return [], ""
        return [], f"{where} «{json.dumps(value, ensure_ascii=False, default=str)}» ist keine Liste von Gemeinden"
    names = [str(name).strip() for name in value if str(name).strip()]
    if names == [ALL]:
        return None, ""
    return [A.canonical_name(name) for name in names], ""


def _named(names):
    return ALL if names is None else ", ".join(names) or "keine"


def read_status(store_path) -> dict:
    try:
        with open(P.status_path(store_path), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _write_atomically(path, payload):
    """Write JSON beside `path`, flush it to disk, then rename it over `path`:
    a reader sees the old file or the new one, never half of either."""
    folder = os.path.dirname(os.path.abspath(path))
    handle, temporary = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", dir=folder)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(payload, out, ensure_ascii=False, indent=1, sort_keys=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    try:
        directory = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        pass  # Not every filesystem lets a directory be synced; the rename stands.


def _record_status(store_path, now, ok, error=""):
    status = read_status(store_path)
    status.update({"last_attempt_at": now.isoformat(timespec="seconds"), "last_attempt_ok": ok,
                   "last_error": error})
    if ok:
        status["last_success_at"] = status["last_attempt_at"]
    try:
        _write_atomically(P.status_path(store_path), status)
    except OSError:
        pass  # The store's own state is what matters; a missing status says nothing.


def _coverage_problems(batch, current, today):
    problems = []
    if batch.municipalities is not None and not batch.municipalities:
        problems.append("Gemeinden fehlen: Liste der erfassten Gemeinden oder «alle» angeben "
                        "(JSON: municipalities, CSV: --gemeinden)")
    if not batch.covers_from or not batch.covers_until:
        return problems + ["Zeitraum fehlt: covers_from und covers_until (--von, --bis) angeben"]
    if batch.covers_from > batch.covers_until:
        problems.append("Zeitraum: covers_from liegt nach covers_until")
    if batch.covers_until > today:
        # A list complete through a day that has not come yet would read as
        # "none" for publications not yet made.
        problems.append(f"Zeitraum endet am {batch.covers_until:%d.%m.%Y}, nach heute ({today:%d.%m.%Y})")
    for name in batch.municipalities or ():
        if not A.authorities_for(name):
            problems.append(f"Gemeinde «{name}» ist nicht im Verzeichnis")
    if current is None:
        return problems
    keys = lambda names: None if names is None else sorted({A.municipality_key(n) for n in names})  # noqa: E731
    if keys(batch.municipalities) != keys(current.get("municipalities")):
        problems.append(f"Gemeinden des Imports ({_named(batch.municipalities)}) passen "
                        f"nicht zum Verzeichnis ({_named(current.get('municipalities'))})")
    since, stand = (date.fromisoformat(current["since"]), date.fromisoformat(current["stand"]))
    if batch.covers_from > stand + timedelta(days=1):
        problems.append(f"Lücke: Verzeichnis bis {stand:%d.%m.%Y}, Import ab {batch.covers_from:%d.%m.%Y}")
    if batch.covers_until < since - timedelta(days=1):
        problems.append(f"Lücke: Import bis {batch.covers_until:%d.%m.%Y}, Verzeichnis ab {since:%d.%m.%Y}")
    return problems


def _row_problems(batch, today):
    # A batch naming no Gemeinde is refused once, not once per row.
    covered = {A.municipality_key(n) for n in batch.municipalities} if batch.municipalities else None
    problems = []
    for record in batch.records:
        key = P.record_key(record)
        day = _date_or_none(record.get("published_on"))
        if day and batch.covers_from and batch.covers_until and not (
                batch.covers_from <= day <= batch.covers_until):
            problems.append(f"{key}: publiziert {day:%d.%m.%Y}, ausserhalb des Zeitraums "
                            f"{batch.covers_from:%d.%m.%Y}–{batch.covers_until:%d.%m.%Y}")
        if day and day > today:
            problems.append(f"{key}: publiziert {day:%d.%m.%Y}, nach heute")
        name = record.get("municipality")
        if name and covered is not None and A.municipality_key(name) not in covered:
            problems.append(f"{key}: Gemeinde {name} liegt ausserhalb der erfassten Gemeinden")
    return problems


def import_batch(store_path, batch, today=None, now=None) -> Result:
    """Merge `batch` into the store at `store_path` and replace it — only if the
    result is a valid store. Returns what happened; never raises for bad data."""
    today, now = today or date.today(), now or datetime.now()
    lock_path = f"{store_path}.lock"
    with open(lock_path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            result = _import_locked(store_path, batch, today, now)
        except Exception as exc:  # the store stays as it was; the reason is recorded
            result = Result(False, f"Import abgebrochen: {type(exc).__name__}: {exc}")
        _record_status(store_path, now, result.ok, "" if result.ok else result.message)
        P.reset_cache()
        return result


def _import_locked(store_path, batch, today, now):
    current = None
    if os.path.exists(store_path):
        try:
            with open(store_path, encoding="utf-8") as handle:
                current = json.load(handle)
            if not isinstance(current, dict) or not current.get("since") or not current.get("stand"):
                raise ValueError("ohne since/stand")
        except (OSError, ValueError) as exc:
            return Result(False, f"Bestehendes Verzeichnis nicht lesbar ({exc}) — nichts ersetzt")
    problems = batch.errors + _coverage_problems(batch, current, today) + _row_problems(batch, today)
    merged, counts = {}, {"added": 0, "updated": 0, "unchanged": 0, "repeated": 0}
    seen = {}
    for record in batch.records:
        key = P.record_key(record)
        if key in seen:
            if seen[key] == record:
                counts["repeated"] += 1
            else:
                problems.append(f"{key}: zweimal im Import, widersprüchlich")
            continue
        seen[key] = record
    if problems:
        return Result(False, "Import abgelehnt — nichts ersetzt:\n" + "\n".join(problems), **counts)
    stored = {P.record_key(r): r for r in (current or {}).get("records", []) if isinstance(r, dict)}
    for key, record in seen.items():
        if key not in stored:
            counts["added"] += 1
        elif stored[key] != record:
            counts["updated"] += 1
        else:
            counts["unchanged"] += 1
        stored[key] = record
    since = min(filter(None, [batch.covers_from, _date_or_none((current or {}).get("since"))]))
    stand = max(filter(None, [batch.covers_until, _date_or_none((current or {}).get("stand"))]))
    payload = {"since": since.isoformat(), "stand": stand.isoformat()}
    if batch.municipalities is not None:
        payload["municipalities"] = batch.municipalities
    payload["source"] = batch.source or (current or {}).get("source", "")
    unchanged = (current is not None and not counts["added"] and not counts["updated"]
                 and all(current.get(k) == payload.get(k) for k in ("since", "stand", "municipalities")))
    if unchanged:
        return Result(True, f"Verzeichnis unverändert ({counts['unchanged']} bereits erfasst)", **counts)
    payload["updated_at"] = now.isoformat(timespec="seconds")
    log = list((current or {}).get("imports", []))[-(LOG_LENGTH - 1):]
    log.append({"at": payload["updated_at"], "covers_from": batch.covers_from.isoformat(),
                "covers_until": batch.covers_until.isoformat(), "source": batch.source,
                "added": counts["added"], "updated": counts["updated"]})
    payload["imports"] = log
    payload["records"] = sorted(stored.values(), key=lambda r: (r.get("published_on", ""), P.record_key(r)))
    # Validate the store as the panel will read it, before it can replace one.
    folder = os.path.dirname(os.path.abspath(store_path))
    handle, candidate = tempfile.mkstemp(prefix=f".{os.path.basename(store_path)}.check.", suffix=".json",
                                         dir=folder)
    os.close(handle)
    try:
        with open(candidate, "w", encoding="utf-8") as out:
            json.dump(payload, out, ensure_ascii=False)
        ok, lines = P.check(candidate, today=today, stale_ok=True)
    finally:
        os.unlink(candidate)
        P.reset_cache()
    if not ok:
        failures = [line for line in lines[1:] if line.startswith(("Problem", "Doppelt", "Quelle",
                                                                   "Erfassungsbeginn"))]
        return Result(False, "Import abgelehnt — nichts ersetzt:\n" + "\n".join(failures or lines), **counts)
    _write_atomically(store_path, payload)
    stale = (today - stand).days > P.STALE_DAYS
    return Result(True, f"{counts['added']} neu, {counts['updated']} geändert, {counts['unchanged']} unverändert"
                        f" — Stand {stand:%d.%m.%Y}" + (" (veraltet)" if stale else ""), **counts)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m amtsblatt_import", description=__doc__.split("\n\n")[0])
    parser.add_argument("store", help="das Publikationsverzeichnis (JSON), SCOPE_PLANNING_PUBLICATIONS")
    parser.add_argument("batch", help="CSV oder JSON im Format oben")
    parser.add_argument("--von", help="erfasst ab (Datum)")
    parser.add_argument("--bis", help="erfasst bis (Datum)")
    parser.add_argument("--gemeinden", help="erfasste Gemeinden, mit Komma getrennt, oder «alle»")
    parser.add_argument("--quelle", default="", help="woher die Zeilen stammen")
    parser.add_argument("--heute", help="Datum der Prüfung (Standard: heute)")
    args = parser.parse_args(argv)
    try:
        today = date.fromisoformat(_day(args.heute)) if args.heute else date.today()
        covers_from = date.fromisoformat(_day(args.von)) if args.von else None
        covers_until = date.fromisoformat(_day(args.bis)) if args.bis else None
    except ValueError as exc:
        parser.error(f"Datum: {exc}")
    batch = read_batch(args.batch, covers_from, covers_until, args.gemeinden, args.quelle)
    result = import_batch(args.store, batch, today=today)
    print(result.message)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
