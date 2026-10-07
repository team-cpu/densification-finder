"""Planning procedures under way — what the Amtsblatt says is coming.

Version 1 of block E uses the plain part of this module only: publications
attributed to one municipality or to the canton as a whole (`publications`,
`View.publications`), each shown as printed (`publication_title`), with no
step or date from its title or text shown. Everything from "chained" on is
the inference path, OFF unless `SCOPE_PLANNING_INFERENCE` is set
(`inference_enabled`): its text heuristics are tested against fixtures, not
trusted with a status of today.

OEREBlex (`regulations.py`) answers "what governs this parcel, since when". It
cannot say what is coming: `has_prepublications` is false for every
municipality, so a revision in Mitwirkung, on public display, or decided but
not yet approved is invisible there. The Amtsblatt publishes each of those
steps, and this module turns its publications into what a parcel can show:

* **attributed** to one municipality — a municipality's own notices by their
  publishing office, the canton's notices by the "Gemeinde X:" part that names
  it. One canton notice often approves plans of several municipalities; it is
  split at every municipality it names, and a part that names another
  municipality, or names one without the "Gemeinde X:" form, is reported —
  never attributed by guess. A notice counts as canton-wide only when its
  rubric is the canton's and its title names a canton-wide instrument
  (Baugesetz, Bauverordnung, Richtplan) and no municipality, municipal plan
  or Planungszone — and, for an approval, the Richtplan.
* **chained** — Mitwirkung, Auflage, Beschluss and Genehmigung of one plan are
  one revision at its latest step. An amendment, revision or repeal of a plan
  is its own revision, and a new Mitwirkung after a decision starts a new one.
  A canton approval that does not name the plan is not attached to one.
* **labelled** — by what a source shows, never by what no source shows.
  Philipp's three labels (2026-10-05) where the evidence supports them:
  "Entwurf in Auflage" while the display period the notice states runs,
  "Genehmigt, Rechtskraft ausstehend" while a stated in-force date or appeal
  period lies ahead, "In Kraft getreten" once a stated date or OEREBlex shows
  it — never the parcel's ÖREB extract, which cannot tell a revision from its
  predecessor of the same name. Otherwise the label says what is known —
  "Beschlossen", "Genehmigt, Inkrafttreten nicht belegt" — or that the current
  state is not shown: "Stand unbestätigt". The step is read from the title
  (the text only for a refusal); texts mention other acts, earlier and later
  ones. Nothing is hidden for its age.
* **scoped** — an area plan says which area it covers. When the parcel's own
  ÖREB extract lists a plan of that kind and name, the row says so, as a
  hint: the listing may be a predecessor of the same name.
* **zones** — a Planungszone ends on evidence only: its term, or the plan a
  notice links it to in force by a stated date (`_zone_verdict`). Another
  plan of the municipality is no evidence.

Where the publications come from is deliberately not decided here. The
Amtsblatt's terms reserve every use beyond the legally permitted cases to the
Staatskanzlei's written consent (see `amtsblatt.py`), so the store is a JSON
file named by `SCOPE_PLANNING_PUBLICATIONS`, unset by default — the panel then
links to the filtered Amtsblatt search instead — and `amtsblatt_import` fills
it from batches an operator writes.
Record format, one entry per publication as the Amtsblatt prints it:

    {"stand": "2026-10-05",              # optional, defaults to the file date
     "since": "2026-06-01",              # first publication date the store covers
     "municipalities": ["Seon"],         # optional; absent means all of them,
                                         # anything but a list is refused
     "records": [{
         "pub_nr": "00.102.603",
         "published_on": "2026-09-10",
         "authority": "Gemeinde Seon",   # "Stelle", as printed
         "rubric": "Gemeinden / Bau- und Nutzungsordnung",
         "title": "Öffentliche Mitwirkung zum Gestaltungsplan «Heidegrabe»",
         "text": "…",                     # as much of the notice as the channel gives
         "url": "https://amtsblatt.ag.ch/ekab/00.102.603/pdf/",
         "effective_on": "2027-01-01"     # optional
     }]}

`since` matters because "no procedure under way" is only true for the period
the store has looked at: a store fed by a daily subscription knows nothing
from before its first day. `python -m planning check <file>` refuses a store
without it, and one whose sources are not the publications themselves.
"""
import json
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import urlsplit

import amtsblatt as A

ENV = "SCOPE_PLANNING_PUBLICATIONS"

#: The inference path — publications chained into revisions, a status of
#: today derived from them, zone verdicts, the parcel's ÖREB extract matched
#: by name — is a switch, OFF unless this is "1"/"true"/"yes"/"on". Version 1
#: shows each publication as printed (`View.publications`).
ENV_INFERENCE = "SCOPE_PLANNING_INFERENCE"


def inference_enabled() -> bool:
    return os.environ.get(ENV_INFERENCE, "").strip().casefold() in ("1", "true", "yes", "on")

#: What the badge says. "Entwurf in Auflage", "Genehmigt, Rechtskraft
#: ausstehend" and "In Kraft getreten" are Philipp's wording; the others are
#: there because one of his three would otherwise be stated without evidence.
#: Version 1 shows his three only where a checked stage supports them on the
#: day shown (`stage_badge`), "In Kraft getreten" also on OEREBlex rows, and our
#: "Künftig in Kraft" for an OEREBlex edition dated ahead. Every other label
#: belongs to the inference path (OFF).
STATUS_LABELS = {
    "entwurf_auflage": "Entwurf in Auflage",
    "entwurf_mitwirkung": "Entwurf in Mitwirkung",
    "entwurf_anhoerung": "Entwurf in Anhörung",
    "planungszone": "Planungszone in Kraft",
    "planungszone_aufgehoben": "Planungszone aufgehoben",
    "planungszone_beendet": "Planungszone beendet",
    "beschlossen": "Beschlossen",
    "genehmigt": "Genehmigt, Rechtskraft ausstehend",
    "inkraft_festgelegt": "Inkrafttreten festgelegt",
    "genehmigt_unbelegt": "Genehmigt, Inkrafttreten nicht belegt",
    "in_kraft": "In Kraft getreten",
    "unbestaetigt": "Stand unbestätigt",
    # An OEREBlex edition with its date ahead: the date is all OEREBlex says.
    "kuenftig": "Künftig in Kraft",
}

#: Philipp's colours: a draft amber, an approval yellow, what is in force grey.
#: A planning zone is binding and announces a change — amber; one lifted, or
#: ended by its term or by the plan it secures, is settled — grey. A decision, and anything whose current state no source
#: shows, stay uncoloured.
STATUS_TONES = {
    "entwurf_auflage": "amber",
    "entwurf_mitwirkung": "amber",
    "entwurf_anhoerung": "amber",
    "planungszone": "amber",
    "planungszone_aufgehoben": "grey",
    "planungszone_beendet": "grey",
    "beschlossen": "neutral",
    "genehmigt": "yellow",
    "inkraft_festgelegt": "yellow",
    "genehmigt_unbelegt": "open",
    "in_kraft": "grey",
    "unbestaetigt": "open",
    "kuenftig": "yellow",
}

#: Which list of the view a status belongs to.
_BUCKET = {
    "in_kraft": "in_force",
    "planungszone": "ongoing",
    "genehmigt": "approved",
    "inkraft_festgelegt": "approved",
    "genehmigt_unbelegt": "approved",
    "beschlossen": "decided",
    "entwurf_auflage": "ongoing",
    "entwurf_mitwirkung": "ongoing",
    "entwurf_anhoerung": "ongoing",
    "planungszone_aufgehoben": "ended",
    "planungszone_beendet": "ended",
    "unbestaetigt": "unclear",
}

STEP_LABELS = {
    "mitwirkung": "Mitwirkung",
    "vorpruefung": "Vorprüfung",
    "auflage": "Öffentliche Auflage",
    "beschluss": "Beschluss",
    "genehmigung": "Genehmigung",
    "inkraft": "Inkraftsetzung",
    "planungszone": "Planungszone",
    "verweigert": "Nichtgenehmigung",
    "unbekannt": "Schritt nicht erkannt",
}

#: In procedural order: a title that names two steps ("öffentliche Auflage und
#: Mitwirkungsverfahren") stands at the later one. "Auflage" only as a word of
#: its own — "Mitwirkungsauflage" is a Mitwirkung, "mit Auflagen" a condition.
_STEPS = (
    ("mitwirkung", r"mitwirkung|anhörung|vernehmlassung"),
    ("vorpruefung", r"vorprüfung"),
    ("auflage", r"\b(?:plan)?auflage\b|\baufgelegt\b"),
    ("beschluss", r"\bbeschluss\b|\bbeschlossen\b|verabschiedet"),
    ("genehmigung", r"\bgenehmig"),
    ("inkraft", r"inkraftsetzung|inkrafttreten|in kraft gesetzt"),
)
_RANK = {step: i for i, (step, _) in enumerate(_STEPS)}
_RANK["planungszone"] = len(_STEPS)
_RANK["unbekannt"] = -1
#: Where an approval would stand: the plan's procedure ends there for now.
_RANK["verweigert"] = _RANK["genehmigung"]

#: The stage each step stands for without further evidence. An approval is
#: "genehmigt" until a date or a source shows the plan in force — Philipp,
#: 2026-10-05: do not map Genehmigt to In Kraft. A notice of the Inkraftsetzung
#: is that evidence only with its date (`_stage`). A Planungszone restricts
#: building from its public display (§ 29 Abs. 2 BauG).
_STAGE_OF_STEP = {
    "mitwirkung": "verfahren",
    "vorpruefung": "verfahren",
    "auflage": "verfahren",
    "beschluss": "beschlossen",
    "genehmigung": "genehmigt",
    "inkraft": "in_kraft",
    "planungszone": "in_kraft",
    "verweigert": "unklar",
    "unbekannt": "unklar",
}

#: "Genehmigung durch die Gemeindeversammlung" is the municipality deciding —
#: the canton's approval is still to come.
_MUNICIPAL_APPROVAL = re.compile(
    r"genehmig\w*[^;]{0,60}?(?:gemeindeversammlung|einwohnerrat|gemeinderat)"
    r"|(?:gemeindeversammlung|einwohnerrat|gemeinderat)[^;]{0,80}?genehmig\w*")
#: Who approves for the canton. A municipality's own notice that says
#: "genehmigt" without naming one of them reports its own decision.
_CANTON_APPROVER = re.compile(r"regierungsrat|departement|\bbvu\b|abteilung raumentwicklung|kanton")
_REFUSED = re.compile(
    r"nichtgenehmig|nicht\s+genehmigt|genehmigung\s+(?:wird\s+|wurde\s+)?verweigert"
    r"|verweigert\s+die\s+genehmigung")

