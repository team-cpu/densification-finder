"""
Constraint layers: heritage, design-plan overlays, and building age.

Heritage in Aargau is spread across four registers that barely overlap, and the
strongest of them is not an object register at all — `Gebäude mit Substanzschutz`
and `Gebäude mit Volumenschutz` live in the zoning overlay and forbid demolition
outright. Any one register on its own misses most of the constrained stock.

Design plans matter for a different reason: where one is in force it may set its
own utilization figures, so the base-zone AZ is not necessarily the operative
one and the computed potential cannot be trusted there.
"""
import glob
import os
import sqlite3

from shapely import from_wkb
from shapely.geometry import Point
from shapely.strtree import STRtree

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Hard: demolition is not permitted. Soft: constrained, but a replacement can
# still be negotiated — surface it and let the user judge.
HARD = {"denkmalschutz", "substanzschutz", "volumenschutz"}

# GBAUP buckets the construction date into periods; the exact year (GBAUJ) is
# missing on two thirds of the target stock, so the age filter has to run off
# these. Value is the LAST year each period covers — a period is only excluded
# when every building in it is newer than the cutoff, so the filter never drops
# a candidate on a maybe.
GBAUP_PERIOD_END = {
    "8011": 1918, "8012": 1945, "8013": 1960, "8014": 1970, "8015": 1980,
    "8016": 1985, "8017": 1990, "8018": 1995, "8019": 2000, "8020": 2005,
    "8021": 2010, "8022": 2015, "8023": 9999,   # 8023 = "after 2015", open-ended
}
# Period start, needed to decide whether the WHOLE period is newer than the cutoff.
GBAUP_PERIOD_START = {
    "8011": 0, "8012": 1919, "8013": 1946, "8014": 1961, "8015": 1971,
    "8016": 1981, "8017": 1986, "8018": 1991, "8019": 1996, "8020": 2001,
    "8021": 2006, "8022": 2011, "8023": 2016,
}


def _gpkg(blob):
    env = (blob[3] >> 1) & 0x07
    return from_wkb(bytes(blob[8 + {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[env]:]))


def _one(pattern):
    hits = glob.glob(os.path.join(DATA, pattern))
    if not hits:
        raise SystemExit(f"missing data file: {pattern}")
    return hits[0]


def _table(db):
    return [r[0] for r in db.execute("SELECT table_name FROM gpkg_contents")][0]


def load_heritage():
    """All four registers as (geometry, tier) pairs, in one index."""
    items, tiers = [], []

    for path, tier in (
        ("ka_denkmalschutzobj_*.gpkg", "denkmalschutz"),
        ("ka_bauinventarobj_*.gpkg", "bauinventar"),
        ("dp_kurzinventarobj_*.gpkg", "kurzinventar"),
    ):
        db = sqlite3.connect(_one(path))
        t = _table(db)
        for e, n in db.execute(f'SELECT E_Koord, N_Koord FROM "{t}" WHERE E_Koord IS NOT NULL'):
            items.append(Point(float(e), float(n)))
            tiers.append(tier)

    # The zoning overlay: building-level protection encoded as a polygon.
    db = sqlite3.connect(_one("are_DNPUPolygon_*.gpkg"))
    t = _table(db)
    for shape, label in db.execute(
        f"SELECT SHAPE, KTBez FROM \"{t}\" WHERE KTBez LIKE 'Geb%schutz'"
    ):
        g = _gpkg(shape)
        items.append(g if g.is_valid else g.buffer(0))
        tiers.append("substanzschutz" if "Substanz" in (label or "") else "volumenschutz")

    return STRtree(items), tiers


def load_planning_freezes():
    """Planungszonen — a planning freeze; no permit will be issued.

    Loaded locally rather than left to ÖREB. Of the 38 in Aargau only 20 carry
    `OEREBexport = 'ja'`; the other 18 are invisible to the cadastre, so a parcel
    inside one would come back clean from an ÖREB extract and reach the final
    list looking developable. The dataset is 160 KB — cheaper than the call it
    backs up, and complete where the call is not.
    """
    db = sqlite3.connect(_one("are_Planungszonen_*.gpkg"))
    t = _table(db)
    out = []
    for shape, status in db.execute(f'SELECT SHAPE, Rechtsstatus FROM "{t}"'):
        if (status or "").strip() != "inKraft":
            continue  # only a freeze actually in force blocks anything
        g = _gpkg(shape)
        out.append(g if g.is_valid else g.buffer(0))
    return STRtree(out) if out else None


def in_planning_freeze(tree, geom):
    if tree is None:
        return False
    for i in tree.query(geom):
        if geom.intersection(tree.geometries.take(i)).area > 1.0:
            return True
    return False


def load_design_plans():
    """Areas where a binding design plan may supersede the base-zone AZ."""
    db = sqlite3.connect(_one("are_DNPUPolygon_*.gpkg"))
    t = _table(db)
    out = []
    for (shape,) in db.execute(
        f"SELECT SHAPE FROM \"{t}\" WHERE KTBez LIKE '%Gestaltungspl%'"
    ):
        g = _gpkg(shape)
        out.append(g if g.is_valid else g.buffer(0))
    return STRtree(out) if out else None


def heritage_for(tree, tiers, geom, buffer_m=5.0):
    """Tiers touching a parcel. The buffer absorbs the offset between a
    register's point coordinate and the building it refers to."""
    probe = geom.buffer(buffer_m)
    found = set()
    for i in tree.query(probe):
        if probe.intersects(tree.geometries.take(i)):
            found.add(tiers[i])
    return found


def under_design_plan(tree, geom):
    if tree is None:
        return False
    for i in tree.query(geom):
        if geom.intersection(tree.geometries.take(i)).area > 1.0:
            return True
    return False


def is_recent(period_code, built_after: int, exact_year: str = ""):
    """True when the building is certainly newer than `built_after`.

    Prefers the exact year where GWR has one. Falling back to the period, a
    building is only called recent when the period STARTS after the cutoff —
    a period straddling it stays a candidate rather than being dropped on a
    guess.
    """
    y = (exact_year or "").strip()
    if y.isdigit():
        return int(y) > built_after

    start = GBAUP_PERIOD_START.get((period_code or "").strip())
    return start is not None and start > built_after
