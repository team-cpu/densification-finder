"""
ÖREB extract for shortlisted parcels.

The cantonal cadastre answers per parcel and carries 25 themes, six of which
decide whether a parcel can be developed at all. None of them is heritage —
that lives in the registers handled by `constraints.py`.

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

# Restrictions that end the conversation: no permit will be issued.
HARD_CODES = {"ch.Planungszonen"}
HARD_TEXT = ("planungszone",)

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
        info = x.get("Information") or x.get("LegendText") or []
        text = info[0].get("Text", "") if isinstance(info, list) and info else ""
        share = x.get("AreaShare")
        key = (code, text)
        if key in seen:
            continue
        seen.add(key)
        out.append((code, text, share))
    return out



def assess(egrid):
    """Fetch and classify in one call. Returns (hard, notable, error)."""
    try:
        items = restrictions(fetch(egrid))
    except Exception as exc:  # network, 404 on an unknown EGRID, malformed body
        return [], [], str(exc)

    hard, notable = [], []
    for code, text, share in items:
        low = text.lower()
        label = f"{text} ({share} m²)" if share else text
        if code in HARD_CODES or any(h in low for h in HARD_TEXT):
            hard.append(label)
        elif any(n in low for n in NOTABLE_TEXT):
            notable.append(label)
    return hard, notable, None