#: (instrument, scope, pattern), first match wins. Scope is what the panel
#: needs: does the change cover a named area, part of the plan, or the whole
#: municipality.
_INSTRUMENTS = (
    ("Planungszone", "gebiet", r"planungszone"),
    ("Gestaltungsplan", "gebiet", r"gestaltungsplan"),
    ("Erschliessungsplan", "gebiet", r"erschliessungsplan|erschließungsplan"),
    ("Überbauungs-/Baulinienplan", "gebiet", r"überbauungspl|baulinienplan"),
    ("Beitragsplan", "gebiet", r"beitragspl"),
    ("Sondernutzungsplan", "gebiet", r"sondernutzungsplan"),
    ("Strassenklassifizierungsplan", "gemeinde", r"strassenklassifizierung"),
    ("Nutzungsplanung, Gesamtrevision", "gemeinde", r"gesamtrevision"),
    ("Nutzungsplanung, Teilrevision", "teil", r"teilrevision|teiländerung|teilzonenplan"),
    ("Nutzungsplanung", "gemeinde", r"nutzungsplan|nutzungsordnung|\bbno\b|zonenplan|kulturlandplan"),
)

#: An amendment, revision, extension or repeal of a plan is not the plan.
_AMEND = re.compile(r"aufhebung|teiländerung|teilrevision|änderung|anpassung|ergänzung"
                    r"|erweiterung|neuerlass|revision")
#: A quoted name in a notice's text names a plan only when it says so — not
#: every «…» is one ("gemäss dem «Räumlichen Entwicklungsleitbild»").
_PLAN_WORD = re.compile(r"plan|pläne|zone|ordnung")

#: A municipal plan or a zone named in a title. A canton notice about one is
#: about some municipalities' plans or a designated area (§ 29 Abs. 1 BauG),
#: whatever law its title cites ("Genehmigung von Nutzungsplänen gemäss § 27
#: BauG") — never a change for every parcel in Aargau.
_MUNICIPAL_PLAN = re.compile(
    r"nutzungspl|nutzungsordnung|\bbno\b|zonenplan|kulturlandplan|gestaltungspl"
    r"|erschliessungspl|erschließungspl|überbauungspl|baulinienpl|beitragspl"
    r"|strassenklassifizierung|planungszone")

#: Canton-wide instruments, read from the title only: a text citing "§ 127
#: BauG" or naming the "Abteilung Raumentwicklung" is not about the Baugesetz.
_CANTON_WIDE = re.compile(r"baugesetz|\bbaug\b|bauverordnung|\bbauv\b|richtplan"
                          r"|gesetz über raumentwicklung")
#: What makes an unattributable canton notice worth reporting rather than
#: ignoring: a consultation on the health law is not this panel's business.
_PLANNING = re.compile(
    r"bau|raum|richtplan|nutzung|zone|planung|siedlung|verdicht|mehrwert|"
    r"erschliess|gewässer|wald|denkmal|ortsbild|lärm"
)

#: "Gemeinde Seon", "Stadt Baden, Bau", "Gemeinde Seon - Bauverwaltung",
#: "Gemeinde Beinwil (Freiamt)".
_OFFICE = re.compile(r"^(?:Einwohnergemeinde|Gemeinde|Stadt)\s+([^,]+?)(?:\s+[-–]\s+.*|,.*)?$")
#: Where a canton notice moves on to another municipality: the " | " the
#: Amtsblatt prints between paragraphs, and any "Gemeinde(n) X",
#: "Einwohnergemeinde X", "Ortsbürgergemeinde X" or "Stadt X" — including one
#: in the middle of a sentence.
_BREAK = re.compile(r"\s*\|\s*|(?=\b(?:Einwohner|Ortsbürger)?[Gg]emeinden?\s+[A-ZÄÖÜ])"
                    r"|(?=\bStadt\s+[A-ZÄÖÜ])")
#: The one form that attributes a part to one municipality: "Gemeinde Seon: …"
#: or "Gemeinde Seon; …". "Gemeinden Reinach und Menziken: …" does not.
_PART_HEAD = re.compile(r"^(?:Einwohnergemeinde|Gemeinde|Stadt)\s+([A-ZÄÖÜ][^:;|]*?)\s*[:;]\s*")
_QUOTED = re.compile(r'«([^»]+)»|"([^"]+)"|„([^“”]+)[“”]|“([^”]+)”')
_LEADING_STEP = re.compile(
    r"^(?:öffentliche\s+)?(?:planauflage|auflage|mitwirkung(?:sverfahren)?|anhörung|"
    r"genehmigung|beschluss|erlass(?:\s+(?:einer|eines|der|des))?)\s+"
    r"(?:zum|zur|der|des|von|für|über|betreffend)?\s*",
    re.IGNORECASE,
)
_STEP_PART = re.compile(
    r"^(?:öffentliche\s+)?(?:planauflage|auflage|mitwirkung\w*|anhörung|beschluss|"
    r"genehmigung|vorprüfung|inkraftsetzung|inkrafttreten|entwurf\s+zur\s+mitwirkung)\b",
    re.IGNORECASE,
)
#: A new sentence right after a plan's quoted name — 'Erschliessungsplan
#: "Milchgasse" Gegen diesen Entscheid …' — is not part of the plan's name.
_SENTENCE_AFTER_NAME = re.compile(
    r'(?<=[»"“”])\s+(?=(?:Gegen|Die|Der|Das|Diese[rsn]?|Wer|Während|Allfällige|'
    r'Einwendungen|Beschwerden?|Mit)\b)')
#: Words a full stop may follow inside a title without ending it.
_ABBREVIATIONS = {"inkl", "bzw", "gem", "nr", "abs", "art", "ca", "resp", "evtl", "lit",
                  "ziff", "str", "st", "ggf", "usw", "z", "u", "d", "h"}
_TITLE_LENGTH = 140

_MONTHS = {
    "januar": 1, "jan": 1, "februar": 2, "feb": 2, "märz": 3, "mär": 3, "maerz": 3,
    "april": 4, "apr": 4, "mai": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9, "oktober": 10, "okt": 10,
    "november": 11, "nov": 11, "dezember": 12, "dez": 12,
}


def _day_pattern(tag) -> str:
    """'1. Januar 2027', '1. Jan. 2027', '01.01.2027', '01.01.27' — as named
    groups `<tag>d`, `<tag>m`/`<tag>w`, `<tag>y`."""
    return (rf"(?P<{tag}d>\d{{1,2}})\.\s*(?:(?P<{tag}m>\d{{1,2}})\.|(?P<{tag}w>[a-zä]+)\.?)"
            rf"\s*(?P<{tag}y>\d{{4}}|\d{{2}})(?!\d)")


_D = _day_pattern("x")
#: An in-force date, forward-looking only: "tritt am … in Kraft", "wird auf
#: den … in Kraft gesetzt", "in Kraft ab …", "Inkrafttreten per …". Not "in
#: Kraft seit …", "seit dem Inkrafttreten am …", "(Inkrafttreten: …)" nor
#: "bleibt … in Kraft": those are about something else.
_EFFECTIVE = (
    re.compile(r"(?:tritt|treten)\s+(?:am|per|auf|mit\s+wirkung\s+(?:ab|auf))\s+(?:den\s+)?"
               + _D + r"\s+in\s+kraft"),
    re.compile(r"(?:wird|werden)\s+(?:[\wäöü-]+\s+){0,4}?(?:auf|per|am|ab|mit\s+wirkung\s+"
               r"(?:ab|auf))\s+(?:den\s+)?" + _D + r"\s+in\s+kraft\s+gesetzt"),
    re.compile(r"\bin\s+kraft\s+(?:ab|per|auf\s+den)\s+" + _D),
    re.compile(r"(?:inkrafttreten|inkraftsetzung)\s+(?:per|am|auf\s+den|ab)\s+" + _D),
)
#: Words before "Inkrafttreten am …" that make it a date looked back on:
#: "seit dem / seinem / dessen Inkrafttreten", "nach Inkrafttreten".
_LOOKING_BACK = re.compile(r"\b(?:seit|nach|vor|bis|während|seinem|seinen|dessen|ihrem|deren)\b")
#: A display or Mitwirkung period, start and end both stated: "vom … bis
#: (zum / und mit) …", "21.09.2026 – 20.10.2026". A lone "bis …" is a deadline
#: for something — not necessarily the display.
_RANGES = (
    re.compile(r"\bvom\s+" + _day_pattern("a") + r"\s+bis\s+(?:und\s+mit\s+|zum\s+|am\s+)?"
               + _day_pattern("b")),
    re.compile(_day_pattern("a") + r"\s*(?:-|bis)\s*(?:zum\s+)?" + _day_pattern("b")),
)
#: "innert 30 Tagen seit der Publikation Beschwerde …": an approval is not
#: legally binding before its appeal period has run. A deadline that is not
#: an appeal's ("innert 30 Tagen einzureichen") is not one.
_APPEAL = re.compile(r"innert\s+(\d{1,3})\s+tagen[^.]{0,160}?beschwerde"
                     r"|beschwerde[^.]{0,160}?innert\s+(\d{1,3})\s+tagen")

#: An approval is published within weeks of the day it puts a plan in force;
#: an in-force date further back belongs to some other act.
EFFECTIVE_LAG_DAYS = 90

#: Two notices of one approval (the canton's, the municipality's) appear
#: within weeks; an approval months after the last one is a new procedure.
SAME_ACT_DAYS = 120

#: A decision is approved, and an approval put in force, within this; a step
#: further apart is another procedure of the same name.
NEW_PROCEDURE_DAYS = 2 * 365

#: When a zone's notice gives no display date, its display may begin this
#: long after publication: the end of its five years is then no known day.
DISPLAY_SLACK_DAYS = 60

#: How long a Planungszone lasts — `_zone_verdict`. § 29 Abs. 2 BauG (checked
#: 2026-10-05 in the canton's collection, SAR 713.100): "Planungszonen werden
#: mit der öffentlichen Auflage wirksam und gelten bis zum Inkrafttreten der
#: Nutzungspläne, deren Zweck sie sichern, längstens 5 Jahre."
PLANUNGSZONE_YEARS = 5


#: What a text can carry inside a word without showing it — a soft hyphen,
#: zero-width joiners, a word joiner, a byte-order mark. A zero-width space
#: is different: it separates two words.
_INVISIBLE = re.compile("[\u00ad\u200c\u200d\u2060\ufeff]")


def _normal(text) -> str:
    """One code point per letter and nothing invisible, whatever the source
    stored: "ö" as "o" + "¨" misses "Böttstein" in the directory and prints
    as "o■" on paper."""
    text = unicodedata.normalize("NFC", str(text or "")).replace("\u200b", " ")
    return _INVISIBLE.sub("", text)


def _fold(text) -> str:
    text = re.sub(r"[‐‑‒–—]", "-", _normal(text))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _clean(text) -> str:
    return re.sub(r"\s+", " ", _normal(text)).strip()


#: The years a publication, or a date in it, can plausibly carry.
YEARS = range(1900, 2101)


def _date(value) -> Optional[date]:
    try:
        day = date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None
    return day if day is None or day.year in YEARS else None


def _day(match, tag) -> Optional[date]:
    day, number, word, year = (match.group(f"{tag}{k}") for k in "dmwy")
    month = int(number) if number else _MONTHS.get(word or "")
    year = int(year) + (2000 if len(year) == 2 else 0)
    if year not in YEARS:
        return None
    try:
        return date(year, month, int(day)) if month else None
    except ValueError:
        return None


