"""
Shared wording for the register's own vocabulary.

Lives apart from `app.py` because the detail view and the exported data sheet
have to say exactly what the result table says. A building period abbreviated
one way in the list and another way in the document would read as two different
buildings.
"""
import re

# GWR's own wording is exact but repetitive — ten rows of "Gebäude mit einer
# Wohnung" cost more width than they carry meaning.
USE_SHORT = {"1110": "1 Whg.", "1121": "2 Whg.", "1122": "3+ Whg."}

# Shown when a zone is governed by something other than Aargau's usual
# Ausnützungsziffer, so the "Ziffer" column is never read as the wrong metric.
METRIC_LABELS = {
    "UEZ": "Überbauungsziffer (Ziffer × Geschosse)",
    "BMZ": "Baumassenziffer",
    "GFZ": "Geschossflächenziffer",
}


def short_year(text):
    """"von 1946 bis 1960" is seventeen characters to say what "1946–60" says in
    seven, and the column is competing for width with the address."""
    t = (text or "").strip()
    m = re.match(r"von (\d{4}) bis (\d{2})(\d{2})$", t)
    if m:
        return f"{m.group(1)}–{m.group(3)}"
    m = re.match(r"nach (\d{4})$", t)
    if m:
        return f"ab {m.group(1)}"
    return t or "—"


def short_use(text):
    t = (text or "").strip()
    if "einer Wohnung" in t:
        return USE_SHORT["1110"]
    if "zwei" in t and "Wohnung" in t:
        return USE_SHORT["1121"]
    if "drei oder mehr" in t:
        return USE_SHORT["1122"]
    return t or "—"
