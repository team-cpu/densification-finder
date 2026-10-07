"""Links into the Amtsblatt, filtered to one municipality.

The Amtsblatt des Kantons Aargau is where a planning change shows up first: a
municipality's Mitwirkung, its öffentliche Auflage, the Gemeinderat's
Beschluss, and the canton's Genehmigung are all published there, months or
years before OEREBlex lists the result as in force. It is also the one source
whose own rules stand in the way of copying it:

* `robots.txt` disallows the search (`/publikationen/`), every publication
  (`/ekab/`) and their attachments (`/fileadmin/ekab/`) for every crawler.
* The legal notice reserves every use beyond the legally permitted cases to
  the Staatskanzlei's prior written consent and forbids "Einspeisung in
  Online-Dienste" by unauthorised third parties
  (amtsblatt.ag.ch/informationen/rechtliche-hinweise/, read on 2026-10-05 and
  2026-10-06). Whether Scope's use is a permitted case is an open legal
  question (NEWSFEED.md §5).

So this module does the part that reuses nothing: it builds the link to the
Amtsblatt's own search, already filtered to the parcel's municipality, and the
reader's browser does the reading. Nothing here fetches anything.

The filter format is the one the search page itself writes into the address
bar when a reader ticks a publishing office and a category. It was checked in
a browser on 2026-10-05, driven by Claude at the user's request: Seon's link listed its eleven planning publications and
nothing from another municipality; two offices for one municipality are
combined, not intersected; `searchQuery` with quotes finds the canton's
approval notices that name the municipality.
"""
import re
from urllib.parse import quote, unquote, urlsplit

BASE = "https://amtsblatt.ag.ch/publikationen/"

#: Search categories (`Rubrik`), as the search page numbers them.
BNO = "190,192"             #: Gemeinden → Bau- und Nutzungsordnung
RAUMPLANUNG = "162,175"     #: Kanton → Raumplanung (approvals of municipal plans)
MITWIRKUNG = "162,166"      #: Kanton → Anhörungs- und Mitwirkungsverfahren

#: `timerange[type]=4` is "Ohne Einschränkungen". A revision takes years from
#: Mitwirkung to Genehmigung, so cutting the list to this month or this year
#: would hide exactly the earlier steps that say where it stands.
ALL_TIME = "4"