def _effectives(text) -> list:
    """Every in-force date the text announces, in reading order."""
    folded = _fold(text)
    found = []
    for index, pattern in enumerate(_EFFECTIVE):
        for match in pattern.finditer(folded):
            if index == 3 and _LOOKING_BACK.search(folded[max(0, match.start() - 30):match.start()]):
                continue
            day = _day(match, "x")
            if day:
                found.append((match.start(), day))
    return [day for _, day in sorted(found)]


#: A Planungszone's own term, when its notice states one ("gilt längstens bis
#: 31. Dezember 2024", "gilt vom … bis …"): a date after "bis" shortly after
#: "gilt", "befristet" or "längstens" — not a display period ("liegt vom …
#: bis … auf") and not a deadline for objections.
_TERM_WORDS = r"\bgilt\b|\bgelten\b|befristet|längstens"
_TERM_NOT = re.compile(r"liegt|liegen|aufgelegt|auflage|einwend|einsprach|eingab|mitwirk"
                       r"|einseh|eingeseh|einsicht|beschwerde|einzureich|einreich")
_TERM_DAY = re.compile(r"\bbis\s+(?:zum\s+|und\s+mit\s+|am\s+)?" + _day_pattern("t"))


def _term_end(text) -> Optional[date]:
    """The term a zone's notice gives it, read within one sentence."""
    found = None
    for sentence in _sentences(str(text or "")):
        folded = _fold(sentence)
        if re.search(_TERM_WORDS, folded) and not _TERM_NOT.search(folded):
            for match in _TERM_DAY.finditer(folded):
                found = _day(match, "t") or found
    return found


#: When the act a notice reports was decided — not when the notice was
#: published: "Der Gemeinderat hat am 2. September 2024 … beschlossen",
#: "hat an seiner Sitzung vom …", "Gemeindeversammlung vom 12.06.2026".
#: The deciding body, between "hat" and its date: "hat der Regierungsrat am …".
#: A date further on belongs to the sentence's object ("hat die von der
#: Gemeindeversammlung am … beschlossene Teiländerung genehmigt").
_AUTHORITY = (r"(?:gemeinderat|stadtrat|regierungsrat|einwohnerrat|gemeindeversammlung"
              r"|grosse\s+rat|departement[\w\s,]{0,40}?|abteilung[\w\s]{0,30}?)")
_DECIDED_AT = re.compile(r"\bhat\s+(?:(?:der|die|das)\s+" + _AUTHORITY + r"\s+)?"
                         r"(?:am|an\s+(?:seiner|ihrer)\s+sitzung\s+vom)\s+" + _day_pattern("e"))
_DECIDED_VERBS = {
    "beschluss": r"beschlossen|verabschiedet|erlassen|festgesetzt|zugestimmt|genehmigt",
    "genehmigung": r"genehmigt",
    "verweigert": r"verweigert|nicht\s+genehmigt",
}
_DECIDED_BY = re.compile(r"\b(?:gemeindeversammlung|einwohnerrat\w*|beschluss|entscheid"
                         r"|genehmigungsentscheid)\s+vom\s+" + _day_pattern("e"))


def _split_part(pub) -> bool:
    """A canton notice's part naming one municipality, split off the rest."""
    return pub.level == "kanton" and bool(pub.municipality)


def publication_title(pub) -> str:
    """Version 1's title for a publication: the notice's own — for a canton
    notice split per municipality, the part naming this one, as printed and
    whole: "Gestaltungsplan «Giessi»" alone, under "Genehmigung von
    Sondernutzungsplänen", would hide a refusal in the part's next words."""
    if not _split_part(pub):
        return pub.title
    return _clean(pub.text) or pub.title


def record_key(record) -> str:
    """What makes a stored row unique: its publication number — and, for a
    canton notice the importer split per Gemeinde, that Gemeinde, and the
    part's number when the notice names the Gemeinde more than once."""
    name = _clean(record.get("municipality"))
    where = A.canonical_name(name) if name else ("kantonsweit" if record.get("canton_wide") is True else "")
    if where and record.get("part_nr") not in (None, ""):
        where += f" #{record.get('part_nr')}"
    number = _clean(record.get("pub_nr"))
    return f"{number} ({where})" if where else number


