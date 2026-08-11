"""
The seven-step cascade, as a reusable engine.

`run.py` loads every constraint layer on each invocation, which is fine for one
municipality and wasteful for 196. `Engine` loads them once — heritage,
planning freezes, design plans, the zone table and the GWR extract — and then
answers per municipality.

Order follows the brief: hard exclusions first, so a parcel that may not be
demolished is reported as protected rather than as having too little potential.
"""
import csv
import glob
import os
import re
import sqlite3
from datetime import date

from shapely.geometry import Polygon
from shapely.strtree import STRtree

import constraints as C
from shapely import from_wkb


def gpkg_geom(blob):
    """Strip the GeoPackage binary header and hand the WKB to shapely."""
    env = (blob[3] >> 1) & 0x07
    return from_wkb(bytes(blob[8 + {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[env]:]))


def valid(g):
    return g if g.is_valid else g.buffer(0)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
GWR_CSV = os.path.join(DATA, "gwr", "gebaeude_batiment_edificio.csv")
# Addresses are a separate GWR file keyed by EGID; the building extract has none.
ENTRANCE_CSV = os.path.join(DATA, "gwr", "eingang_entree_entrata.csv")
CODES_CSV = os.path.join(DATA, "gwr", "kodes_codes_codici.csv")
TARGET_CLASSES = {"1110", "1121"}


def load_parcels(bfs):
    """Exterior ring minus interior rings — parcels routinely exclude roads and
    carved-out neighbours, and dropping the holes overstates the area."""
    path = os.path.join(DATA, f"parcels_{bfs}.xml")
    xml = open(path, encoding="utf-8", errors="replace").read()
    out = []
    for m in re.finditer(r"<ms:RESF\b.*?</ms:RESF>", xml, re.S):
        f = m.group(0)

        def g(tag):
            mm = re.search(rf"<ms:{tag}>([^<]*)</ms:{tag}>", f)
            return mm.group(1) if mm else None

        def rings(kind):
            res = []
            for blk in re.findall(rf"<gml:{kind}>(.*?)</gml:{kind}>", f, re.S):
                for pl in re.findall(r"<gml:posList[^>]*>([^<]+)</gml:posList>", blk):
                    v = [float(x) for x in pl.split()]
                    r = list(zip(v[0::2], v[1::2]))
                    if len(r) >= 4:
                        res.append(r)
            return res

        ext = rings("exterior")
        if not ext:
            continue
        area = g("Flaeche")
        out.append(
            {
                "nummer": g("Nummer"),
                "egrid": g("EGRIS_EGRID"),
                "area": float(area) if area else None,
                "geom": valid(Polygon(ext[0], rings("interior"))),
            }
        )
    return out


class Engine:
    def __init__(self, built_after=None, min_delta=130.0, min_area=300.0,
                 max_area=5000.0, exclude_inventory=False):
        self.built_after = built_after or (date.today().year - 15)
        self.min_delta, self.min_area, self.max_area = min_delta, min_area, max_area
        self.exclude_inventory = exclude_inventory

        self.heritage, self.tiers = C.load_heritage()
        self.freezes = C.load_planning_freezes()
        self.plans = C.load_design_plans()
        self._zones = self._load_all_zones()
        self._labels = self._load_labels()
        self._addresses = self._load_addresses()
        self._gwr = self._load_gwr()

    @staticmethod
    def _load_labels():
        """GWR ships its own code table; the alternative is hardcoding German
        strings that only the federal office is entitled to change."""
        out = {}
        if not os.path.exists(CODES_CSV):
            return out
        with open(CODES_CSV, newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                out[(row.get("CMERKM"), row.get("CECODID"))] = (row.get("CODTXTLD") or "").strip()
        return out

    @staticmethod
    def _load_addresses():
        """EGID -> street address. A building can carry several entrances; the
        official one (DOFFADR=1) wins, and anything else is only a fallback so a
        parcel still gets an address rather than a blank."""
        official, fallback = {}, {}
        if not os.path.exists(ENTRANCE_CSV):
            return official
        with open(ENTRANCE_CSV, newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                egid = (row.get("EGID") or "").strip()
                street = (row.get("STRNAME") or "").strip()
                if not egid or not street:
                    continue
                nr = (row.get("DEINR") or "").strip()
                plz = (row.get("DPLZ4") or "").strip()
                town = (row.get("DPLZNAME") or "").strip()
                text = " ".join(x for x in (street, nr) if x)
                if plz or town:
                    text += ", " + " ".join(x for x in (plz, town) if x)
                target = official if (row.get("DOFFADR") or "").strip() == "1" else fallback
                target.setdefault(egid, text)
        for egid, text in fallback.items():
            official.setdefault(egid, text)
        return official

    def _load_all_zones(self):
        """Zone polygons keyed by municipality, so a run touches only its own."""
        db = sqlite3.connect(glob.glob(os.path.join(DATA, "are_bzbauzone_*.gpkg"))[0])
        t = [r[0] for r in db.execute("SELECT table_name FROM gpkg_contents")][0]
        by_bfs = {}
        for shape, az, name, gde in db.execute(
            f'SELECT SHAPE, AZmax, GDEBez, GDENR FROM "{t}" WHERE AZmax>0'
        ):
            by_bfs.setdefault(int(gde), []).append(
                {"geom": valid(gpkg_geom(shape)), "az": round(float(az), 2), "name": name}
            )
        return by_bfs

    def _load_gwr(self):
        """One pass over the 52 MB extract; buildings grouped by municipality."""
        by_bfs = {}
        with open(GWR_CSV, newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                if (row.get("GKLAS") or "").strip() not in TARGET_CLASSES:
                    continue
                a, f = (row.get("GAREA") or "").strip(), (row.get("GASTW") or "").strip()
                if not a or not f:
                    continue
                bfs = (row.get("GGDENR") or "").strip()
                if not bfs.isdigit():
                    continue
                by_bfs.setdefault(int(bfs), []).append(
                    {
                        "egrid": (row.get("EGRID") or "").strip(),
                        "egid": (row.get("EGID") or "").strip(),
                        "existing": float(a) * int(f),
                        "footprint": float(a),
                        "klass": (row.get("GKLAS") or "").strip(),
                        "period": (row.get("GBAUP") or "").strip(),
                        "year": (row.get("GBAUJ") or "").strip(),
                    }
                )
        return by_bfs

    def _describe(self, r):
        """The human-facing columns, derived from the buildings on one parcel.

        Age reports the OLDEST building, because that is the one a replacement
        new-build is arguing against; reporting the newest would make a parcel
        with one recent annexe look untouchable. Falls back to the GWR period
        label when no exact year is recorded, which is most of this stock.
        """
        years = sorted(int(y) for y in r["years"] if y.strip().isdigit())
        if years:
            built = str(years[0])
        else:
            periods = sorted(p for p in r["periods"] if p)
            built = self._labels.get(("GBAUP", periods[0]), periods[0]) if periods else ""
            built = built.replace("Periode ", "")

        # Use: the class of the largest building on the parcel.
        biggest = max(r["buildings"], key=lambda b: b["footprint"], default=None)
        use = self._labels.get(("GKLAS", biggest["klass"]), "") if biggest else ""

        address = ""
        for b in sorted(r["buildings"], key=lambda b: -b["footprint"]):
            address = self._addresses.get(b["egid"], "")
            if address:
                break
        return address, built, use

    def run(self, bfs):
        parcels = load_parcels(bfs)
        zones = self._zones.get(bfs, [])
        zone_index = STRtree([z["geom"] for z in zones]) if zones else None
        by_egrid = {p["egrid"]: p for p in parcels if p["egrid"]}

        per_parcel = {}
        for b in self._gwr.get(bfs, []):
            p = by_egrid.get(b["egrid"])
            if not p:
                continue
            r = per_parcel.setdefault(
                p["nummer"],
                {"parcel": p, "existing": 0.0, "n": 0, "periods": [], "years": [],
                 "buildings": []},
            )
            r["existing"] += b["existing"]
            r["n"] += 1
            r["periods"].append(b["period"])
            r["years"].append(b["year"])
            r["buildings"].append(b)

        no_az = 0
        candidates = []
        for num, r in per_parcel.items():
            g = r["parcel"]["geom"]
            area = r["parcel"]["area"] or g.area

            allowance = buildable = 0.0
            dominant = None  # (intersected area, zone) — for the reported zone/AZ
            if zone_index is not None:
                for i in zone_index.query(g):
                    z = zones[i]
                    inter = g.intersection(z["geom"]).area
                    if inter > 1.0:
                        allowance += inter * z["az"]
                        buildable += inter
                        if dominant is None or inter > dominant[0]:
                            dominant = (inter, z)
            if buildable <= 1.0:
                no_az += 1
                continue

            if all(C.is_recent(p, self.built_after, y)
                   for p, y in zip(r["periods"], r["years"])):
                continue

            found = C.heritage_for(self.heritage, self.tiers, g)
            if found & C.HARD:
                continue
            soft = sorted(found - C.HARD)
            if soft and self.exclude_inventory:
                continue

            if C.in_planning_freeze(self.freezes, g):
                continue

            delta = allowance - r["existing"]
            if delta < self.min_delta or not (self.min_area <= area <= self.max_area):
                continue

            address, built, use = self._describe(r)
            # The AGIS map link works by simulated click: info=E,N pops the
            # parcel card at that LV95 coordinate (format from Philipp's own
            # browser, 2026-08-11 — there is no EGRID parameter). The
            # representative point is guaranteed inside the polygon, where a
            # centroid of an L-shaped parcel can land on the neighbour.
            pt = g.representative_point()
            candidates.append(
                {
                    "parcel": num,
                    "egrid": r["parcel"]["egrid"],
                    "east": round(pt.x, 2),
                    "north": round(pt.y, 2),
                    "address": address,
                    "built": built,
                    "use": use,
                    # The zone covering most of the parcel, and the AZ actually
                    # applied — area-weighted, so a parcel straddling two zones
                    # reports the figure the potential was computed from rather
                    # than one of the two headline values.
                    "zone": dominant[1]["name"] if dominant else "",
                    "az": round(allowance / buildable, 3) if buildable else 0.0,
                    "area": area,
                    "buildable": buildable,
                    "share": buildable / area if area else 0.0,
                    "n": r["n"],
                    "existing": r["existing"],
                    "delta": delta,
                    "heritage": ",".join(soft),
                    "design_plan": C.under_design_plan(self.plans, g),
                }
            )

        candidates.sort(key=lambda r: r["delta"] / max(r["existing"], 1), reverse=True)
        return {
            "parcels": len(parcels),
            "assessed": len(per_parcel),
            "no_az": no_az,
            "candidates": candidates,
        }
