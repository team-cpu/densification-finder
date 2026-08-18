"""
ÖREB extract for shortlisted parcels.

The cantonal cadastre answers per parcel. Hard heritage protection IS in there —
"Kantonales Denkmalschutzobjekt", "Gebäude mit Substanzschutz" and "Gebäude mit
Volumenschutz" all arrive as legend texts inside the Nutzungsplanung theme, not
as themes of their own, which is why scanning theme titles misses them (an
earlier version of this file claimed heritage was absent for exactly that
reason; verified wrong on live extracts, 5 of 5 protected parcels visible).

What ÖREB does NOT carry is the advisory inventories — Bauinventar and
Kurzinventar returned nothing on any tested parcel. Those exist only in the
registers `constraints.py` loads, so the local layers stay necessary; this step
is the safety net behind them, per the brief's step 5, not their replacement.

Endpoint shape was not obvious and is worth recording: the format goes in the
path, the identifier is a query parameter, and the trailing slash matters.

    /extract/json/?EGRID=CH...        200
    /extract/reduced/json/CH...       404
    /extract/json/CH...               404

`getegrid/json/?EN=<E>,<N>` resolves a coordinate to an EGRID when one is not
already known.
"""
import json
import urllib.request

BASE = "https://api.geo.ag.ch/v2/oereb"

# Restrictions that end the conversation: no permit will be issued, or the
# building may not be demolished. The heritage patterns mirror C.HARD in
# constraints.py — normally the local layers exclude these parcels long before
# the shortlist, so a match here means the registers and the cadastre disagree
# and the safe reading is the restrictive one.
HARD_CODES = {"ch.Planungszonen"}
HARD_TEXT = ("planungszone", "denkmalschutz", "substanzschutz", "volumenschutz")

# Constraints worth surfacing but not excluding on — they shape a project
# rather than forbidding it. Matched on the legend text because Aargau delivers
# most of them inside the Nutzungsplanung theme rather than as separate codes.
NOTABLE_TEXT = (
    "hochwassergefahrenzone",
    "gewässerraum",
    "uferschutz",
    "waldabstand",
    "grundwasserschutzzone s1",
    "grundwasserschutzzone s2",
    "belastet",
    "landwirtschaftszone",
)


#: The theme the zoning plan and everything overlaid on it arrives under. Aargau
#: delivers base zones, design plans and heritage tiers all inside it, so this is
#: the theme to read for "what does the plan say here", not a filter to narrow.
ZONING_THEME = "ch.Nutzungsplanung"


def text(value, default=""):
    """The extract is multilingual: every human-readable field is a list of
    {Language, Text}. German is what the cantonal service returns and what the
    interface shows, so the first entry is taken rather than searched for."""
    if isinstance(value, list):
        return value[0].get("Text", default) if value else default
    if isinstance(value, dict):
        return text(value.get("Text"), default)
    return value if isinstance(value, str) else default


def fetch(egrid, timeout=60):
    url = f"{BASE}/extract/json/?EGRID={egrid}"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def restrictions(doc):
    """(theme_code, legend_text, area_share) per restriction, de-duplicated."""
    e = doc.get("GetExtractByIdResponse", {}).get("extract", doc.get("extract", doc))
    out, seen = [], set()
    for x in e.get("RealEstate", {}).get("RestrictionOnLandownership", []) or []:
        code = (x.get("Theme") or {}).get("Code", "")
        legend = text(x.get("Information") or x.get("LegendText"))
        share = x.get("AreaShare")
        key = (code, legend)
        if key in seen:
            continue
        seen.add(key)
        out.append((code, legend, share))
    return out