def _part_nr(value):
    """A part's number: a whole number from 1, as int or digits — else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value) if int(value) >= 1 else None
    return None


def stage_badge(pub, today) -> tuple:
    """(Philipp's status key, note) for a publication on `today`. The key only
    while the checked source supports it on that day — a display period that
    runs, an approval whose legal force is shown to be pending, an entry into
    force that has happened. Otherwise None, and what is known in the note."""
    proof = getattr(pub, "verified", None)
    if proof is None:
        return None, ""
    checked = f"geprüft am {proof.verified_on:%d.%m.%Y}: {proof.method}"
    if proof.verified_on > today:
        return None, f"Prüfung datiert nach dem {today:%d.%m.%Y} — für diesen Tag kein Beleg"
    if proof.stage == "auflage":
        period = f"Öffentliche Auflage {proof.auflage_from:%d.%m.%Y}–{proof.auflage_to:%d.%m.%Y}"
        if proof.auflage_from <= today <= proof.auflage_to:
            return "entwurf_auflage", f"{period} · {checked}"
        if today > proof.auflage_to:
            return None, f"{period} beendet — heutiger Stand nicht geprüft · {checked}"
        return None, f"{period} angekündigt · {checked}"
    if proof.stage == "genehmigt":
        approved = f"Genehmigt am {proof.approved_on:%d.%m.%Y}"
        if proof.rechtskraft_on and today >= proof.rechtskraft_on:
            if proof.rechtskraft_on > proof.verified_on:
                # Stated ahead of the check: an announcement, not a fact anyone saw.
                return None, (f"{approved}, Rechtskraft auf {proof.rechtskraft_on:%d.%m.%Y} angekündigt — "
                              f"nicht geprüft · {checked}")
            return None, (f"{approved}, rechtskräftig seit {proof.rechtskraft_on:%d.%m.%Y} — "
                          f"Inkrafttreten nicht geprüft · {checked}")
        # Pending only on a date the source gives: legal force cannot come
        # before the appeal period ends, or before a stated date of legal
        # force. Without either, pending is shown on no day.
        pending_until = (proof.rechtskraft_on - timedelta(days=1) if proof.rechtskraft_on
                         else proof.appeal_until)
        if pending_until and proof.approved_on <= today <= pending_until:
            appeal = (f"Beschwerdefrist bis {proof.appeal_until:%d.%m.%Y}" if proof.appeal_until else "")
            return "genehmigt", " · ".join(filter(None, [approved, appeal, checked]))
        if not pending_until:
            return None, f"{approved} — Rechtskraft nicht belegt · {checked}"
        return None, f"{approved} — Rechtskraft nach dem {pending_until:%d.%m.%Y} nicht geprüft · {checked}"
    if proof.stage == "in_kraft":
        return "in_kraft", f"In Kraft seit {proof.effective_on:%d.%m.%Y} · {checked}"
    return None, f"Genehmigung verweigert am {proof.decided_on:%d.%m.%Y} · {checked}"


def _decided(text, step, published_on) -> Optional[date]:
    """When the act a notice reports was decided: the date in "hat … am …"
    followed by this step's verb ("genehmigt" for an approval), else one in
    "Gemeindeversammlung vom …". Never after the notice, and the latest such
    date: an earlier one belongs to an act the notice recalls."""
    verbs = _DECIDED_VERBS.get(step)
    if not verbs:
        return None
    reported, named = [], []
    for sentence in _sentences(str(text or "")):
        folded = _fold(sentence)
        reported += [_day(m, "e") for m in _DECIDED_AT.finditer(folded)
                     if re.search(verbs, folded[m.end():])]
        named += [_day(m, "e") for m in _DECIDED_BY.finditer(folded)]
    for found in (reported, named):
        found = [day for day in found if day and day <= published_on]
        if found:
            return max(found)
    return None


#: Words that say a sentence is about this step, and words that say it is
#: about the other one. Documents "liegen auf" for a Mitwirkung as for an
#: Auflage, so those words belong to both.
_STEP_OWN = {"auflage": r"auflage|aufgelegt|liegen|liegt",
             "mitwirkung": r"mitwirkung|anhörung|liegen|liegt|aufgelegt",
             "planungszone": r"auflage|aufgelegt|liegen|liegt"}
_STEP_OTHER = {"auflage": r"mitwirkung|anhörung", "mitwirkung": r"\bauflage\b",
               "planungszone": _TERM_WORDS}


def _period(text, step=""):
    """(start, end) of the one period a notice states for its step, or (None,
    None) when it states none — or more than one, and it cannot be told which
    is the display's. A range in a sentence about another step ("Die
    Mitwirkung fand vom … bis … statt" in an Auflage) is that step's."""
    folded = _fold(text)
    own, other = _STEP_OWN.get(step), _STEP_OTHER.get(step)
    found = set()
    for pattern in _RANGES:
        for match in pattern.finditer(folded):
            sentence_start = folded.rfind(". ", 0, match.start()) + 1
            sentence_end = folded.find(". ", match.end())
            sentence = folded[sentence_start:sentence_end if sentence_end >= 0 else None]
            if other and re.search(other, sentence) and not re.search(own, sentence):
                continue
            start, end = _day(match, "a"), _day(match, "b")
            if start and end and start <= end:
                found.add((start, end))
    return found.pop() if len(found) == 1 else (None, None)


def _appeal_days(text) -> Optional[int]:
    match = _APPEAL.search(_fold(text))
    return int(match.group(1) or match.group(2)) if match else None


def _steps_in(text) -> list:
    return [step for step, pattern in _STEPS if re.search(pattern, text)]


def step_of(title, text="", level="kanton") -> str:
    """The procedural step a publication announces — read from its title.

    The title names the step the notice is for, at the furthest step it
    names. The text is not read for the step: it mentions other acts, earlier
    ones ("wurde 2010 genehmigt"), later ones ("zur Genehmigung unterbreitet")
    and other ones ("hat beschlossen, den Plan aufzulegen"). It is read for a
    refusal, which no title-word can outweigh. A municipality's own notice that
    reports an approval without naming the canton reports its own decision.
    """
    folded = _fold(title)
    body = _fold(text)
    if "planungszone" in folded:
        return "planungszone"
    if _REFUSED.search(folded) or _REFUSED.search(body):
        return "verweigert"
    folded = _MUNICIPAL_APPROVAL.sub(" beschluss ", folded)
    folded = re.sub(r"mitwirkungsauflage", "mitwirkung", folded)
    folded = re.sub(r"zur\s+mitwirkung\s+(?:öffentlich\s+)?aufgelegt", "zur mitwirkung", folded)
    found = _steps_in(folded)
    if not found:
        return "unbekannt"
    step = max(found, key=_RANK.get)
    if (step == "genehmigung" and level == "gemeinde"
            and not _CANTON_APPROVER.search(f"{folded} {body}")):
        return "beschluss"
    return step


def _quoted(text) -> list:
    return [_clean(next(g for g in m.groups() if g)) for m in _QUOTED.finditer(str(text or ""))]


def _sentences(text) -> list:
    """The sentences of a text; "1. Januar" and "Nr. 5" do not end one, a
    year does ("… ab 21. September 2026. Die Unterlagen …")."""
    out, start = [], 0
    for match in re.finditer(r"\.\s+(?=[A-ZÄÖÜ])", text):
        word = re.search(r"([\wäöüÄÖÜ]+)$", text[start:match.start()])
        if word and (word.group(1).casefold() in _ABBREVIATIONS
                     or (word.group(1).isdigit() and len(word.group(1)) <= 2)):
            continue
        out.append(text[start:match.start() + 1])
        start = match.end()
    return [s for s in out + [text[start:]] if s.strip()]


def _first_sentence(text) -> str:
    for match in re.finditer(r"\.\s+(?=[A-ZÄÖÜ])", text):
        before = text[:match.start()]
        word = re.search(r"([\wäöüÄÖÜ]+)$", before)
        if word and (word.group(1).casefold() in _ABBREVIATIONS or word.group(1).isdigit()):
            continue
        return before
    return text


def _subject(text, steps=False) -> str:
    """What a publication is about, every part of it, with the step words
    taken off: 'Nutzungsplanung; Teiländerung Zentrum; Beschluss' →
    'Nutzungsplanung, Teiländerung Zentrum' — or left on, with `steps`. It
    ends at the first sentence: a notice's body does not belong in a title."""
    text = _SENTENCE_AFTER_NAME.split(_clean(text).strip(" |"), 1)[0]
    text = _first_sentence(text)
    parts = [part.strip(" |") for part in text.split(";") if part.strip(" |")]
    kept = parts if steps else [part for part in parts if not _STEP_PART.match(part)] or parts[:1]
    # The search list cuts notices off mid-word ("Erschliessungsp […]"): a
    # title made from such a part says it is cut.
    cut = bool(kept) and bool(re.search(r"(?:\[…\]|…)\s*$", kept[-1]))
    kept = [_clean(part.replace("[…]", "").replace("…", "")) for part in kept]
    subject = ", ".join(s for s in ((p if steps else _LEADING_STEP.sub("", p)).strip(" .,")
                                    for p in kept) if s)
    subject = _QUOTED.sub(lambda m: f"«{_clean(next(g for g in m.groups() if g))}»", subject)
    if len(subject) > _TITLE_LENGTH:
        subject, cut = subject[:_TITLE_LENGTH].rsplit(" ", 1)[0], True
    return subject + "…" if cut and subject else subject


def _instrument(*texts):
    for text in texts:
        folded = _fold(text)
        for name, scope, pattern in _INSTRUMENTS:
            if re.search(pattern, folded):
                return name, scope
    return "Planung", "unklar"


def _area(subject, scope) -> str:
    if scope == "gebiet":
        names = _quoted(subject)
        if names:
            return " / ".join(names)
        match = re.search(r"(?:plan|pläne|zone)\s+(.+)$", subject, re.IGNORECASE)
        return match.group(1).strip(" .,…") if match else ""
    if scope == "teil":
        match = re.search(r"(?:teilrevision|teiländerung|teilzonenplanrevision)\s+(.+)$",
                          subject, re.IGNORECASE)
        return match.group(1).strip(" .,…") if match else ""
    return ""


#: Every municipality this tool knows, by comparison key, to find the ones a
#: notice names in passing.
_KNOWN = {A.municipality_key(name): name for name in A.AUTHORITIES}
_MENTION = re.compile(r"(?<![\w-])(" + "|".join(
    sorted((re.escape(key) for key in _KNOWN), key=len, reverse=True)) + r")(?![\w-])")


def _mentions(text) -> list:
    """The known municipalities a text names, in the order it names them."""
    names = []
    for match in _MENTION.finditer(_fold(text)):
        name = _KNOWN[match.group(1)]
        if name not in names:
            names.append(name)
    return names


class Problem(str):
    """A line for the report that also knows which municipalities it names, so
    a parcel there can say "a publication mentions us and could not be
    attributed" instead of "nothing under way"."""

    def __new__(cls, text, mentions=()):
        problem = super().__new__(cls, text)
        problem.mentions = tuple(mentions)
        return problem


#: What a person can check a publication's stage against: Philipp's three,
#: and a refusal, which a row says and which earns no badge.
STAGES = ("auflage", "genehmigt", "in_kraft", "verweigert")
#: The dates a check may carry, each its own thing: the display period, the
#: decision or refusal, the canton's approval, the end of the appeal period the
#: source states, legal force, entry into force.
_STAGE_DATES = ("auflage_from", "auflage_to", "decided_on", "approved_on", "appeal_until",
                "rechtskraft_on", "effective_on")
#: Which of them each stage cannot do without.
_STAGE_NEEDS = {"auflage": ("auflage_from", "auflage_to"), "genehmigt": ("approved_on",),
                "in_kraft": ("effective_on",), "verweigert": ("decided_on",)}


@dataclass(frozen=True)
class Verified:
    """A stage a person checked against a source, for one publication and one
    Gemeinde — never one read from a title. `source` is the document that
    shows it; `verified_on` says when, `method` how it was checked."""

    stage: str
    source: str
    verified_on: date
    method: str
    auflage_from: Optional[date] = None
    auflage_to: Optional[date] = None
    decided_on: Optional[date] = None
    approved_on: Optional[date] = None
    appeal_until: Optional[date] = None
    rechtskraft_on: Optional[date] = None
    effective_on: Optional[date] = None


def _verified(raw):
    """(Verified, "") — or (None, what is wrong with the check)."""
    if not isinstance(raw, dict):
        return None, "kein Objekt"
    stage = str(raw.get("stage") or "").strip()
    if stage not in STAGES:
        return None, f"stage «{stage}» unbekannt (erlaubt: {', '.join(STAGES)})"
    source = str(raw.get("source") or "").strip()
    if not web_link(source):
        return None, "source ist kein Web-Link"
    method = _clean(raw.get("method"))
    if not method:
        return None, "method fehlt"
    checked = _date(raw.get("verified_on"))
    if not checked:
        return None, "verified_on fehlt oder ist ungültig"
    dates = {}
    for name in _STAGE_DATES:
        if raw.get(name) in (None, ""):
            continue
        dates[name] = _date(raw.get(name))
        if not dates[name]:
            return None, f"{name} ist ungültig"
    missing = [name for name in _STAGE_NEEDS[stage] if name not in dates]
    if missing:
        return None, f"{', '.join(missing)} fehlt für «{stage}»"
    if stage == "auflage" and dates["auflage_from"] > dates["auflage_to"]:
        return None, "auflage_from liegt nach auflage_to"
    # What has happened can be checked only once it has: an approval, a
    # decision, and — for "in force" — the entry into force itself.
    for name in ("approved_on", "decided_on") + (("effective_on",) if stage == "in_kraft" else ()):
        if name in dates and dates[name] > checked:
            return None, f"{name} liegt nach verified_on"
    for name in ("appeal_until", "rechtskraft_on"):
        if name in dates and "approved_on" in dates and dates[name] < dates["approved_on"]:
            return None, f"{name} liegt vor approved_on"
    return Verified(stage, source, checked, method, **dates), ""


@dataclass(frozen=True)
class Notice:
    """One Amtsblatt publication as the store holds it."""

    pub_nr: str
    published_on: date
    authority: str
    rubric: str
    title: str
    text: str
    url: str
    effective_on: Optional[date]
    #: Set by the importer: the Gemeinde this row concerns, or that it applies
    #: canton-wide — the record's word, no text read for it.
    municipality: str = ""
    canton_wide: bool = False
    #: For a canton notice naming several Gemeinden: this one's part, as printed
    #: — numbered when the notice names this Gemeinde more than once.
    part: str = ""
    part_nr: Optional[int] = None
    verified: Optional[Verified] = None
    #: Why a check on this row was not taken — reported, the row still shown.
    verification_problem: Optional[str] = None


@dataclass(frozen=True)
class Publication:
    """The part of one notice that concerns one municipality — or the canton
    as a whole, when `municipality` is empty."""

    municipality: str
    level: str               #: "gemeinde" (the municipality's own notice) or "kanton"
    subject: str
    title: str
    text: str
    published_on: date
    url: str
    pub_nr: str
    step: str
    effective_on: Optional[date]
    #: The display or Mitwirkung period the notice states, both ends.
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    #: Days the notice gives for an appeal against it.
    appeal_days: Optional[int] = None
    #: A Planungszone's term, when the notice states one.
    term_end: Optional[date] = None
    #: For a decision, approval or refusal: the date of the act, when the
    #: notice gives it. Never the publication date in disguise.
    decided_on: Optional[date] = None
    #: A stage a person checked for this publication and this Gemeinde.
    verified: Optional[Verified] = None

    @property
    def key(self) -> str:
        return A.municipality_key(self.municipality)


def _office(authority) -> str:
    match = _OFFICE.match(authority)
    return match.group(1).strip() if match else ""


#: Characters an address never carries unencoded: a quote would end a link's
#: attribute and start another, a space would end the address.
_NOT_IN_URL = re.compile(r'[\s"<>\\^`{|}]')


def web_link(url) -> bool:
    """An http(s) address with a host that the URL parser accepts, written as
    a browser would take it — "https://[invalid/" starts like one and is none."""
    if not isinstance(url, str) or _NOT_IN_URL.search(url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https") and bool(parts.hostname)


def _notice(record):
    if not isinstance(record, dict):
        return None, Problem("Eintrag ist kein Objekt")
    label = _clean(record.get("pub_nr") or record.get("title") or "?")[:60]
    published = _date(record.get("published_on"))
    title = _clean(record.get("title"))
    authority = _clean(record.get("authority"))
    url = str(record.get("url") or "").strip()
    # A record that cannot be read still says where it belongs, so that
    # municipality's parcels do not read "nothing under way".
    named = _mentions(f"{authority} {title}")
    if not published:
        return None, Problem(f"{label}: Publikationsdatum fehlt oder ist ungültig", named)
    if not title:
        return None, Problem(f"{label}: Titel fehlt", named)
    if not authority:
        return None, Problem(f"{label}: publizierende Stelle fehlt", named)
    if not web_link(url):
        return None, Problem(f"{label}: Quelle fehlt oder ist kein Web-Link", named)
    municipality = _clean(record.get("municipality"))
    canton_wide = record.get("canton_wide") is True
    part_nr = None
    if record.get("part_nr") not in (None, ""):
        part_nr = _part_nr(record.get("part_nr"))
        if part_nr is None:
            return None, Problem(f"{label}: part_nr «{record.get('part_nr')}» ist keine Zahl ab 1",
                                 [municipality] if municipality else named)
    verified, why = None, ""
    if record.get("verified") is not None:
        if not (municipality or canton_wide):
            # A canton notice split by its text names several Gemeinden: a check
            # on it would be lent to every one of them.
            why = "nur für eine Zeile mit Gemeinde (municipality) oder kantonsweit (canton_wide)"
        else:
            verified, why = _verified(record.get("verified"))
    return Notice(
        pub_nr=_clean(record.get("pub_nr")),
        published_on=published,
        authority=authority,
        rubric=_clean(record.get("rubric")),
        title=title,
        text=_clean(record.get("text")),
        url=url,
        effective_on=_date(record.get("effective_on")),
        municipality=municipality,
        canton_wide=canton_wide,
        part=_clean(record.get("part")),
        part_nr=part_nr,
        verified=verified,
        verification_problem=Problem(f"{label}: geprüfter Schritt nicht übernommen — {why}") if why else None,
    ), None


def _publication(notice, municipality, level, subject, text, context="", decision=""):
    """`context` is what a canton notice says for its municipalities at once —
    "Gegen diesen Genehmigungsentscheid kann innert 30 Tagen …" — read where
    the municipality's own part says nothing; `decision`, the sentence that
    dates the one act approving them all ("hat am 13. Januar 2025 nachstehende
    Sondernutzungspläne genehmigt")."""
    step = step_of(notice.title, text, level)
    decided = (_decided(text, step, notice.published_on)
               or _decided(decision, step, notice.published_on))
    # An in-force date before the act that would set it is another act's;
    # the first credible one is this act's.
    earliest = decided or notice.published_on - timedelta(days=EFFECTIVE_LAG_DAYS)
    effective = notice.effective_on or next(
        (day for day in _effectives(text) + _effectives(context) if day >= earliest), None)
    start, end = _period(text, step)
    if not end and context:
        start, end = _period(context, step)
    return Publication(
        municipality=municipality,
        level=level,
        subject=_subject(subject),
        title=notice.title,
        text=text,
        published_on=notice.published_on,
        url=notice.url,
        pub_nr=notice.pub_nr,
        step=step,
        effective_on=effective,
        period_start=start,
        period_end=end,
        appeal_days=_appeal_days(text) or _appeal_days(context),
        term_end=(_term_end(text) or _term_end(context)) if step == "planungszone" else None,
        decided_on=decided,
        verified=notice.verified,
    )


def _parts(text):
    """(part, cut mid-sentence) for each stretch of a canton notice between
    two municipality markers or paragraph breaks."""
    out, start = [], 0
    for match in _BREAK.finditer(text):
        if match.start() == start and match.end() == start:
            continue
        out.append((text[start:match.start()], match.end() == match.start()))
        start = match.end()
    out.append((text[start:], False))
    return [(part.strip(), cut) for part, cut in out if part.strip()]


def _trim(body) -> str:
    """A part cut off mid-sentence by the next municipality's name ends at its
    last comma or full stop: '… "Friedstrasse", im Anschluss an den Plan der'."""
    cut = max(body.rfind(","), body.rfind(";"), body.rfind("."))
    return body[:cut] if cut > 0 else body


def _attribute_explicit(notice):
    """A row that names its Gemeinde, or says it applies canton-wide: taken at
    its word, checked against the directory and its publishing office — its
    text is not read for it."""
    label = notice.pub_nr or notice.title[:60]
    name = notice.municipality
    if name and notice.canton_wide:
        return [], Problem(f"{label}: zugleich Gemeinde «{name}» und kantonsweit", [name])
    if notice.canton_wide:
        office = _office(notice.authority)
        if (office and A.authorities_for(office)) or not _fold(notice.rubric).startswith("kanton"):
            # Canton-wide reaches every parcel in Aargau: only a canton office,
            # in a canton rubric, publishes for all of them.
            return [], Problem(f"{label}: kantonsweit nur für eine kantonale Stelle in einer "
                               f"Kanton-Rubrik (Stelle «{notice.authority}», Rubrik «{notice.rubric}»)",
                               [office] if office else [])
        return [_publication(notice, "", "kanton", notice.title, notice.text)], None
    if not A.authorities_for(name):
        return [], Problem(f"{label}: Gemeinde «{name}» ist nicht im Verzeichnis", [name])
    office = _office(notice.authority)
    if office and A.authorities_for(office) and not _fold(notice.rubric).startswith("kanton"):
        if A.municipality_key(office) != A.municipality_key(name):
            return [], Problem(f"{label}: Stelle «{notice.authority}» gehört zu {office}, nicht zu "
                               f"{name}", [name, office])
        return [_publication(notice, name, "gemeinde", notice.title, notice.text)], None
    return [_publication(notice, name, "kanton", notice.part or notice.title, notice.part)], None


def _attribute(notice):
    if notice.municipality or notice.canton_wide:
        return _attribute_explicit(notice)
    label = notice.pub_nr or notice.title[:60]
    rubric = _fold(notice.rubric)
    office = _office(notice.authority)
    everything = f"{notice.title.rstrip('.')}. {notice.text}"
    if office and not rubric.startswith("kanton"):
        if not A.authorities_for(office):
            return [], Problem(f"{label}: Gemeinde «{office}» ist nicht im Verzeichnis",
                               _mentions(everything))
        own = A.municipality_key(office)
        others = [n for n in _mentions(notice.title) if A.municipality_key(n) != own]
        if others:
            return [], Problem(f"{label}: Publikation von {office} nennt "
                               f"{', '.join(others)} — keiner Gemeinde zugeordnet",
                               [office] + others)
        return [_publication(notice, office, "gemeinde", notice.title, everything)], None
    if rubric.startswith("gemeinde"):
        return [], Problem(f"{label}: Stelle «{notice.authority}» ist keiner Gemeinde "
                           "zugeordnet", _mentions(everything))
    attributed, loose, common, intro = [], [], [], []
    for part, cut in _parts(notice.text):
        head = _PART_HEAD.match(part)
        if head and A.authorities_for(head.group(1)):
            name = head.group(1).strip()
            body = _trim(part[head.end():]) if cut else part[head.end():]
            neighbours = [n for n in _mentions(body)
                          if A.municipality_key(n) != A.municipality_key(name)]
            if neighbours:
                # A part headed for one municipality that names another — the
                # plan may be either's. Reported, not attributed.
                loose += [n for n in [name] + neighbours if n not in loose]
                continue
            attributed.append((name, body))
        else:
            named = _mentions(part)
            loose += [name for name in named if name not in loose]
            if not named and not _quoted(part):
                common.append(part)
                if not attributed and not loose:
                    intro.append(part)
    # Shared text speaks for every municipality only when there is one: with
    # several, a date or period in it may be any one plan's.
    context = decision = " ".join(common)
    if len(attributed) > 1:
        context = " ".join(s for p in common for s in _sentences(p) if _appeal_days(s))
        # The one act approving them all is dated before the list begins.
        decision = " ".join(intro)
    pubs = [_publication(notice, name, "kanton", body, body, context, decision)
            for name, body in attributed]
    if pubs:
        return pubs, (Problem(f"{label}: Teil der Publikation keiner Gemeinde zugeordnet "
                              f"(nennt {', '.join(loose)})", loose) if loose else None)
    title_names = _mentions(notice.title)
    title = _fold(notice.title)
    step = step_of(notice.title, notice.text)
    # Of the canton-wide instruments only the Richtplan is approved (by the
    # Bund); an approval citing the BauG approves municipalities' plans.
    if (rubric.startswith("kanton") and _CANTON_WIDE.search(title) and not title_names
            and not _MUNICIPAL_PLAN.search(title)
            and ("genehmig" not in title or "richtplan" in title)):
        # An address or a name in the text ("5001 Aarau") does not make a
        # canton-wide change any one municipality's.
        return [_publication(notice, "", "kanton", notice.title, everything)], None
    loose += [name for name in title_names if name not in loose]
    if loose:
        return [], Problem(f"{label}: keiner Gemeinde zugeordnet (nennt {', '.join(loose)})",
                           loose)
    folded = _fold(everything)
    if "gemeinde" in folded or "genehmig" in title or _PLANNING.search(folded):
        return [], Problem(f"{label}: keiner Gemeinde zugeordnet")
    return [], None


def publications(records):
    """(publications, problems). A record that is incomplete, or a notice no
    municipality can be read from, is a problem to report — never a guess."""
    out, problems = [], []
    for record in records or []:
        notice, problem = _notice(record)
        if problem:
            problems.append(problem)
            continue
        if notice.verification_problem:
            problems.append(notice.verification_problem)
        attributed, problem = _attribute(notice)
        if problem:
            problems.append(problem)
        out.extend(attributed)
    return out, problems


def _plan_names(pub) -> list:
    """What a publication calls the plan: its quoted name, a quoted plan name
    in its text, or the words after the instrument ("Gestaltungsplan Giessi")."""
    names = _quoted(pub.subject) or _quoted(pub.title)
    if not names:
        names = [n for n in _quoted(pub.text) if _PLAN_WORD.search(_fold(n))]
    if not names:
        area = _area(pub.subject, _instrument(pub.subject, pub.text)[1])
        names = [area] if area else []
    return names


def _amendment(pub) -> str:
    match = _AMEND.search(_fold(pub.subject))
    return match.group(0) if match else ""


def _effective_of(steps) -> Optional[date]:
    """The in-force date the approval or the Inkraftsetzung gives. What a
    decision announced is a plan (`_planned_of`): the approval may come later
    and say otherwise, or nothing."""
    return next((p.effective_on for p in reversed(steps)
                 if p.effective_on and p.step in ("genehmigung", "inkraft")), None)


def _planned_of(steps) -> Optional[date]:
    return next((p.effective_on for p in reversed(steps)
                 if p.effective_on and p.step == "beschluss"), None)


@dataclass(frozen=True)
class OerebListing:
    """A plan of a revision's kind and name in the parcel's ÖREB extract."""

    title: str
    number: str
    #: "inForce", "changeWithPreEffect", or "" in an extract cached before
    #: `oereb.details` kept it.
    lawstatus: str
    #: The extract's date: when the cadastre was read — not when the plan
    #: took force.
    checked_on: Optional[date]


@dataclass
class Revision:
    """One plan's way through the procedure, at its latest step."""

    steps: list
    stage: str
    #: Set by `view` (see `_status`): what the badge may say on the evidence.
    status: str = "unbestaetigt"
    #: Set by `view` from the parcel's ÖREB extract: a plan of this kind and
    #: name is listed for this parcel.
    on_parcel: bool = False
    #: That listing (`OerebListing`): a hint, never this revision's state.
    oereb: Optional[OerebListing] = None
    #: Set by `view` for a Planungszone not shown in force: (kind, date, title,
    #: link) of the reason — its term ("frist", "frist_ca", "befristet",
    #: "frist_offen", "ab"), a partial lifting ("teilaufhebung"), or what may
    #: be the entry into force of the plan it secures ("in_kraft",
    #: "oereb_unbestaetigt", "genehmigung", "inkraft", "ausgabe", "offen") and
    #: how that plan is linked to the zone: "explicit" (a notice says so),
    #: "kind" (the zone's notice names its kind, not its name), "name" (the
    #: same name only), "general" (it secures the Nutzungsplanung; an OEREBlex
    #: edition is not shown to be it).
    zone_end: Optional[tuple] = None

    @property
    def latest(self) -> Publication:
        return self.steps[-1]

    @property
    def zone_start(self) -> date:
        """When a Planungszone took effect: with the public display of its
        first notice (§ 29 Abs. 2 BauG)."""
        first = self.steps[0]
        return first.period_start or first.published_on

    @property
    def term_end(self) -> Optional[date]:
        """A Planungszone's term, as its latest notice stating one gives it."""
        return next((p.term_end for p in reversed(self.steps) if p.term_end), None)

    @property
    def step(self) -> str:
        return self.latest.step

    @property
    def published_on(self) -> date:
        return self.latest.published_on

    @property
    def url(self) -> str:
        return self.latest.url

    @property
    def effective_on(self) -> Optional[date]:
        return _effective_of(self.steps)

    @property
    def planned_on(self) -> Optional[date]:
        """The in-force date a decision announced, when no approval gives one."""
        return None if self.effective_on else _planned_of(self.steps)

    @property
    def municipality(self) -> str:
        return next((p.municipality for p in self.steps if p.municipality), "")

    @property
    def _origin(self) -> Publication:
        """The municipality's own first notice when there is one: it names the
        plan the way the municipality does, where a canton notice abbreviates."""
        return next((p for p in self.steps if p.level == "gemeinde"), self.steps[0])

    @property
    def title(self) -> str:
        return self._origin.subject or self._origin.title

    @property
    def instrument(self) -> str:
        return _instrument(self._origin.subject, self._origin.text)[0]

    @property
    def scope(self) -> str:
        if not self.municipality:
            return "kanton"
        return _instrument(self._origin.subject, self._origin.text)[1]

    @property
    def area(self) -> str:
        return _area(self._origin.subject, self.scope)

    @property
    def scope_label(self) -> str:
        if self.on_parcel:
            # The listing may be a predecessor of the same name.
            return "gleichnamiger Plan im ÖREB-Auszug"
        # A badge, not a sentence: an area named at length is left to the
        # title, which already carries it.
        area = self.area if len(self.area) <= 24 else ""
        return {
            "kanton": "Kanton Aargau",
            "gebiet": f"Gebiet «{area}»" if area else "Teilgebiet",
            "teil": f"Teilbereich «{area}»" if area else "Teilbereiche",
            "gemeinde": "Ganze Gemeinde",
        }.get(self.scope, "Geltungsbereich prüfen")

    @property
    def appeal_until(self) -> Optional[date]:
        latest = self.latest
        if latest.step != "genehmigung" or not latest.appeal_days:
            return None
        return latest.published_on + timedelta(days=latest.appeal_days)

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def tone(self) -> str:
        return STATUS_TONES[self.status]

    @property
    def decided_on(self) -> Optional[date]:
        """When the latest step's act was decided, if its notice says."""
        return self.latest.decided_on

    @property
    def history(self) -> str:
        """Each step with the date its notice was published — and the date
        of the act, when the notice gives one."""
        return " → ".join(
            f"{STEP_LABELS[p.step]} vom {p.decided_on:%d.%m.%Y}, publiziert {p.published_on:%d.%m.%Y}"
            if p.decided_on else f"{STEP_LABELS[p.step]}, publiziert {p.published_on:%d.%m.%Y}"
            for p in self.steps)


def _stage(steps, today) -> str:
    """The stage the evidence supports. An approved plan is in force once the
    in-force date its notices give has passed, and approved but not yet in
    force before it. A notice of the Inkraftsetzung whose date cannot be read
    shows nothing; a decision stays a decision whatever date it plans for."""
    latest = steps[-1].step
    effective = _effective_of(steps)
    if latest in ("genehmigung", "inkraft") and effective:
        return "in_kraft" if effective <= today else "genehmigt"
    if latest == "inkraft":
        return "unklar"
    return _STAGE_OF_STEP[latest]


def _status(revision, today) -> str:
    """What the badge may say, on the evidence the publications carry.

    A current-state label needs evidence that the state is current: a draft is
    "in Auflage" while the period its notice states runs; an approval's legal
    force is "ausstehend" while a stated in-force date or appeal period lies
    ahead; a Planungszone binds from its public display for at most five
    years, and not past the plan it secures (`_zone_verdict`). Without it the label
    says only what happened — "Beschlossen", "Genehmigt, Inkrafttreten nicht
    belegt" — or that the current state is not shown ("Stand unbestätigt").
    That nothing proves a plan in force is not proof that it is pending.
    """
    latest = revision.latest
    if revision.step == "planungszone":
        if _lifting(latest) == "ganz":
            return "planungszone_aufgehoben"
        return "planungszone"  # settled by `_zone_states`
    if revision.stage == "in_kraft":
        return "in_kraft"
    if revision.stage == "genehmigt":
        effective = revision.effective_on
        if effective and effective > today:
            return "genehmigt" if latest.step == "genehmigung" else "inkraft_festgelegt"
        if revision.appeal_until and revision.appeal_until >= today:
            return "genehmigt"
        return "genehmigt_unbelegt"
    if revision.stage == "beschlossen":
        # The extract lists a plan of this name, read after the decision: it
        # may be this one, in force by now — or its predecessor. Unless an
        # in-force date still ahead rules the first out, the state is open.
        # An amendment's listing is the plan it amends.
        listing = revision.oereb
        planned = revision.effective_on or revision.planned_on
        if (listing and listing.checked_on and listing.checked_on >= revision.published_on
                and not (planned and planned > today)
                and not _amendment(revision.latest)):
            return "unbestaetigt"
        return "beschlossen"
    if (revision.stage == "verfahren" and latest.period_start and latest.period_end
            and latest.period_start <= today <= latest.period_end):
        if latest.step == "auflage":
            return "entwurf_auflage"
        if latest.step == "mitwirkung":
            if re.search(r"anhörung|vernehmlassung", _fold(f"{latest.title} {latest.text}")):
                return "entwurf_anhoerung"
            return "entwurf_mitwirkung"
    return "unbestaetigt"


def _chain_key(pub):
    """Same municipality, same kind of plan, same amendment-or-not, same name.
    A municipality-wide plan has no name to tell two apart and runs one
    procedure at a time."""
    instrument, scope = _instrument(pub.subject, pub.text)
    if scope == "gemeinde":
        return pub.key, instrument, _amendment(pub)
    if instrument == "Planungszone":
        # Its lifting or extension is the zone's own next step, not a new plan.
        return pub.key, instrument, "", frozenset(_fold(n) for n in _plan_names(pub))
    names = frozenset(_fold(name) for name in _plan_names(pub))
    return (pub.key, instrument, _amendment(pub),
            names or _fold(re.sub(r"[^\w\s-]", " ", pub.subject)))


def chain(pubs, today=None):
    """Revisions from publications of ONE municipality (or of the canton as a
    whole). Publications naming the same plan join one revision; a Mitwirkung
    or Auflage after a decision starts a new one — the plan is being changed
    again. A canton approval that does not name the plan stays on its own:
    attaching it to the plan that seems to fit would be an inference."""
    today = today or date.today()
    groups = {}
    for pub in sorted(pubs, key=lambda p: (p.published_on, _RANK[p.step])):
        procedures = groups.setdefault(_chain_key(pub), [[]])
        current = procedures[-1]
        restarted = current and (
            (_RANK[current[-1].step] >= _RANK["beschluss"] and _RANK[pub.step] < _RANK["beschluss"])
            # After a decision, the same or an earlier step months later — or
            # any step years later — is a new procedure; it must not borrow
            # the old one's dates.
            or (current[-1].step in ("beschluss", "genehmigung", "inkraft", "verweigert")
                and (pub.published_on - current[-1].published_on).days
                > (SAME_ACT_DAYS if _RANK[pub.step] <= _RANK[current[-1].step]
                   else NEW_PROCEDURE_DAYS)))
        # A zone enacted again after its lifting is a new zone, with its own term.
        reenacted = (current and pub.step == current[-1].step == "planungszone"
                     and _lifting(current[-1]) == "ganz" and not _lifting(pub))
        if restarted or reenacted:
            current = []
            procedures.append(current)
        current.append(pub)
    return [Revision(steps, _stage(steps, today))
            for procedures in groups.values() for steps in procedures if steps]


@dataclass
class View:
    """What block E shows about planning procedures for one municipality."""

    status: str = "ok"           #: "off" | "uncovered" | "error" | "ok"
    error: str = ""
    stand: Optional[date] = None
    #: The first publication date the store covers; "none under way" means
    #: none since then.
    since: Optional[date] = None
    in_force: list = field(default_factory=list)
    #: Approved: legal force pending on the evidence, or not shown either way.
    approved: list = field(default_factory=list)
    decided: list = field(default_factory=list)
    #: Drafts whose stated period runs today.
    ongoing: list = field(default_factory=list)
    #: Everything whose current state no source shows.
    unclear: list = field(default_factory=list)
    #: Planungszonen lifted, or ended by their term or the plan they secure.
    ended: list = field(default_factory=list)
    canton: list = field(default_factory=list)
    #: OEREBlex edict → the revision whose approval put it in force. The same
    #: change seen from two sources belongs on one line.
    approvals: dict = field(default_factory=dict)
    #: Publications that name this municipality but could not be attributed to
    #: one plan of it — reason enough not to say "nothing under way".
    unattributed: int = 0
    #: Version 1: this municipality's publications and the canton-wide ones,
    #: each as printed, newest first — none merged, no status inferred.
    publications: list = field(default_factory=list)
    canton_publications: list = field(default_factory=list)
    #: Whether block E shows the inference path (`ENV_INFERENCE`) instead.
    inference: bool = False
    #: The store's last import, and one that failed after it (`Store`).
    updated_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    failure: str = ""

    def all(self) -> list:
        return (self.in_force + self.approved + self.decided + self.ongoing + self.unclear
                + self.ended + self.canton)

    def pending(self) -> list:
        """Everything for this municipality that is not shown in force."""
        return self.approved + self.decided + self.ongoing + self.unclear


def _approved_edict(revision, edicts):
    """The OEREBlex edict this approval put in force: same municipality, in
    force from shortly before the notice to shortly after it. An approval
    published months after the edict is a later change, not this one."""
    if (revision.stage not in ("in_kraft", "genehmigt")
            or not revision.instrument.startswith("Nutzungsplanung")):
        return None
    key = A.municipality_key(revision.municipality)
    same = [e for e in edicts or () if A.municipality_key(e.municipality) == key]
    if revision.scope != "gemeinde":
        # A partial revision approved around an edition may be another change
        # than that edition's: the same one only when its decision date is the
        # edition's in-force date.
        return next((e for e in same if revision.decided_on
                     and e.in_force == revision.decided_on), None)
    close = [e for e in same
             if revision.published_on - timedelta(days=45)
             <= e.in_force <= revision.published_on + timedelta(days=60)]
    return min(close, key=lambda e: abs((e.in_force - revision.published_on).days),
               default=None)


def _newest_first(revisions):
    return sorted(revisions, key=lambda r: r.published_on, reverse=True)


#: How an area plan's kind is written in an ÖREB document title.
_STEMS = {
    "Planungszone": ("planungszone",),
    "Gestaltungsplan": ("gestaltungsplan",),
    "Erschliessungsplan": ("erschliessungsplan",),
    "Überbauungs-/Baulinienplan": ("überbauungsplan", "baulinienplan"),
    "Beitragsplan": ("beitragsplan",),
    "Sondernutzungsplan": ("sondernutzungsplan", "gestaltungsplan", "erschliessungsplan"),
}


def _unquote(text) -> str:
    return re.sub(r"[«»\"„“”']", "", text).strip(" .-…")


def _extract_names(title, stems) -> set:
    """What an ÖREB title calls the plan, whole: 'Gestaltungsplan Giessi,
    1. Änderung' → {'gestaltungsplan giessi', 'giessi'}. Compared as a whole,
    so 'Zentrumszone' and 'Zentrum Hirschthal' are not 'Zentrum'."""
    folded = _fold(title)
    names = {_unquote(re.split(r"[,;(]", folded, 1)[0])}
    for stem in stems:
        at = folded.find(stem)
        if at >= 0:
            names.add(_unquote(re.split(r"[,;(]", folded[at + len(stem):], 1)[0]))
    return names - {""}


def _listing(revision, provisions):
    """The extract's entry of this revision's kind and name — an `inForce` one
    first. The name is all the match rests on: a later plan that keeps its
    predecessor's name matches the predecessor."""
    if revision.scope != "gebiet":
        return None
    stems = _STEMS.get(revision.instrument, ())
    names = {_unquote(_fold(n)) for n in _plan_names(revision._origin)}
    found = [p for p in provisions
             if any(stem in _fold(p.get("title")) for stem in stems)
             and names & _extract_names(_fold(p.get("title")), stems)]
    return next((p for p in found if p.get("lawstatus") == "inForce"),
                found[0] if found else None)


def _apply_parcel(revisions, parcel, today):
    """What the parcel's ÖREB extract can say about a published revision: that
    a plan of its kind and name covers this parcel (`on_parcel`), kept with
    the extract's date (`oereb`). Never which version: no field links a
    notice to an ÖREB document version — the name does not (a new plan keeps
    its predecessor's), nor does an official number a notice cites (it may
    be the number of the plan being replaced). So the extract never sets a
    revision's state; a listing read after a decision leaves that decision
    open (`_status`)."""
    if not parcel:
        return
    provisions = [p for p in parcel.get("provisions") or [] if p.get("title")]
    checked = _date(str(parcel.get("created") or "")[:10])
    for revision in revisions:
        listing = _listing(revision, provisions)
        if listing is not None:
            revision.on_parcel = True
            revision.oereb = OerebListing(str(listing.get("title")),
                                          str(listing.get("number") or "").strip(),
                                          str(listing.get("lawstatus") or ""), checked)


#: What a zone's notice says it secures: the phrase after "zur Sicherung
#: der/des", between "um" and "zu sichern", after "sichert die/den" or "im
#: Hinblick auf die/den" — up to the end of the clause.
_SECURING = (
    re.compile(r"sicherung\s+(?:der|des|von|eines|einer)\s+(?P<obj>[^,;:]+)"),
    re.compile(r"\bum\s+(?P<obj>[^,;:]+?)\s+zu\s+sichern"),
    re.compile(r"\bsichert\s+(?:die|den|das)\s+(?P<obj>[^,;:]+)"),
    re.compile(r"\bim\s+hinblick\s+auf\s+(?:die|den|das)\s+(?P<obj>[^,;:]+)"),
)
#: Where that phrase ends: its verb or a preposition starts the next part
#: ("… der Gesamtrevision der Nutzungsplanung erlässt … für das Gebiet «X»").
_PHRASE_END = re.compile(r"\s(?:erlässt|erlassen|wird|werden|hat|haben|ist|sind|für|im|in|auf|bei"
                         r"|südlich|nördlich|östlich|westlich|zwischen|gemäss|und)\s")
#: The zone itself, named in that phrase: "erlässt … die Planungszone «Oberdorf»".
_ZONE_MENTION = re.compile(r"planungszone\s*(?:«[^»]*»|\"[^\"]*\"|„[^“”]*[“”])?")
#: The zone's own name where a sentence gives it: "die Planungszone «Oberdorf»".
_ZONE_NAMED = re.compile(r"planungszone\s*(?:«([^»]*)»|\"([^\"]*)\"|„([^“”]*)[“”])")
#: A plan's notice saying the zone ends with it — and not that it stays, or
#: anything negated ("kein Anlass, … aufzuheben").
_ZONE_ENDS = re.compile(r"dahinf|\bf(?:ä|a)ll(?:t|en)\b[^.]{0,60}\bdahin\b|aufgehoben|aufzuheben"
                        r"|ausser\s+kraft|erlisch|erlösch|\bendet\b|beendet")
_ZONE_STAYS = re.compile(r"\bnicht\b|\bkein\w*\b|\bohne\b|\bbleibt\b|unberührt|weiterhin")
_SONDERNUTZUNG = frozenset({"Gestaltungsplan", "Erschliessungsplan", "Überbauungs-/Baulinienplan",
                            "Beitragsplan", "Sondernutzungsplan"})


#: A lifting that covers part of a zone only.
_PARTIAL = re.compile(r"teilaufhebung|teilweise|teilgebiet|teilbereich|teil\s+des|"
                      r"für\s+das\s+(?:teil)?gebiet|im\s+bereich|perimeter")


def _lifting(pub) -> str:
    """"ganz" or "teil" for a notice lifting a zone — by its title — else ""."""
    if "aufhebung" not in _fold(pub.title):
        return ""
    return "teil" if _PARTIAL.search(_fold(f"{pub.title} {pub.text}")) else "ganz"


def _years_later(day, years) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


def _names(revision) -> set:
    return {_unquote(_fold(n)) for p in revision.steps for n in _plan_names(p)} - {""}


def _secured(zone) -> list:
    """(instrument, names) for each plan the zone's notices say it secures."""
    specs = []
    for pub in zone.steps:
        for sentence in _sentences(f"{pub.title.rstrip('.')}. {pub.text}"):
            folded = _fold(sentence)
            for pattern in _SECURING:
                for match in pattern.finditer(folded):
                    phrase = _ZONE_MENTION.sub(" ", _PHRASE_END.split(match.group("obj"), 1)[0])
                    instrument = _instrument(phrase)[0]
                    names = frozenset(_unquote(_fold(n)) for n in _quoted(phrase)) - {""}
                    if instrument != "Planung" or names:
                        specs.append((instrument, names))
    return specs


def _secures(spec, plan, names):
    """"explicit" when `plan` is the one a zone's notice names: the same kind
    of plan (a "Sondernutzungsplan" any of them, a "Revision der
    Nutzungsplanung" its Gesamtrevision) and the same name — or, unnamed, the
    Gesamtrevision. "kind" when the notice names the kind only: it may be
    this one. None otherwise."""
    instrument, spec_names = spec
    if instrument == "Sondernutzungsplan":
        same_kind = plan.instrument in _SONDERNUTZUNG
    elif instrument == "Nutzungsplanung":
        same_kind = plan.instrument == "Nutzungsplanung, Gesamtrevision"
    else:
        same_kind = instrument == plan.instrument
    if not same_kind:
        return None
    if spec_names:
        return "explicit" if spec_names & names else None
    return "explicit" if plan.instrument == "Nutzungsplanung, Gesamtrevision" else "kind"


def _names_zone(revision, zone_names, zones) -> bool:
    """Whether a plan's own notice says the zone ends with it — "Mit dem
    Inkrafttreten fällt die Planungszone «Oberdorf» dahin" — naming it, or
    "die Planungszone" where the municipality has only one. A notice saying
    the zone stays does not."""
    for pub in revision.steps:
        for sentence in _sentences(f"{pub.title.rstrip('.')}. {pub.text}"):
            folded = _fold(sentence)
            if ("planungszone" in folded and _ZONE_ENDS.search(folded)
                    and not _ZONE_STAYS.search(folded)):
                named = {_unquote(_fold(next(g for g in m.groups() if g is not None)))
                         for m in _ZONE_NAMED.finditer(folded)}
                if named & zone_names or (not named and zones == 1):
                    return True
    return False


def _plan_event(revision, start, today):
    """What a plan's sources show of its entry into force since `start`:
    (kind, date, proven) — or None when nothing since then may be it."""
    if revision.status == "in_kraft":
        if revision.effective_on and start < revision.effective_on <= today:
            return "in_kraft", revision.effective_on, True
        return None
    listing = revision.oereb
    if (revision.status == "unbestaetigt" and listing
            and listing.checked_on and listing.checked_on > start):
        return "oereb_unbestaetigt", listing.checked_on, False
    if (revision.latest.step in ("genehmigung", "inkraft")
            and revision.status in ("genehmigt_unbelegt", "unbestaetigt")
            and revision.latest.published_on > start):
        return revision.latest.step, revision.latest.published_on, False
    return None


def _zone_verdict(zone, revisions, edicts, approvals, key, today, zones):
    """(status, reason) for a zone that is not simply in force, or None.

    Ended — on evidence: the term its notice states, five years from its
    public display (§ 29 Abs. 2 BauG), or the plan it secures in force by a
    stated date. That plan is the one a notice links to the zone: the zone's
    notice names it (`_secures`), or the plan's notice says the zone ends with
    it (`_names_zone`). Another plan of the municipality is no evidence and
    leaves the zone as it is. Open: a plan of the zone's name only, an
    approval of the secured plan without an in-force date, an OEREBlex
    edition no known change explains while the zone secures the
    Nutzungsplanung, a partial lifting, or five years ending on no known day."""
    first = zone.steps[0]
    start = zone.zone_start
    if start > today:
        return "unbestaetigt", ("ab", start, "", "")
    if zone.term_end and zone.term_end < today:
        return "planungszone_beendet", ("befristet", zone.term_end, "", "")
    limit = _years_later(start, PLANUNGSZONE_YEARS)
    slack = timedelta(days=0 if first.period_start else DISPLAY_SLACK_DAYS)
    if today > limit + slack:
        if first.period_start:
            return "planungszone_beendet", ("frist", limit, "", "")
        return "planungszone_beendet", ("frist_ca", first.published_on, "", "")
    if today > limit:
        return "unbestaetigt", ("frist_offen", limit, "", "")
    if _lifting(zone.latest) == "teil":
        return "unbestaetigt", ("teilaufhebung", zone.latest.published_on, "", "")
    specs = _secured(zone)
    own = _names(zone)
    proven, open_ = [], []
    for plan in revisions:
        if plan.step == "planungszone":
            continue
        names = _names(plan)
        links = {_secures(spec, plan, names) for spec in specs}
        if "explicit" in links or _names_zone(plan, own, zones):
            link = "explicit"
        elif "kind" in links:
            link = "kind"
        elif own & names:
            link = "name"
        else:
            continue
        event = _plan_event(plan, start, today)
        if event:
            kind, when, certain = event
            (proven if certain and link == "explicit" else open_).append(
                (kind, when, plan.title, link))
    if any(instrument.startswith("Nutzungsplanung") for instrument, _ in specs):
        for edict in edicts or ():
            if A.municipality_key(edict.municipality) != key or not start < edict.in_force <= today:
                continue
            approval = approvals.get(edict)
            title = edict.title or edict.label
            links = ({_secures(spec, approval, _names(approval)) for spec in specs}
                     if approval is not None else set())
            if approval is None:
                open_.append(("ausgabe", edict.in_force, title, "general"))
            elif "explicit" in links:
                proven.append(("in_kraft", edict.in_force, title, "explicit"))
            elif "kind" in links:
                open_.append(("in_kraft", edict.in_force, approval.title, "kind"))
            # Otherwise the edition is the change merged into it, not the secured plan.
        if edicts is None:
            open_.append(("offen", None, "", "general"))
    if proven:
        return "planungszone_beendet", min(proven, key=lambda reason: reason[1])
    if open_:
        return "unbestaetigt", min(open_, key=lambda reason: reason[1] or date.max)
    return None


def _zone_states(revisions, edicts, approvals, key, today):
    """Settle each Planungszone its notices show in force (`_zone_verdict`)."""
    zones = [r for r in revisions if r.step == "planungszone"]
    for zone in zones:
        if zone.status == "planungszone":
            verdict = _zone_verdict(zone, revisions, edicts, approvals, key, today, len(zones))
            if verdict:
                zone.status, zone.zone_end = verdict


def listing(pubs, municipality) -> View:
    """Version 1: the municipality's and the canton-wide publications, each as
    printed, newest first. Nothing is chained, dated or matched."""
    key = A.municipality_key(municipality)
    newest = lambda found: sorted(found, key=lambda p: p.published_on, reverse=True)  # noqa: E731
    return View(publications=newest(p for p in pubs if p.municipality and p.key == key),
                canton_publications=newest(p for p in pubs if not p.municipality))


def view(pubs, municipality, edicts=(), today=None, parcel=None, inference=False) -> View:
    """This municipality's revisions by status, plus canton-wide planning
    changes. Matched on the exact municipality — Wohlenschwil's plans are
    not Wohlen's. `parcel` is the parcel's ÖREB extract as `oereb.details`
    keeps it, when there is one. `edicts` None means the editions in force
    are not known (OEREBlex down or still loading). Nothing is hidden for its age: an old
    publication keeps its place, with a label that claims no more than its
    evidence (`_status`)."""
    today = today or date.today()
    key = A.municipality_key(municipality)
    out = listing(pubs, municipality)
    out.inference = inference
    revisions = chain([p for p in pubs if p.municipality and p.key == key], today)
    for revision in list(revisions):
        edict = _approved_edict(revision, edicts)
        if edict is not None and edict not in out.approvals:
            out.approvals[edict] = revision
            revisions.remove(revision)
    _apply_parcel(revisions, parcel, today)
    for revision in revisions:
        revision.status = _status(revision, today)
    _zone_states(revisions, edicts, out.approvals, key, today)
    for revision in revisions:
        getattr(out, _BUCKET[revision.status]).append(revision)
    out.canton = chain([p for p in pubs if not p.municipality], today)
    for revision in out.canton:
        revision.status = _status(revision, today)
    for name in ("in_force", "approved", "decided", "ongoing", "unclear", "ended", "canton"):
        setattr(out, name, _newest_first(getattr(out, name)))
    return out


@dataclass
class Store:
    status: str                  #: "off" | "error" | "ok"
    publications: list
    stand: Optional[date]
    error: str = ""
    problems: list = field(default_factory=list)
    records: int = 0
    #: Publication numbers that occur more than once — what overlapping pages
    #: of an import look like.
    duplicates: list = field(default_factory=list)
    since: Optional[date] = None
    #: Comparison keys of the municipalities the store covers; None for all.
    municipalities: Optional[frozenset] = None
    #: Records whose source is not one publication — the arrow would open a
    #: search or another page instead of the original.
    not_publications: list = field(default_factory=list)
    #: When an import last replaced the store (`amtsblatt_import`).
    updated_at: Optional[datetime] = None
    #: An import that failed after the last one that worked: when, and why.
    #: The store on disk is still the last valid one.
    failed_at: Optional[datetime] = None
    failure: str = ""


_CACHE = {}


def status_path(path) -> str:
    """Where `amtsblatt_import` records its last attempt beside a store."""
    return f"{path}.status.json"


def _import_status(path):
    """(failed_at, why) when the last import attempt failed — it came after
    the last success, by being the last — else (None, ""). A missing or
    unreadable status says nothing."""
    try:
        with open(status_path(path), encoding="utf-8") as handle:
            status = json.load(handle)
        if status.get("last_attempt_ok", True):
            return None, ""
        failed = datetime.fromisoformat(status["last_attempt_at"])
        return failed, str(status.get("last_error") or "unbekannter Fehler")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, ""


def _stamp(value) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def reset_cache():
    """Test-only: the store is cached per path, file date and size."""
    _CACHE.clear()


def load_store(path=None) -> Store:
    """The configured publication store. Unset is "off" — the panel then says
    procedures are not tracked and links to the Amtsblatt. A store that is set
    but unreadable is an error, never an empty list: "no procedures" and
    "could not read the list" must not look alike."""
    path = (os.environ.get(ENV, "") if path is None else path or "").strip()
    if not path:
        return Store("off", [], None)
    try:
        stat = os.stat(path)
        try:
            beside = os.stat(status_path(path))
            beside = (beside.st_mtime, beside.st_size)
        except OSError:
            beside = None
        version = (stat.st_mtime, stat.st_size, beside)
        cached = _CACHE.get(path)
        if cached and cached[0] == version:
            return cached[1]
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as exc:
        return Store("error", [], None, f"Verzeichnis nicht lesbar ({type(exc).__name__})")
    except ValueError as exc:
        return Store("error", [], None, f"Verzeichnis fehlerhaft ({type(exc).__name__})")
    records = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        return Store("error", [], None, "Verzeichnis enthält keine Publikationsliste")
    meta = payload if isinstance(payload, dict) else {}
    covered = meta.get("municipalities")
    if "municipalities" in meta and not isinstance(covered, list):
        # Absent means every Gemeinde; a null or a blank is no such statement.
        return Store("error", [], None, "Verzeichnis fehlerhaft (municipalities ist keine Liste)")
    try:
        stand = _date(meta.get("stand")) or date.fromtimestamp(stat.st_mtime)
        # Fields only the inference path uses are parsed here, too: whatever
        # fails reads as an unreadable list, never as a page that fails.
        pubs, problems = publications(records)
        numbers = Counter(record_key(r) for r in records if isinstance(r, dict) and r.get("pub_nr"))
        store = Store(
            "ok", pubs, stand, "", problems, len(records),
            sorted(n for n, count in numbers.items() if count > 1),
            since=_date(meta.get("since")),
            municipalities=None if covered is None else frozenset(A.municipality_key(n) for n in covered),
            not_publications=sorted({f"{p.pub_nr or p.title} ({p.url})" for p in pubs
                                     if A.link_kind(p.url) != "publikation"}),
            updated_at=_stamp(meta.get("updated_at")),
        )
        store.failed_at, store.failure = _import_status(path)
    except Exception as exc:
        return Store("error", [], None, f"Verzeichnis nicht auswertbar ({type(exc).__name__})")
    _CACHE[path] = (version, store)
    return store


def state_for(municipality, edicts, path=None, today=None, parcel=None) -> View:
    store = load_store(path)
    if store.status != "ok":
        return View(status=store.status, error=store.error)
    key = A.municipality_key(municipality)
    if store.municipalities is not None and key not in store.municipalities:
        return View(status="uncovered", stand=store.stand, since=store.since)
    try:
        result = (view(store.publications, municipality, edicts, today=today, parcel=parcel,
                       inference=True)
                  if inference_enabled() else listing(store.publications, municipality))
    except Exception as exc:  # the card says the list is unreadable; the page stays up
        return View(status="error", error=f"Verzeichnis nicht auswertbar ({type(exc).__name__})")
    result.stand = store.stand
    result.since = store.since
    result.updated_at, result.failed_at, result.failure = store.updated_at, store.failed_at, store.failure
    result.unattributed = sum(
        1 for problem in store.problems
        if any(A.municipality_key(name) == key for name in getattr(problem, "mentions", ())))
    return result


#: New publications appear Monday to Friday at 07:00; a store a week behind
#: has missed at least five of them.
STALE_DAYS = 7


def check(path, today=None, stale_ok=False):
    """(ok, report lines) for a store, before the panel is pointed at it.

    Fails on what would make the panel wrong rather than merely incomplete: a
    record it cannot read, a notice it cannot attribute, a publication
    imported twice, a source that is not the publication itself, a check dated
    after today, a store more than a week old — unless `stale_ok`, as for an
    import of older publications: the panel then says how old it is — a store
    that does not say since when it looks."""
    today = today or date.today()
    _CACHE.pop(path, None)
    store = load_store(path)
    if store.status != "ok":
        return False, [store.error or "Kein Verzeichnis angegeben."]
    lines = [f"{store.records} Publikationen, {len(store.publications)} Zuordnungen, "
             f"Stand {store.stand:%d.%m.%Y}"
             + (f", erfasst seit {store.since:%d.%m.%Y}" if store.since else "")]
    ok = True
    if store.since is None:
        ok = False
        lines.append("Erfassungsbeginn fehlt: Feld 'since' angeben (erstes erfasstes "
                     "Publikationsdatum) — sonst heisst \"keine Verfahren\" nichts.")
    for problem in store.problems:
        ok = False
        lines.append(f"Problem: {problem}")
    for number in store.duplicates:
        ok = False
        lines.append(f"Doppelt importiert: {number}")
    for source in store.not_publications:
        ok = False
        lines.append(f"Quelle ist keine Einzelpublikation: {source}")
    for pub in store.publications:
        if pub.verified and pub.verified.verified_on > today:
            ok = False
            lines.append(f"Problem: {pub.pub_nr} ({pub.municipality or 'kantonsweit'}): geprüft am "
                         f"{pub.verified.verified_on:%d.%m.%Y}, nach heute")
    if store.stand > today:
        ok = False
        lines.append(f"Problem: Stand {store.stand:%d.%m.%Y} liegt nach heute")
    age = (today - store.stand).days
    if age > STALE_DAYS:
        ok = ok and stale_ok
        lines.append(f"Verzeichnis veraltet: Stand {store.stand:%d.%m.%Y}, {age} Tage alt")
    if store.failed_at:
        lines.append(f"Letzter Import am {store.failed_at:%d.%m.%Y %H:%M} fehlgeschlagen: {store.failure}")
    names = sorted({p.municipality for p in store.publications if p.municipality})
    for name in names:
        if inference_enabled():
            result = view(store.publications, name, today=today)
            counts = Counter(r.status_label for r in result.all() if r.municipality)
            lines.append(f"{name}: " + ", ".join(f"{label}: {n}" for label, n in sorted(counts.items())))
        else:
            # Version 1 reads no step from a publication; neither does the check.
            count = sum(p.municipality == name for p in store.publications)
            lines.append(f"{name}: {count} Publikation{'' if count == 1 else 'en'}")
    canton = [p for p in store.publications if not p.municipality]
    if canton:
        lines.append(f"Kanton (kantonsweit): {len(canton)}")
    lines.append("OK" if ok else "NICHT OK — das Panel nicht auf dieses Verzeichnis umstellen.")
    return ok, lines


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3 or sys.argv[1] != "check":
        sys.exit("Aufruf: python -m planning check <verzeichnis.json>")
    passed, report = check(sys.argv[2])
    print("\n".join(report))
    sys.exit(0 if passed else 1)