#: Every publishing office ("Publizierende Stelle") of each municipality, keyed
#: by the OEREBlex spelling this tool already uses for municipality names.
#: Snapshot of the search page's office list on 2026-10-05, restricted to the
#: 196 municipalities OEREBlex carries — all 196 have at least one office.
#: Departments are kept: Baden, Lenzburg and Aarburg publish only through them.
#: An office the Amtsblatt adds later is missing here until this table is
#: refreshed; the link then still shows the main office's publications.
AUTHORITIES = {
    "Aarau": ("10,131",),
    "Aarburg": ("10,384", "10,471", "10,795", "10,825"),
    "Abtwil": ("10,279",),
    "Ammerswil": ("10,470",),
    "Aristau": ("10,127",),
    "Arni (AG)": ("10,110",),
    "Auenstein": ("10,42",),
    "Auw": ("10,130",),
    "Baden": ("10,151", "10,269", "10,423", "10,448", "10,550", "10,576", "10,581", "10,721"),
    "Beinwil (Freiamt)": ("10,156",),
    "Beinwil am See": ("10,159",),
    "Bellikon": ("10,122",),
    "Bergdietikon": ("10,273",),
    "Berikon": ("10,459",),
    "Besenbüren": ("10,290",),
    "Bettwil": ("10,121",),
    "Biberstein": ("10,456",),
    "Birmenstorf (AG)": ("10,278",),
    "Birr": ("10,225",),
    "Birrhard": ("10,473",),
    "Birrwil": ("10,170",),
    "Boniswil": ("10,446",),
    "Boswil": ("10,126",),
    "Bottenwil": ("10,193",),
    "Bremgarten (AG)": ("10,363",),
    "Brittnau": ("10,400",),
    "Brugg": ("10,144",),
    "Brunegg": ("10,549",),
    "Buchs (AG)": ("10,75",),
    "Buttwil": ("10,119",),
    "Böttstein": ("10,171",),
    "Bözberg": ("10,43",),
    "Böztal": ("10,619",),
    "Bünzen": ("10,124",),
    "Büttikon": ("10,120",),
    "Densbüren": ("10,265",),
    "Dietwil": ("10,123",),
    "Dintikon": ("10,317",),
    "Dottikon": ("10,117",),
    "Döttingen": ("10,85",),
    "Dürrenäsch": ("10,47",),
    "Eggenwil": ("10,297",),
    "Egliswil": ("10,74",),
    "Ehrendingen": ("10,50",),
    "Eiken": ("10,347",),
    "Endingen": ("10,177",),
    "Ennetbaden": ("10,270",),
    "Erlinsbach (AG)": ("10,427",),
    "Fahrwangen": ("10,66",),
    "Fischbach-Göslikon": ("10,304",),
    "Fisibach": ("10,348",),
    "Fislisbach": ("10,258",),
    "Freienwil": ("10,206",),
    "Frick": ("10,229",),
    "Full-Reuenthal": ("10,70",),
    "Gansingen": ("10,524",),
    "Gebenstorf": ("10,192",),
    "Geltwil": ("10,137",),
    "Gipf-Oberfrick": ("10,56",),
    "Gontenschwil": ("10,463",),
    "Gränichen": ("10,104",),
    "Habsburg": ("10,254",),
    "Hallwil": ("10,468",),
    "Hausen (AG)": ("10,178",),
    "Hellikon": ("10,82",),
    "Hendschiken": ("10,44",),
    "Herznach-Ueken": ("10,415",),
    "Hirschthal": ("10,310",),
    "Holderbank (AG)": ("10,174",),
    "Holziken": ("10,492",),
    "Hunzenschwil": ("10,429",),
    "Hägglingen": ("10,245",),
    "Islisberg": ("10,486",),
    "Jonen": ("10,488",),
    "Kaiseraugst": ("10,227",),
    "Kaisten": ("10,163",),
    "Kallern": ("10,165",),
    "Killwangen": ("10,572",),
    "Kirchleerau": ("10,294",),
    "Klingnau": ("10,327", "10,441"),
    "Koblenz": ("10,403", "10,573"),
    "Kölliken": ("10,432",),
    "Künten": ("10,360",),
    "Küttigen": ("10,59",),
    "Laufenburg": ("10,302", "10,373"),
    "Leibstadt": ("10,69",),
    "Leimbach (AG)": ("10,530",),
    "Lengnau (AG)": ("10,183",),
    "Lenzburg": ("10,396", "10,434", "10,542", "10,627", "10,675", "10,803"),
    "Leuggern": ("10,247",),
    "Leutwil": ("10,288",),
    "Lupfig": ("10,226",),
    "Magden": ("10,166",),
    "Mandach": ("10,289",),
    "Meisterschwanden": ("10,316",),
    "Mellikon": ("10,94",),
    "Mellingen": ("10,287", "10,352"),
    "Menziken": ("10,256",),
    "Merenschwand": ("10,86",),
    "Mettauertal": ("10,464",),
    "Moosleerau": ("10,46",),
    "Muhen": ("10,132",),
    "Mumpf": ("10,246",),
    "Murgenthal": ("10,89",),
    "Muri (AG)": ("10,477",),
    "Mägenwil": ("10,97",),
    "Möhlin": ("10,103",),
    "Mönthal": ("10,469",),
    "Möriken-Wildegg": ("10,252",),
    "Mühlau": ("10,54",),
    "Mülligen": ("10,241",),
    "Münchwilen (AG)": ("10,467",),
    "Neuenhof": ("10,209",),
    "Niederlenz": ("10,370",),
    "Niederrohrdorf": ("10,108",),
    "Niederwil (AG)": ("10,509",),
    "Oberentfelden": ("10,162",),
    "Oberhof": ("10,321",),
    "Oberkulm": ("10,449",),
    "Oberlunkhofen": ("10,283",),
    "Obermumpf": ("10,608",),
    "Oberrohrdorf": ("10,116",),
    "Oberrüti": ("10,140",),
    "Obersiggenthal": ("10,204",),
    "Oberwil-Lieli": ("10,367",),
    "Oeschgen": ("10,179",),
    "Oftringen": ("10,90",),
    "Olsberg": ("10,228",),
    "Othmarsingen": ("10,379",),
    "Reinach (AG)": ("10,154",),
    "Reitnau": ("10,485",),
    "Remetschwil": ("10,125",),
    "Remigen": ("10,539",),
    "Rheinfelden": ("10,68",),
    "Riniken": ("10,438",),
    "Rothrist": ("10,161",),
    "Rottenschwil": ("10,128",),
    "Rudolfstetten-Friedlisberg": ("10,194",),
    "Rupperswil": ("10,439",),
    "Rüfenach": ("10,518",),
    "Safenwil": ("10,153",),
    "Sarmenstorf": ("10,231",),
    "Schafisheim": ("10,198",),
    "Schinznach": ("10,173",),
    "Schlossrued": ("10,105",),
    "Schmiedrued": ("10,409",),
    "Schneisingen": ("10,376",),
    "Schupfart": ("10,51",),
    "Schwaderloch": ("10,303", "10,587"),
    "Schöftland": ("10,72",),
    "Seengen": ("10,141",),
    "Seon": ("10,398",),
    "Siglistorf": ("10,184",),
    "Sins": ("10,199",),
    "Sisseln": ("10,481",),
    "Spreitenbach": ("10,57",),
    "Staffelbach": ("10,232",),
    "Staufen": ("10,498",),
    "Stein (AG)": ("10,93",),
    "Stetten (AG)": ("10,233",),
    "Strengelbach": ("10,478",),
    "Suhr": ("10,139",),
    "Tegerfelden": ("10,201",),
    "Teufenthal (AG)": ("10,249",),
    "Thalheim (AG)": ("10,200",),
    "Tägerig": ("10,136",),
    "Uerkheim": ("10,223",),
    "Uezwil": ("10,129",),
    "Unterentfelden": ("10,167",),
    "Unterkulm": ("10,40",),
    "Unterlunkhofen": ("10,298",),
    "Untersiggenthal": ("10,237",),
    "Veltheim (AG)": ("10,242",),
    "Villigen": ("10,272",),
    "Villmergen": ("10,53",),
    "Vordemwald": ("10,160",),
    "Wallbach": ("10,157",),
    "Waltenschwil": ("10,295",),
    "Wegenstetten": ("10,357",),
    "Wettingen": ("10,230",),
    "Widen": ("10,79",),
    "Wiliberg": ("10,195",),
    "Windisch": ("10,440", "10,730"),
    "Wittnau": ("10,351",),
    "Wohlen (AG)": ("10,168", "10,388", "10,765"),
    "Wohlenschwil": ("10,451",),
    "Wölflinswil": ("10,266",),
    "Würenlingen": ("10,280",),
    "Würenlos": ("10,155",),
    "Zeihen": ("10,502",),
    "Zeiningen": ("10,58",),
    "Zetzwil": ("10,443",),
    "Zofingen": ("10,222",),
    "Zufikon": ("10,73",),
    "Zurzach": ("10,620",),
    "Zuzgen": ("10,395",),
}