def details(doc):
    """Everything in the extract that describes the parcel rather than
    restricting it: the official zone split, the documents that govern it, and
    who is responsible.

    This is the half of the answer the tool used to throw away. The same request
    that decides whether a parcel is excluded also carries the parcel's building
    regulation — the municipality's BNO, the zoning plan, the cantonal Baugesetz
    — each with a link to the document itself. Fetching it again from anywhere
    else would be a second source that can disagree with this one.

    Deliberately keyed on `Type.Code`, which is how the printed extract splits
    its two lists: `LegalProvision` becomes *Rechtsvorschriften* (the plans and
    the BNO), `Law` becomes *Gesetzliche Grundlagen* (federal and cantonal law).
    """
    e = doc.get("GetExtractByIdResponse", {}).get("extract", doc.get("extract", doc))
    estate = e.get("RealEstate", {}) or {}

    zones, documents, seen_docs = [], {"provisions": [], "laws": []}, {}
    offices = []
    for x in estate.get("RestrictionOnLandownership", []) or []:
        legend = text(x.get("LegendText") or x.get("Information"))
        if (x.get("Theme") or {}).get("Code") == ZONING_THEME and legend:
            zones.append({
                "text": legend,
                "area": x.get("AreaShare"),
                "percent": x.get("PartInPercent"),
            })
        if x.get("ResponsibleOffice"):
            offices.append({
                "name": text(x["ResponsibleOffice"].get("Name")),
                "url": text(x["ResponsibleOffice"].get("OfficeAtWeb")),
            })
        for p in x.get("LegalProvisions") or []:
            kind = (p.get("Type") or {}).get("Code")
            bucket = {"LegalProvision": "provisions", "Law": "laws"}.get(kind)
            if not bucket:
                continue
            title = text(p.get("Title"))
            number = text(p.get("OfficialNumber"))
            url = text(p.get("TextAtWeb"))
            if not title:
                continue
            # One document, several files: a plan arrives with its annexes as
            # separate entries under the same title, which is why the printed
            # extract shows the title once with the links stacked beneath it.
            # Keying on the URL instead would list "Bauzonen- und Kulturlandplan"
            # twice and look like two different plans.
            key = (bucket, title, number)
            entry = seen_docs.get(key)
            if entry is None:
                entry = {
                    "title": title,
                    "abbr": text(p.get("Abbreviation")),
                    "number": number,
                    "urls": [],
                    # The extract's own ordering of the legal bases — federal
                    # before cantonal, and by subject. Reproducing it keeps the
                    # document recognisable next to the official one.
                    "index": p.get("Index") or 0,
                }
                seen_docs[key] = entry
                documents[bucket].append(entry)
            if url and url not in entry["urls"]:
                entry["urls"].append(url)

    documents["laws"].sort(key=lambda d: (d["index"], d["title"]))

    # Several offices answer for one parcel — the municipality for its zoning,
    # the canton for a road building line. Taking whichever came first put
    # "Abteilung Verkehr" on a parcel whose zoning question belongs to the
    # commune, so the commune wins when it is among them.
    municipality = estate.get("MunicipalityName", "")
    office = next((o for o in offices if o["name"] == municipality), None)
    if office is None:
        office = offices[0] if offices else {}
    return {
        "municipality": estate.get("MunicipalityName", ""),
        "bfs": estate.get("MunicipalityCode"),
        "parcel": estate.get("Number", ""),
        # The land registry's own area for this parcel. Worth carrying because
        # the tool computes its own from the cadastral geometry, and two numbers
        # that should agree are only useful if they are both visible.
        "land_registry_area": estate.get("LandRegistryArea"),
        "zones": zones,
        "provisions": documents["provisions"],
        "laws": documents["laws"],
        "office": office,
        "created": e.get("CreationDate", ""),
    }


def assess(egrid):
    """Fetch and classify in one call.

    Returns (hard, notable, error, details) — one request, both answers, because
    the extract is the expensive part and it carries the parcel's legal basis
    alongside the restrictions that might exclude it.
    """
    try:
        doc = fetch(egrid)
        items = restrictions(doc)
    except Exception as exc:  # network, 404 on an unknown EGRID, malformed body
        return [], [], str(exc), None

    hard, notable = [], []
    for code, legend, share in items:
        low = legend.lower()
        label = f"{legend} ({share} m²)" if share else legend
        if code in HARD_CODES or any(h in low for h in HARD_TEXT):
            hard.append(label)
        elif any(n in low for n in NOTABLE_TEXT):
            notable.append(label)
    return hard, notable, None, details(doc)
