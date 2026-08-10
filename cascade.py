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
        self._gwr = self._load_gwr()

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
                        "existing": float(a) * int(f),
                        "period": (row.get("GBAUP") or "").strip(),
                        "year": (row.get("GBAUJ") or "").strip(),
                    }
                )
        return by_bfs

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
                p["nummer"], {"parcel": p, "existing": 0.0, "n": 0, "periods": [], "years": []}
            )
            r["existing"] += b["existing"]
            r["n"] += 1
            r["periods"].append(b["period"])
            r["years"].append(b["year"])

        no_az = 0
        candidates = []
        for num, r in per_parcel.items():
            g = r["parcel"]["geom"]
            area = r["parcel"]["area"] or g.area

            allowance = buildable = 0.0
            if zone_index is not None:
                for i in zone_index.query(g):
                    z = zones[i]
                    inter = g.intersection(z["geom"]).area
                    if inter > 1.0:
                        allowance += inter * z["az"]
                        buildable += inter
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

            candidates.append(
                {
                    "parcel": num,
                    "egrid": r["parcel"]["egrid"],
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