def municipality_key(name) -> str:
    """One spelling per place, for comparison only.

    The Amtsblatt hyphenates with U+2010 and writes "Hausen AG"; the building
    register hyphenates with ASCII and writes "Hausen (AG)". Both name the
    same municipality. Never used for display.
    """
    text = re.sub(r"[\u2010\u2011\u2012\u2013\u2014]", "-", str(name or "")).strip()
    text = re.sub(r"\s*\(AG\)$|\s+AG$", "", text)
    return re.sub(r"\s+", " ", text).casefold()


_BY_KEY = {municipality_key(name): offices for name, offices in AUTHORITIES.items()}
_BY_NAME = {municipality_key(name): name for name in AUTHORITIES}


def search_name(name) -> str:
    """The municipality as Amtsblatt texts write it: "Reinach", not
    "Reinach (AG)"."""
    text = re.sub(r"\s*\(AG\)$", "", str(name or "").strip())
    return re.sub(r"\s+", " ", text)


def canonical_name(name) -> str:
    """The directory's spelling of a municipality ("Hausen (AG)" for "Hausen
    AG"), or the name as given when the directory does not know it."""
    return _BY_NAME.get(municipality_key(name), str(name or "").strip())


def authorities_for(name) -> tuple:
    """The municipality's own offices — exact name, never a prefix: Wohlen's
    offices must not include Wohlenschwil's."""
    return _BY_KEY.get(municipality_key(name), ())


def link_kind(url) -> str:
    """What a link opens: one Amtsblatt publication, a document on OEREBlex, an
    Amtsblatt search, or anything else. The panel's arrow promises the
    publication itself; a search has to say it is one."""
    text = str(url or "").strip().lower()
    if not text:
        return ""
    try:
        path = urlsplit(text).path
    except ValueError:  # "https://[invalid/": no address a browser opens as one
        return "link"
    # A "." or ".." segment, encoded or not, leaves the publication: the
    # browser resolves "…/ekab/1/../../publikationen/" to the search.
    climbs = any(unquote(segment) in (".", "..") for segment in path.split("/"))
    if re.match(r"https?://(?:www\.)?amtsblatt\.ag\.ch/ekab/[\w.]+(?:/|$)", text) and not climbs:
        return "publikation"
    if re.match(r"https?://(?:www\.)?amtsblatt\.ag\.ch(?:/|$)", text):
        # Anything else on the Amtsblatt — its search with or without the
        # trailing slash, its home page with a query — is not one publication.
        return "suche"
    if re.match(r"https?://oereblex\.ag\.ch/api/attachments/\d+", text):
        return "dokument"
    return "link"


def _link(pairs) -> str:
    return BASE + "?" + "&".join(f"{k}={quote(v, safe=',')}" for k, v in pairs)


def municipal_planning_link(name) -> str:
    """The municipality's own planning publications, oldest step to newest."""
    offices = authorities_for(name)
    if offices:
        pairs = [("filter[authority][]", office) for office in offices]
    else:
        pairs = [("searchQuery", f'"{search_name(name)}"')]
    return _link(pairs + [("filter[category][]", BNO), ("timerange[type]", ALL_TIME)])


def canton_approvals_link(name) -> str:
    """The canton's approval notices that name the municipality.

    The canton publishes under its own offices, so filtering by the
    municipality's office cannot find these; their text names it instead.
    One notice often approves plans of several municipalities at once —
    the reader sees the whole notice, which is why Scope links rather than
    excerpts it.
    """
    return _link([("searchQuery", f'"{search_name(name)}"'),
                  ("filter[category][]", RAUMPLANUNG),
                  ("timerange[type]", ALL_TIME)])


def canton_consultations_link() -> str:
    """Canton-wide consultations — changes to rules that apply to every
    parcel in the canton, such as the Baugesetz or the Richtplan."""
    return _link([("filter[category][]", MITWIRKUNG), ("timerange[type]", ALL_TIME)])
