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
import threading
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


# ── Background fetch for the Streamlit panel ────────────────────────────────
# `load()` above is a synchronous network call — fine for a script, wrong for
# a page that has to appear before it returns. What follows lets the caller
# start it once, off whatever thread is rendering, and poll a module-level
# result every parcel's view shares (the edict list is canton-wide, so there
# is nothing to key a cache on). A bare `threading.Thread` carries no
# Streamlit `ScriptRunContext`, so `_fetch` below stays pure Python — no
# `st.*` calls — and `_LOCK` is the only thing the worker and the render
# thread both touch.
_LOCK = threading.Lock()
_STATUS = "not_started"    # "not_started" | "in_flight" | "done"
_RESULT = None             # (edicts, error), once _STATUS == "done"
_THREAD = None             # kept so tests can wait for a run to finish


def ensure_news_started(timeout=8):
    """Start the canton-wide fetch in the background, once per process.

    Returns True only on the call that actually starts it, so the caller can
    treat *this* render as "in flight" without a second, later read of
    `news_state` — the worker can finish before the next line runs (it does,
    reliably, whenever `load` is stubbed to return immediately, which every
    test that exercises this does), and racing that would make the very first
    frame skip the placeholder depending on how the thread happens to get
    scheduled instead of showing it every time.
    """
    global _STATUS, _THREAD
    with _LOCK:
        if _STATUS != "not_started":
            return False
        _STATUS = "in_flight"

    def _fetch():
        global _STATUS, _RESULT
        result = load(timeout=timeout)
        with _LOCK:
            _RESULT = result
            _STATUS = "done"

    _THREAD = threading.Thread(target=_fetch, daemon=True)
    _THREAD.start()
    return True


def news_state():
    """(status, result) — result is None until status is "done"."""
    with _LOCK:
        return _STATUS, _RESULT


def reset_news_cache():
    """Test-only. The cache above is a module-level singleton keyed by
    nothing — that is the point, the edict list is canton-wide rather than
    per-session — so without an explicit reset, every test after the first
    would see whatever fetch state (or worker thread) an earlier, unrelated
    test left behind. Joining before clearing makes that deterministic
    instead of racing a stray thread's write against the next test's setup.
    """
    global _STATUS, _RESULT, _THREAD
    thread, _THREAD = _THREAD, None
    if thread is not None and thread.is_alive():
        thread.join(timeout=5)
    with _LOCK:
        _STATUS = "not_started"
        _RESULT = None


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
