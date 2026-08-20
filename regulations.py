"""When each municipality's building regulation last changed.

Block D names the documents that govern a parcel, straight out of the ÖREB
extract. What the extract never says is *since when*: its `Lawstatus` reads
`inForce` and stops there. OEREBlex — the canton's own legal-document platform,
the one every link in block D already points into — answers that for the whole
canton in a single request:

    GET https://oereblex.ag.ch/api/edicts.json      ~76 KB, 196 municipalities

Kept out of the interface for the usual two reasons. The join has a trap worth a
test that does not need Streamlit running, and a failed fetch has to come back as
a value: a regulation panel that renders empty reads as "nothing has changed
lately", which is the opposite of "we could not ask".

`has_prepublications` is false for all 196 municipalities today, so this is a
record of what is already in force, never a warning about a revision under way.
Only the Amtsblatt carries those, and it forbids automated access — see
NEWSFEED.md.
"""
import json
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import Optional

URL = "https://oereblex.ag.ch/api/edicts.json"
BASE = "https://oereblex.ag.ch"


@dataclass(frozen=True)
class Edict:
    """One regulation in force, and when it became so."""

    municipality: str
    title: str
    abbreviation: str
    in_force: date
    syst_nr: str
    document: str          #: absolute URL of the PDF, or "" when none is filed

    @property
    def label(self) -> str:
        """`BNO` where there is one, the full title where there is not — some
        entries carry the year in the abbreviation (`BNO 1997`), which is the
        municipality's own naming and is left alone."""
        return self.abbreviation or self.title

    @property
    def when(self) -> str:
        return self.in_force.strftime("%d.%m.%Y")


def fetch(timeout=30):
    with urllib.request.urlopen(URL, timeout=timeout) as r:
        return json.load(r)


def parse(towns) -> list[Edict]:
    """Every regulation currently in force, newest first.

    Superseded versions are in the feed too — `is_active` false, with an
    `outaction_date` — and are dropped: this answers "what governs the parcel
    now", not "what governed it in 2013". An entry without an `inaction_date`
    is dropped as well rather than sorted as though it were undated.
    """
    out = []
    for town in towns or []:
        name = (town or {}).get("name") or ""
        for e in (town or {}).get("edicts") or []:
            if not e.get("is_active") or not e.get("inaction_date"):
                continue
            try:
                when = date.fromisoformat(e["inaction_date"])
            except (TypeError, ValueError):
                continue
            doc = (e.get("main_document") or {}).get("document_path") or ""
            out.append(Edict(
                municipality=name,
                title=e.get("title") or "",
                abbreviation=e.get("abbreviation") or "",
                in_force=when,
                syst_nr=str(e.get("syst_nr") or ""),
                document=(BASE + doc) if doc else "",
            ))
    out.sort(key=lambda x: (x.in_force, x.municipality), reverse=True)
    return out


def load(timeout=30):
    """(edicts, error). Never raises — a panel that cannot say why it is empty
    is worse than one that says the canton did not answer."""
    try:
        return parse(fetch(timeout=timeout)), ""
    except Exception as exc:            # offline, DNS, 5xx, malformed body
        return [], str(exc)


def for_municipality(edicts, name: str, bfs: Optional[int] = None):
    """The regulation governing one municipality, and a note when its OEREBlex
    number disagrees with the BFS number this tool keys on.

    Joined by NAME, deliberately. `syst_nr` looks like the BFS number and equals
    it in 162 of the 163 municipalities in the result set — but Dintikon is 4196
    in OEREBlex against BFS 4194 in the federal building register. Keying on it
    would have attached a neighbour's building regulation to those parcels.
    Names match on all 163, so the number is only ever a cross-check, and a
    disagreement is reported rather than resolved silently.
    """
    hits = [e for e in edicts if e.municipality == name]
    if not hits:
        return None, ""
    best = hits[0]                      # already newest-first
    note = ""
    if bfs is not None and best.syst_nr.isdigit() and int(best.syst_nr) != int(bfs):
        note = (f"OEREBlex führt {name} unter {best.syst_nr}, das Gebäuderegister "
                f"unter BFS {int(bfs)} — nach Name zugeordnet.")
    return best, note
